"""UDP transport: one datagram socket, with our own reliability layer on top.

UDP gives us datagrams that may be lost, duplicated or reordered, and nothing
else. Everything TCP did for free is done here, but only where it is wanted.

Every datagram is  25-byte header | payload (one JSON message, or nothing):

    magic  2  b"DR"      anything else is not ours
    kind   1  0 = ack/heartbeat (no payload), 1 = unreliable, 2 = reliable
    pkt    2  number of this datagram; +1 for every datagram sent to this peer
    seq    2  number of this reliable message (kind 2 only)
    ack    2  the next reliable seq I expect from you: everything before it arrived
    token  16 session token from LOGIN_OK, all zero before login. Client
              messages leave the token out of their JSON; the server side of
              this transport puts the authenticated one back before delivery.

Reliable messages: kept until acked and resent on a timer. The timer (RTO) is
derived from measured round-trip times as in RFC 6298 and doubles after every
resend of the same message. A message unacked for 2 s means the peer is gone,
the same bound as for silence. The receiver delivers reliable messages in seq
order, parking any that arrive early, and drops repeats. Acks ride on every datagram going the other way; if
nothing is going that way, an ack-only datagram is sent at once.

Unreliable messages: sent once. Late or lost ones are never repaired.

Identity on the server is the token, never the source address. A datagram with
a valid token from a new address moves the peer to that address (NAT rebinding).
Datagrams that are malformed or carry no valid token are counted and dropped;
they can never disconnect anyone.

All counters are 16 bits and wrap around; seq_diff() compares them correctly.
The largest datagram is 25 + MAX_PAYLOAD_BYTES = 1125 bytes, under the 1200 cap.
"""

import logging
import select
import socket
import struct
import time
from collections import Counter, deque

from common.constants import HEARTBEAT_INTERVAL_MS, MAX_DATAGRAM_BYTES, PEER_TIMEOUT_MS
from common.protocol import BOOTSTRAP_TYPE, TOKEN_IN_HEADER, ProtocolError, decode, encode
from transport.base import Transport

log = logging.getLogger("transport.udp")

HEADER = struct.Struct("!2sBHHH16s")
MAGIC = b"DR"
KIND_ACK, KIND_UNRELIABLE, KIND_RELIABLE = 0, 1, 2
NO_TOKEN = bytes(16)

MIN_RTO_MS = 100
MAX_RTO_MS = 500
INITIAL_RTO_MS = 200         # used until the first round-trip time is measured
MAX_UNACKED = 1024           # reliable messages in flight per peer; also the receive window
MAX_DATAGRAMS_PER_RECV = 512  # so a flood cannot keep recv() from returning

# Limits on traffic from sources that have not proved who they are.
UNAUTHENTICATED_PER_IP_PER_SECOND = 30
MAX_PENDING_PEERS = 64       # peers that have sent LOGIN but hold no token yet
AMPLIFICATION_FACTOR = 3     # bytes we may send per byte received, until the address is proven

# preflight.py --public-test sends this (plus a few random bytes) and we echo it.
PROBE_MAGIC = b"DRAGRACE-PROBE-1"
PROBE_MAX_BYTES = 32


def seq_diff(a, b):
    """Signed distance a - b between two 16-bit counters, correct across wraparound:
    seq_diff(2, 65534) is 4, not -65532."""
    return ((a - b + 0x8000) & 0xFFFF) - 0x8000


class _Pending:
    """A reliable message that has not been acked yet."""

    def __init__(self, payload):
        self.payload = payload
        self.first_sent = time.monotonic()
        self.last_sent = self.first_sent
        self.attempts = 0  # transmissions so far


class _Peer:
    def __init__(self, address, proven, first_seq):
        now = time.monotonic()
        self.address = address
        self.token = NO_TOKEN
        # Server side: has this address shown it receives what we send it?
        # Until then our replies are capped by send_budget (bytes).
        self.proven = proven
        self.send_budget = 0
        # sending
        self.next_pkt = first_seq
        self.next_seq = first_seq
        self.unacked = {}          # seq -> _Pending, oldest first
        self.ack_owed = False      # received something reliable, not yet acked
        self.last_send = now
        self.srtt_ms = None        # smoothed round-trip time; None until the first sample
        self.rttvar_ms = 0.0       # how much the round-trip time varies
        self.rto_ms = INITIAL_RTO_MS  # how long to wait for an ack before resending
        # receiving
        self.expected_seq = first_seq
        self.early = {}            # seq -> message that arrived before its turn
        self.recent_pkts = deque(maxlen=64)  # for spotting duplicated datagrams
        self.newest_pkt = None
        self.pkts_expected = 0     # how many datagrams the peer has sent, judged by pkt numbers
        self.last_recv = now       # every datagram carries an ack, so this is also the last ack


class UdpTransport(Transport):
    # First value of the pkt and seq counters. Both ends must agree. Tests raise
    # it to just below 65536 to exercise wraparound.
    INITIAL_SEQ = 0

    def __init__(self, sock, is_server):
        super().__init__()
        sock.setblocking(False)
        self._sock = sock
        self._is_server = is_server
        self._peers = {}       # peer id -> _Peer
        self._by_address = {}  # (ip, port) -> peer id
        self._by_token = {}    # token bytes -> peer id (server only)
        self._next_peer = 1
        self._window = 0       # the one-second window the two limits below belong to
        self._unauthenticated_seen = Counter()  # source ip -> datagrams this second
        self._probes_answered = set()           # source ips answered this second
        self.server_peer = None                 # client side: the peer to send to

    @classmethod
    def listen(cls, host, port):
        """Server side. Port 0 picks a free port; read it back from .address."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((host, port))
        return cls(sock, is_server=True)

    @classmethod
    def connect(cls, host, port):
        """Client side. UDP has no connection: this only picks the destination.
        An unreachable server shows up later as an 'unreachable' disconnect."""
        address = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", 0))  # Windows refuses to receive on a socket with no local port
        transport = cls(sock, is_server=False)
        transport.server_peer = transport._add_peer(address, proven=True)
        return transport

    @property
    def address(self):
        return self._sock.getsockname()

    def _add_peer(self, address, proven):
        peer_id = self._next_peer
        self._next_peer += 1
        self._peers[peer_id] = _Peer(address, proven, self.INITIAL_SEQ)
        self._by_address[address] = peer_id
        return peer_id

    def authenticate(self, peer_id, token):
        """From now on datagrams to this peer carry the token. On the server,
        datagrams carrying it are this peer, whatever address they come from."""
        peer = self._peers.get(peer_id)
        if peer is None:
            return
        peer.token = bytes.fromhex(token)
        if self._is_server:
            self._by_token[peer.token] = peer_id

    def rtt_sample(self, peer_id, rtt_ms):
        """RFC 6298: keep a smoothed RTT and its variation; wait srtt + 4 * variation
        for an ack. A jittery link gets a longer timer, a steady one a tight timer."""
        peer = self._peers.get(peer_id)
        if peer is None:
            return
        if peer.srtt_ms is None:
            peer.srtt_ms = rtt_ms
            peer.rttvar_ms = rtt_ms / 2
        else:
            peer.rttvar_ms = 0.75 * peer.rttvar_ms + 0.25 * abs(peer.srtt_ms - rtt_ms)
            peer.srtt_ms = 0.875 * peer.srtt_ms + 0.125 * rtt_ms
        peer.rto_ms = min(max(peer.srtt_ms + 4 * peer.rttvar_ms, MIN_RTO_MS), MAX_RTO_MS)

    # --- sending -------------------------------------------------------------

    @staticmethod
    def _encode(message):
        # The token already travels in the header, so sending it in the JSON too
        # would spend about 40 bytes per message on nothing.
        return encode(message, omit_token=message["type"] in TOKEN_IN_HEADER)

    def send_reliable(self, peer_id, message):
        peer = self._peers.get(peer_id)
        if peer is None:
            self._totals["sends_to_lost_peer"] += 1
            return
        pending = _Pending(self._encode(message))
        seq = peer.next_seq
        peer.next_seq = (seq + 1) & 0xFFFF
        peer.unacked[seq] = pending
        if len(peer.unacked) > MAX_UNACKED:
            self._drop(peer_id, "send_buffer_full")
            return
        self._count(peer_id, "messages_sent")
        self._count(peer_id, "payload_bytes_sent", len(pending.payload))
        self._send_reliable_datagram(peer_id, peer, seq, pending)

    def _send_reliable_datagram(self, peer_id, peer, seq, pending):
        pending.last_sent = time.monotonic()
        if self._send_datagram(peer_id, peer, KIND_RELIABLE, seq, pending.payload):
            pending.attempts += 1
            if pending.attempts > 1:
                self._count(peer_id, "retransmits")

    def send_unreliable(self, peer_id, message):
        peer = self._peers.get(peer_id)
        if peer is None:
            self._totals["sends_to_lost_peer"] += 1
            return
        payload = self._encode(message)
        self._count(peer_id, "messages_sent")
        self._count(peer_id, "payload_bytes_sent", len(payload))
        self._send_datagram(peer_id, peer, KIND_UNRELIABLE, 0, payload)

    def _send_datagram(self, peer_id, peer, kind, seq, payload):
        """Build and send one datagram. Returns False if it was held back."""
        datagram = HEADER.pack(MAGIC, kind, peer.next_pkt, seq, peer.expected_seq, peer.token) + payload
        assert len(datagram) <= MAX_DATAGRAM_BYTES
        if not peer.proven:
            # Anti-amplification, the rule QUIC uses: a source address can be forged,
            # so until this one is proven we send it at most 3x what it sent us.
            if len(datagram) > peer.send_budget:
                self._totals["amplification_limited"] += 1
                return False
            peer.send_budget -= len(datagram)
        peer.next_pkt = (peer.next_pkt + 1) & 0xFFFF
        peer.last_send = time.monotonic()
        peer.ack_owed = False  # every datagram carries the current ack
        self._count(peer_id, "datagrams_sent")
        self._count(peer_id, "wire_bytes_sent", len(datagram))
        self._transmit(datagram, peer.address)
        return True

    def _transmit(self, datagram, address):
        """The one place bytes leave this process. Tests wrap it to lose,
        duplicate and reorder datagrams; the impairment flags will too."""
        try:
            self._sock.sendto(datagram, address)
        except OSError as exc:
            # Send buffer full, or the network just went away. Either way the
            # datagram is lost, which the layer above already copes with.
            self._totals["send_errors"] += 1
            if self._totals["send_errors"] == 1:
                log.warning("sendto failed (further failures are only counted): %s", exc)

    # --- receiving -----------------------------------------------------------

    def recv(self, timeout=0.0):
        self._report_lost()
        messages = []
        readable, _, _ = select.select([self._sock], [], [], timeout)
        if readable:
            for _ in range(MAX_DATAGRAMS_PER_RECV):
                try:
                    data, address = self._sock.recvfrom(2048)
                except BlockingIOError:
                    break  # nothing more waiting
                except OSError as exc:
                    # Mostly Windows reporting an ICMP "port unreachable" for a
                    # datagram we sent earlier to a peer that has since exited.
                    # That peer will time out; the socket itself is fine.
                    self._totals["recv_errors"] += 1
                    log.debug("recvfrom failed: %s", exc)
                    continue
                self._on_datagram(data, address, messages)
        self._run_timers()
        return messages

    def _on_datagram(self, data, address, messages):
        parsed = self._parse(data)  # None if malformed
        if self._is_server:
            peer_id = self._identify_on_server(data, parsed, address)
        else:
            peer_id = self._identify_on_client(parsed, address)
        if peer_id is None:
            return
        peer = self._peers[peer_id]
        kind, pkt, seq, ack, _, message = parsed

        if pkt in peer.recent_pkts:
            self._count(peer_id, "duplicates_dropped")
            return
        peer.recent_pkts.append(pkt)
        if peer.newest_pkt is None:
            peer.pkts_expected = 1
            peer.newest_pkt = pkt
        elif seq_diff(pkt, peer.newest_pkt) > 0:
            peer.pkts_expected += seq_diff(pkt, peer.newest_pkt)
            peer.newest_pkt = pkt
        now = time.monotonic()
        peer.last_recv = now
        self._count(peer_id, "datagrams_received")
        self._count(peer_id, "wire_bytes_received", len(data))
        if not peer.proven:
            peer.send_budget += AMPLIFICATION_FACTOR * len(data)

        # The peer expects `ack` next, so everything before it has arrived.
        for acked in [s for s in peer.unacked if seq_diff(s, ack) < 0]:
            del peer.unacked[acked]

        if kind == KIND_UNRELIABLE:
            self._deliver(peer_id, message, len(data), messages)
        elif kind == KIND_RELIABLE:
            peer.ack_owed = True  # even for a repeat: our earlier ack may have been lost
            distance = seq_diff(seq, peer.expected_seq)
            if distance < 0 or seq in peer.early:
                self._count(peer_id, "duplicates_dropped")
            elif distance >= MAX_UNACKED:
                self._count(peer_id, "dropped_outside_window")
            else:
                peer.early[seq] = (message, len(data))
                # Deliver every message whose turn has come, in order.
                while peer.expected_seq in peer.early:
                    ready, size = peer.early.pop(peer.expected_seq)
                    peer.expected_seq = (peer.expected_seq + 1) & 0xFFFF
                    self._deliver(peer_id, ready, size, messages)

    def _deliver(self, peer_id, message, datagram_size, messages):
        self._count(peer_id, "messages_received")
        self._count(peer_id, "payload_bytes_received", datagram_size - HEADER.size)
        messages.append((peer_id, message))

    def _parse(self, data):
        """Returns (kind, pkt, seq, ack, token, message), or None if the datagram
        is malformed. message is None for an ack-only datagram."""
        if not HEADER.size <= len(data) <= MAX_DATAGRAM_BYTES:
            return None
        magic, kind, pkt, seq, ack, token = HEADER.unpack_from(data)
        if magic != MAGIC or kind not in (KIND_ACK, KIND_UNRELIABLE, KIND_RELIABLE):
            return None
        if (kind == KIND_ACK) != (len(data) == HEADER.size):
            return None  # an ack has no payload; the other kinds must have one
        try:
            # On the server the header token (authenticated below, or all zero) becomes
            # the message's token. "" marks a sender that has not logged in.
            claimed = None if not self._is_server else "" if token == NO_TOKEN else token.hex()
            message = None if kind == KIND_ACK else decode(data[HEADER.size:], claimed)
        except ProtocolError as exc:
            log.debug("undecodable payload: %s", exc)
            return None
        return kind, pkt, seq, ack, token, message

    def _identify_on_client(self, parsed, address):
        """Client: accept only well-formed datagrams from the server's address."""
        peer = self._peers.get(self.server_peer)
        if peer is None or address != peer.address:
            self._totals["dropped_unauthenticated"] += 1
            return None
        if parsed is None:
            self._totals["dropped_malformed"] += 1
            return None
        return self.server_peer

    def _identify_on_server(self, data, parsed, address):
        """Server: work out which peer sent this, or return None to drop it."""
        now = time.monotonic()
        if int(now) != self._window:  # a new second: reset the per-second limits
            self._window = int(now)
            self._unauthenticated_seen.clear()
            self._probes_answered.clear()
        if data.startswith(PROBE_MAGIC):
            self._answer_probe(data, address)
            return None

        token = parsed[4] if parsed else None
        peer_id = self._by_token.get(token)
        if peer_id is not None:
            # Authenticated by token. The source address does not matter.
            peer = self._peers[peer_id]
            peer.proven = True  # it could only know the token by receiving LOGIN_OK
            if peer.address != address:
                self._rebind(peer_id, peer, parsed[1], address)
            return peer_id

        # From here on the sender is unauthenticated, so it is rate limited by source IP.
        self._unauthenticated_seen[address[0]] += 1
        if self._unauthenticated_seen[address[0]] > UNAUTHENTICATED_PER_IP_PER_SECOND:
            self._totals["dropped_rate_limited"] += 1
            return None
        if parsed is None:
            self._totals["dropped_malformed"] += 1
            return None
        kind, _, seq, _, _, message = parsed
        peer_id = self._by_address.get(address)
        if token == NO_TOKEN and peer_id is not None and not self._peers[peer_id].proven:
            # A client that has sent LOGIN and cannot know its token yet: either
            # LOGIN_OK is not out, or it is still in flight (or was lost).
            return peer_id
        # LOGIN is the bootstrap case: the only thing a stranger can send that
        # creates state here. The new peer is unproven, so replies to it are
        # capped at 3x what it sends, and it times out like any other peer.
        pending = sum(1 for p in self._peers.values() if p.token == NO_TOKEN)
        if (token == NO_TOKEN and peer_id is None and kind == KIND_RELIABLE and seq == self.INITIAL_SEQ
                and message["type"] == BOOTSTRAP_TYPE and pending < MAX_PENDING_PEERS):
            log.info("new peer from %s:%s", *address)
            return self._add_peer(address, proven=False)
        self._totals["dropped_unauthenticated"] += 1
        return None

    def _rebind(self, peer_id, peer, pkt, address):
        """An authenticated datagram arrived from a new address: the client's NAT
        gave it a new mapping, or it changed network. Follow it."""
        if peer.newest_pkt is not None and seq_diff(pkt, peer.newest_pkt) <= 0:
            return  # an old datagram, possibly a replay from elsewhere: not a reason to move
        log.info("peer %s rebound from %s:%s to %s:%s", peer_id, *peer.address, *address)
        self._totals["rebinds"] += 1
        stale = self._by_address.pop(address, None)  # an unproven peer squatting on the new address
        if stale is not None and stale != peer_id:
            self._remove(stale)
        if self._by_address.get(peer.address) == peer_id:
            del self._by_address[peer.address]
        peer.address = address
        self._by_address[address] = peer_id

    def _answer_probe(self, data, address):
        """Reachability check for preflight.py. The reply is the request echoed,
        so it is never larger than the request, and each source IP gets one
        reply per second: useless for amplification or flooding a third party."""
        if len(data) > PROBE_MAX_BYTES or address[0] in self._probes_answered:
            self._totals["probes_ignored"] += 1
            return
        self._probes_answered.add(address[0])
        self._totals["probes_answered"] += 1
        self._transmit(data, address)

    # --- timers --------------------------------------------------------------

    def _run_timers(self):
        now = time.monotonic()
        for peer_id, peer in list(self._peers.items()):
            if (now - peer.last_recv) * 1000 > PEER_TIMEOUT_MS:
                self._drop(peer_id, "timeout")
            elif not self._retransmit(peer_id, peer, now):
                self._drop(peer_id, "unreachable")
            elif peer.ack_owed or (now - peer.last_send) * 1000 >= HEARTBEAT_INTERVAL_MS:
                # Nothing else went out to carry the ack, or the line has been
                # quiet for 250 ms. Heartbeats prove we are alive and keep the
                # client's NAT mapping open.
                if not self._send_datagram(peer_id, peer, KIND_ACK, 0, b""):
                    # Held back by the amplification cap. Wait a heartbeat interval
                    # instead of retrying on every call.
                    peer.ack_owed = False
                    peer.last_send = now

    def _retransmit(self, peer_id, peer, now):
        """Resend reliable messages whose ack is overdue.
        Returns False if one has gone unacked for the whole peer timeout."""
        for seq, pending in peer.unacked.items():
            if (now - pending.first_sent) * 1000 >= PEER_TIMEOUT_MS:
                return False
            # Wait one RTO before the first resend, then twice as long before each
            # further one (so a struggling link is not flooded), up to the cap.
            wait_ms = min(peer.rto_ms * 2 ** max(0, pending.attempts - 1), MAX_RTO_MS)
            if (now - pending.last_sent) * 1000 >= wait_ms:
                self._send_reliable_datagram(peer_id, peer, seq, pending)
        return True

    # --- stats and closing ---------------------------------------------------

    def stats(self, peer=None):
        result = super().stats(peer)
        state = self._peers.get(peer)
        if state is not None:
            result["datagrams_lost"] = max(0, state.pkts_expected - result.get("datagrams_received", 0))
            result["unacked_messages"] = len(state.unacked)
            result["ms_since_last_ack"] = int((time.monotonic() - state.last_recv) * 1000)
            result["rto_ms"] = round(state.rto_ms, 1)
            result["srtt_ms"] = round(state.srtt_ms or 0.0, 1)
            result["rttvar_ms"] = round(state.rttvar_ms, 1)
        return result

    def _remove(self, peer_id):
        peer = self._peers.pop(peer_id)
        self._per_peer.pop(peer_id, None)
        if self._by_address.get(peer.address) == peer_id:
            del self._by_address[peer.address]
        if self._by_token.get(peer.token) == peer_id:
            del self._by_token[peer.token]

    def _drop(self, peer_id, reason):
        """The peer is gone and the owner did not ask for it: report it."""
        self._remove(peer_id)
        self._lost.append((peer_id, reason))

    def close_peer(self, peer_id):
        if peer_id in self._peers:
            self._remove(peer_id)

    def close(self):
        # We cannot wait for acks while shutting down, so anything still unacked
        # (typically BYE) goes out twice more. The receiver drops the repeats.
        for peer_id, peer in self._peers.items():
            for seq, pending in peer.unacked.items():
                for _ in range(2):
                    self._send_datagram(peer_id, peer, KIND_RELIABLE, seq, pending.payload)
        self._peers.clear()
        self._sock.close()
