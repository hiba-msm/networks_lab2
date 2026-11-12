"""TCP transport: one stream socket per peer, non-blocking, multiplexed with select.

TCP delivers a byte stream, not messages. One send() on the far side can arrive
split over several recv() calls, and several sends can arrive glued together in
one. So every message is framed as

    4-byte big-endian payload length | payload (JSON)

and bytes are collected in a per-connection buffer until a whole frame is there.

Reliability and ordering are TCP's job, so send_reliable and send_unreliable do
the same thing. Stats count bytes handed to and read from the socket; the
TCP/IP headers added by the kernel are not visible from here.
"""

import logging
import os
import selectors
import socket
import struct
import time

from common.constants import MAX_PAYLOAD_BYTES, PEER_TIMEOUT_MS
from common.protocol import ProtocolError, decode, encode
from transport.base import Transport

log = logging.getLogger("transport.tcp")

FRAME_HEADER = struct.Struct(">I")
# A peer that stops reading makes our send buffer grow. Past this it is dropped.
MAX_SEND_BUFFER_BYTES = 1 << 20


class _Connection:
    def __init__(self, sock, address):
        self.sock = sock
        self.address = address
        self.inbuf = bytearray()   # received bytes not yet forming a whole frame
        self.outbuf = bytearray()  # frames the kernel has not accepted yet
        self.want_write = False    # registered for "socket writable" events?
        self.last_recv = time.monotonic()


class TcpTransport(Transport):
    def __init__(self):
        super().__init__()
        self._selector = selectors.DefaultSelector()
        self._listener = None
        self._connections = {}  # peer -> _Connection
        self._next_peer = 1
        self.server_peer = None  # client side: the peer to send to

    @classmethod
    def listen(cls, host, port):
        """Server side. Port 0 picks a free port; read it back from .address."""
        transport = cls()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":
            # Lets the server restart at once on Linux/macOS. On Windows the same
            # option would let a second process steal the port, so it stays off.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen()
        sock.setblocking(False)
        transport._selector.register(sock, selectors.EVENT_READ, None)
        transport._listener = sock
        return transport

    @classmethod
    def connect(cls, host, port, timeout=5.0):
        """Client side. Blocks until connected, then the socket is non-blocking."""
        transport = cls()
        sock = socket.create_connection((host, port), timeout)
        transport.server_peer = transport._add_connection(sock, sock.getpeername())
        return transport

    @property
    def address(self):
        return self._listener.getsockname()

    def _add_connection(self, sock, address):
        sock.setblocking(False)
        # Nagle's algorithm would hold small messages back to merge them. A 30 Hz
        # game wants each one on the wire immediately.
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        peer = self._next_peer
        self._next_peer += 1
        self._connections[peer] = _Connection(sock, address)
        self._selector.register(sock, selectors.EVENT_READ, peer)
        return peer

    # --- sending -------------------------------------------------------------

    def authenticate(self, peer, token):
        pass  # a TCP connection already is the peer's identity; the server checks the token

    def send_reliable(self, peer, message):
        self._send(peer, message)

    def send_unreliable(self, peer, message):
        self._send(peer, message)

    def _send(self, peer, message):
        conn = self._connections.get(peer)
        if conn is None:
            # Normal race: the peer dropped and the owner has not been told yet.
            self._totals["sends_to_lost_peer"] += 1
            return
        payload = encode(message)
        conn.outbuf += FRAME_HEADER.pack(len(payload)) + payload
        self._count(peer, "messages_sent")
        self._count(peer, "payload_bytes_sent", len(payload))
        self._count(peer, "wire_bytes_sent", FRAME_HEADER.size + len(payload))
        if len(conn.outbuf) > MAX_SEND_BUFFER_BYTES:
            self._drop(peer, "send_buffer_full")
            return
        self._flush(peer)

    def _flush(self, peer):
        conn = self._connections[peer]
        try:
            sent = conn.sock.send(conn.outbuf)
        except BlockingIOError:
            sent = 0  # kernel buffer full; try again when the socket is writable
        except OSError as exc:
            log.info("peer %s: send failed: %s", peer, exc)
            self._drop(peer, "reset")
            return
        del conn.outbuf[:sent]
        want_write = bool(conn.outbuf)
        if want_write != conn.want_write:
            conn.want_write = want_write
            events = selectors.EVENT_READ | (selectors.EVENT_WRITE if want_write else 0)
            self._selector.modify(conn.sock, events, peer)

    # --- receiving -----------------------------------------------------------

    def recv(self, timeout=0.0):
        self._report_lost()
        messages = []
        if self._selector.get_map():
            events = self._selector.select(timeout)
        else:
            time.sleep(timeout)  # select() with no sockets is an error on Windows
            events = []
        for key, mask in events:
            peer = key.data
            if peer is None:
                self._accept()
                continue
            if mask & selectors.EVENT_READ and peer in self._connections:
                self._read(peer, messages)
            if mask & selectors.EVENT_WRITE and peer in self._connections:
                self._flush(peer)

        # A killed process closes its socket and we see it at once. A pulled cable
        # or a frozen machine sends nothing, so silence itself has to be the signal.
        now = time.monotonic()
        for peer, conn in list(self._connections.items()):
            if (now - conn.last_recv) * 1000 > PEER_TIMEOUT_MS:
                self._drop(peer, "timeout")
        return messages

    def _accept(self):
        while True:
            try:
                sock, address = self._listener.accept()
            except BlockingIOError:
                return  # no more pending connections
            except OSError as exc:
                log.warning("accept failed: %s", exc)
                return
            peer = self._add_connection(sock, address)
            log.info("peer %s connected from %s:%s", peer, *address[:2])

    def _read(self, peer, messages):
        conn = self._connections[peer]
        try:
            data = conn.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError as exc:
            log.info("peer %s: recv failed: %s", peer, exc)
            self._drop(peer, "reset")
            return
        if not data:  # the other side closed the connection
            self._drop(peer, "closed")
            return
        conn.last_recv = time.monotonic()
        conn.inbuf += data
        self._count(peer, "wire_bytes_received", len(data))

        # Take out every complete frame; leave a partial one for the next recv().
        while len(conn.inbuf) >= FRAME_HEADER.size:
            (length,) = FRAME_HEADER.unpack_from(conn.inbuf)
            if length == 0 or length > MAX_PAYLOAD_BYTES:
                log.warning("peer %s: bad frame length %d", peer, length)
                self._drop(peer, "protocol_error")
                return
            end = FRAME_HEADER.size + length
            if len(conn.inbuf) < end:
                break
            payload = bytes(conn.inbuf[FRAME_HEADER.size:end])
            del conn.inbuf[:end]
            try:
                message = decode(payload)
            except ProtocolError as exc:
                log.warning("peer %s: %s", peer, exc)
                self._drop(peer, "protocol_error")
                return
            self._count(peer, "messages_received")
            self._count(peer, "payload_bytes_received", len(payload))
            messages.append((peer, message))

    # --- closing -------------------------------------------------------------

    def _remove(self, peer):
        conn = self._connections.pop(peer)
        self._per_peer.pop(peer, None)
        self._selector.unregister(conn.sock)
        conn.sock.close()

    def _drop(self, peer, reason):
        """The peer is gone and the owner did not ask for it: report it."""
        self._remove(peer)
        self._lost.append((peer, reason))

    def close_peer(self, peer):
        if peer in self._connections:
            self._remove(peer)

    def close(self):
        for peer in list(self._connections):
            self._remove(peer)
        if self._listener is not None:
            self._selector.unregister(self._listener)
            self._listener.close()
        self._selector.close()
