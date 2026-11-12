"""End-to-end tests over real sockets on 127.0.0.1. Nothing is mocked.

Run with:  python -m tests.test_server            (everything)
           python -m tests.test_server udp_       (only tests whose name contains udp_)

Every test named test_* runs once per transport in TRANSPORTS against a fresh
server on a free port. test_tcp_* and test_udp_* run for that transport only:
they poke at framing and at the reliability layer with raw sockets, or by
wrapping the transport's send to lose, duplicate and reorder datagrams.
"""

import contextlib
import os
import pathlib
import random
import re
import socket
import struct
import sys
import threading
import time

import preflight
from common import protocol
from common.protocol import decode, encode
from server.server import GameServer
from transport import udp_transport
from transport.tcp_transport import TcpTransport
from transport.udp_transport import UdpTransport

TRANSPORTS = {"tcp": TcpTransport, "udp": UdpTransport}
KIND = "tcp"    # transport under test; set by the runner
SERVER = None   # the running server's transport, for reading its counters
CLIENTS = []    # every live test client; they are all pumped together


@contextlib.contextmanager
def running_server():
    global SERVER
    SERVER = TRANSPORTS[KIND].listen("127.0.0.1", 0)
    # short track, short pauses and a short name reservation, so tests take seconds
    server = GameServer(SERVER, track_length=30.0, countdown_ms=300, intermission_ms=200, session_keep_ms=1000)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        yield SERVER.address[1]
        assert thread.is_alive(), "the server crashed during the test"
    finally:
        for client in list(CLIENTS):
            client.kill()
        server.running = False
        thread.join(timeout=2)
        SERVER.close()


def pump_all():
    for client in CLIENTS:
        if not client.frozen:
            client.pump()
    time.sleep(0.005)


def wait(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pump_all()


def settle():
    """Let in-flight messages land, then forget them, so a later expect()
    cannot match something left over from the setup."""
    wait(0.1)
    for client in CLIENTS:
        client.inbox.clear()


class Client:
    """A bare protocol client for tests: sends what it is told, collects what arrives."""

    def __init__(self, port, name=None, token=""):
        self.transport = TRANSPORTS[KIND].connect("127.0.0.1", port)
        self.name = name
        self.token = ""
        self.inbox = []
        self.lost = None       # disconnect reason, once the server is gone
        self.frozen = False    # True simulates a hung machine: socket open, no traffic
        self.last_ping = 0.0
        self.transport.on_disconnect(lambda peer, reason: setattr(self, "lost", reason))
        CLIENTS.append(self)
        if name is not None:
            self.login(name, token)

    def login(self, name, token=""):
        """Log in and do what every real client does with LOGIN_OK."""
        self.send("LOGIN", name=name, token=token)
        ok = self.expect("LOGIN_OK")
        self.name, self.player_id, self.token = name, ok["player_id"], ok["token"]
        self.transport.authenticate(self.transport.server_peer, self.token)

    def send(self, msg_type, **fields):
        fields.setdefault("token", self.token)
        self.transport.send(self.transport.server_peer, {"type": msg_type, **fields})

    def pump(self):
        now = time.monotonic()
        if now - self.last_ping > 0.2:  # the heartbeat every real client sends
            self.send("PING", t=int(now * 1000), rtt_ms=0)
            self.last_ping = now
        self.inbox += [m for _, m in self.transport.recv(0) if m["type"] != "PONG"]

    def find(self, types, where=lambda m: True):
        types = (types,) if isinstance(types, str) else types
        return next((m for m in self.inbox if m["type"] in types and where(m)), None)

    def expect(self, types, where=lambda m: True, timeout=5.0):
        """Wait for a message of one of the given types, remove it from the inbox, return it."""
        deadline = time.monotonic() + timeout
        while True:
            message = self.find(types, where)
            if message is not None:
                self.inbox.remove(message)
                return message
            if time.monotonic() > deadline:
                seen = [m["type"] for m in self.inbox if m["type"] != "SNAPSHOT"]
                raise AssertionError(f"{self.name}: no {types} within {timeout}s; inbox has {seen}; lost={self.lost}")
            pump_all()

    def expect_error(self, code):
        error = self.expect("ERROR")
        assert error["code"] == code, error

    def expect_room(self, player_count):
        return self.expect("ROOM_STATE", lambda m: len(m["players"]) == player_count)

    def kill(self):
        """Vanish without saying BYE, like a crashed process."""
        self.transport.close()
        CLIENTS.remove(self)


def make_room(port, names):
    """Log everyone in and put them in one room. Returns (clients, code)."""
    clients = [Client(port, name) for name in names]
    clients[0].send("CREATE_ROOM", room_name="test room")
    code = clients[0].expect_room(1)["code"]
    for client in clients[1:]:
        client.send("JOIN_ROOM", code=code)
    for client in clients[1:] and clients:  # a lone creator has already seen its room
        client.expect_room(len(clients))
    settle()
    return clients, code


def ready_up(clients):
    """Everyone readies. Three start by themselves; two need the creator's START_MATCH."""
    for client in clients:
        client.send("READY", ready=True)
    if len(clients) < 3:
        clients[0].expect("ROOM_STATE", lambda m: all(p["ready"] for p in m["players"]))
        clients[0].send("START_MATCH")


def play_round(clients, rates, round_number, kill=None, midway=None):
    """Drive one round: each client presses at rates[client] presses/second.
    kill=(client, seconds) makes that client vanish that long after its start.
    midway=(seconds, function) calls the function once, that long after the start.
    Returns the ROUND_END each surviving client received."""
    starts = [c.expect("MATCH_START", lambda m: m["round_number"] == round_number) for c in clients]
    assert len({m["start_at_server_time_ms"] for m in starts}) == 1, "clients disagree on the start time"
    clients = list(clients)
    started = {}  # client -> local time of its first snapshot of this round
    deadline = time.monotonic() + 20
    is_end = lambda m: m["round_number"] == round_number
    while not all(c.find(("ROUND_END", "MATCH_END"), lambda m: m["type"] == "MATCH_END" or is_end(m)) for c in clients):
        assert time.monotonic() < deadline, "round never ended"
        pump_all()
        now = time.monotonic()
        for c in clients:
            if c not in started and c.find("SNAPSHOT", is_end):
                started[c] = now
            if c in started:
                c.send("INPUT", round_number=round_number, tick=0, client_time_ms=int(now * 1000),
                       cumulative_presses=int(rates[c] * (now - started[c])))
        if kill is not None and kill[0] in started and now - started[kill[0]] > kill[1]:
            kill[0].kill()
            clients.remove(kill[0])
            kill = None
        if midway is not None and started and now - min(started.values()) > midway[0]:
            midway[1]()
            midway = None
    return [c.find("ROUND_END", is_end) for c in clients]


# --- tests run for every transport --------------------------------------------

def test_login_and_tokens(port):
    ada, bob = Client(port, "ada"), Client(port, "bob")
    assert ada.player_id != bob.player_id
    assert len(bytes.fromhex(ada.token)) == 16 and ada.token != bob.token
    stranger = Client(port)
    stranger.send("LOGIN", name="ada")       # no token: the name is simply taken
    stranger.expect_error(protocol.ERR_NAME_IN_USE)
    stranger.send("LIST_ROOMS")
    stranger.expect_error(protocol.ERR_NOT_LOGGED_IN)
    stranger.send("LOGIN", name="   ")
    stranger.expect_error(protocol.ERR_BAD_NAME)
    stranger.login("cy")
    assert stranger.player_id not in (ada.player_id, bob.player_id)
    stranger.send("LOGIN", name="cy")
    stranger.expect_error(protocol.ERR_ALREADY_LOGGED_IN)
    stranger.send("LOGIN_OK", player_id=1, server_time_ms=0)  # a server message from a client
    stranger.expect_error(protocol.ERR_BAD_MESSAGE)
    # Writing someone else's token into a message gets you nothing.
    stranger.send("JOIN_ROOM", code="ZZZZZZ", token=ada.token)
    if KIND == "tcp":
        # TCP: the token does not belong to this connection.
        stranger.expect_error(protocol.ERR_NOT_LOGGED_IN)
    else:
        # UDP: the JSON token is not even sent; the header's (cy's own) is used,
        # so the request is handled as cy's.
        stranger.expect_error(protocol.ERR_ROOM_NOT_FOUND)
    stranger.send("LIST_ROOMS")
    stranger.expect("ROOM_LIST")


def test_rooms_create_list_join_leave_and_code_expiry(port):
    (ada, bob, cy), code = make_room(port, ["ada", "bob", "cy"])
    assert protocol.is_room_code(code)
    dee = Client(port, "dee")
    dee.send("LIST_ROOMS")
    assert dee.expect("ROOM_LIST")["rooms"] == [
        {"room_id": 1, "code": code, "room_name": "test room", "player_count": 3, "in_match": False}]
    dee.send("JOIN_ROOM", code=code)
    dee.expect_error(protocol.ERR_ROOM_FULL)
    dee.send("JOIN_ROOM", code="ZZZZZZ" if code != "ZZZZZZ" else "YYYYYY")
    dee.expect_error(protocol.ERR_ROOM_NOT_FOUND)
    dee.send("LEAVE_ROOM")
    dee.expect_error(protocol.ERR_NOT_IN_ROOM)
    bob.send("CREATE_ROOM", room_name="second")
    bob.expect_error(protocol.ERR_ALREADY_IN_ROOM)

    bob.send("LEAVE_ROOM")
    for client in (ada, bob, cy):  # the leaver gets a copy as confirmation
        left = client.expect("PLAYER_LEFT")
        assert (left["player_id"], left["reason"]) == (bob.player_id, "left")
    assert [p["name"] for p in ada.expect_room(2)["players"]] == ["ada", "cy"]
    dee.send("JOIN_ROOM", code=f" {code.lower()} ")  # typed sloppily
    assert [p["name"] for p in dee.expect_room(3)["players"]] == ["ada", "cy", "dee"]

    for client in (ada, cy, dee):
        client.send("LEAVE_ROOM")
    dee.expect("PLAYER_LEFT", lambda m: m["player_id"] == dee.player_id)
    bob.send("JOIN_ROOM", code=code)  # the room emptied, so its code is gone
    bob.expect_error(protocol.ERR_ROOM_NOT_FOUND)
    bob.send("LIST_ROOMS")
    assert bob.expect("ROOM_LIST")["rooms"] == []


def test_player_left_is_sent_exactly_once(port):
    (ada, bob, cy), _ = make_room(port, ["ada", "bob", "cy"])
    bob.send("LEAVE_ROOM")
    wait(0.5)
    for client in (ada, bob, cy):
        assert len([m for m in client.inbox if m["type"] == "PLAYER_LEFT"]) == 1, client.name


def test_full_match_scores_and_winner(port):
    (ada, bob, cy), _ = make_room(port, ["ada", "bob", "cy"])
    ada.send("READY", ready=True)
    assert [p["ready"] for p in bob.expect("ROOM_STATE", lambda m: m["players"][0]["ready"])["players"]] == [True, False, False]
    bob.send("READY", ready=True)
    cy.send("READY", ready=True)  # the room is full and ready: it starts by itself
    rates = {ada: 6, bob: 5, cy: 4}
    began = time.monotonic()
    for round_number in (1, 2):  # ada wins both, so there is no third round
        for end in play_round([ada, bob, cy], rates, round_number):
            assert end["finish_order"] == [ada.player_id, bob.player_id, cy.player_id]
            assert end["times_ms"] == sorted(end["times_ms"])
    for client in (ada, bob, cy):
        result = client.expect("MATCH_END")
        assert result["winner"] == ada.player_id
        assert result["final_ranking"] == [ada.player_id, bob.player_id, cy.player_id]
        assert result["scores"][0] == {"id": ada.player_id, "wins": 2, "points": 6}
        # back in the lobby with ready flags cleared
        assert not any(p["ready"] for p in client.expect("ROOM_STATE", lambda m: not m["players"][0]["ready"])["players"])

    snapshots = [m for m in cy.inbox if m["type"] == "SNAPSHOT"]
    ticks = [m["tick"] for m in snapshots if m["round_number"] == 1]
    assert ticks == sorted(ticks) and ticks[0] == 1, "snapshots out of order"
    assert len(snapshots[0]["players"]) == 3
    assert snapshots[-1]["players"][0]["distance"] == 30.0  # ada is on the finish line
    racing_seconds = time.monotonic() - began - 0.3 - 0.2 - 0.3  # minus two countdowns and the intermission
    assert len(snapshots) / racing_seconds > 25, "snapshot rate is well under 30 Hz"


def test_start_rules(port):
    (ada, bob), _ = make_room(port, ["ada", "bob"])
    ada.send("START_MATCH")
    ada.expect_error(protocol.ERR_NOT_READY)
    ada.send("READY", ready=True)
    ada.send("READY", ready=False)  # ready can be withdrawn
    bob.send("READY", ready=True)
    bob.expect("ROOM_STATE", lambda m: [p["ready"] for p in m["players"]] == [False, True])
    ada.send("START_MATCH")
    ada.expect_error(protocol.ERR_NOT_READY)
    ada.send("READY", ready=True)
    bob.expect("ROOM_STATE", lambda m: all(p["ready"] for p in m["players"]))
    wait(0.3)
    assert not bob.find("MATCH_START"), "two ready players must not start without START_MATCH"
    bob.send("START_MATCH")
    bob.expect_error(protocol.ERR_NOT_CREATOR)
    ada.send("START_MATCH")
    bob.expect("MATCH_START")
    ada.send("START_MATCH")
    ada.expect_error(protocol.ERR_MATCH_IN_PROGRESS)


def test_creator_leaving_hands_the_room_over(port):
    (ada, bob, cy), _ = make_room(port, ["ada", "bob", "cy"])
    ada.send("LEAVE_ROOM")
    assert bob.expect_room(2)["creator_id"] == bob.player_id
    ready_up([bob, cy])  # bob, now the creator, sends START_MATCH
    cy.expect("MATCH_START")


def test_disconnect_in_lobby(port):
    (ada, bob, cy), _ = make_room(port, ["ada", "bob", "cy"])
    cy_id = cy.player_id
    cy.kill()
    for client in (ada, bob):
        left = client.expect("PLAYER_LEFT")
        # TCP sees the closed socket at once; UDP can only notice the silence
        assert left["player_id"] == cy_id and left["reason"] in ("closed", "reset", "timeout"), left
        client.expect_room(2)
    ready_up([ada, bob])  # the room still works
    ada.expect("MATCH_START")


def test_disconnect_during_round(port):
    (ada, bob, cy), _ = make_room(port, ["ada", "bob", "cy"])
    ready_up([ada, bob, cy])
    cy_id = cy.player_id
    rates = {ada: 6, bob: 5, cy: 6}
    ends = play_round([ada, bob, cy], rates, 1, kill=(cy, 0.5))
    assert all(end["finish_order"] == [ada.player_id, bob.player_id] for end in ends)
    assert ada.expect("PLAYER_LEFT")["player_id"] == cy_id
    play_round([ada, bob], rates, 2)
    assert bob.expect("MATCH_END")["final_ranking"] == [ada.player_id, bob.player_id]


def test_last_player_standing_wins(port):
    (ada, bob), _ = make_room(port, ["ada", "bob"])
    ready_up([ada, bob])
    play_round([ada, bob], {ada: 3, bob: 3}, 1, kill=(bob, 0.5))
    result = ada.expect("MATCH_END")
    assert result["winner"] == ada.player_id and result["final_ranking"] == [ada.player_id]
    ada.expect_room(1)


def test_silent_client_times_out(port):
    (ada, bob, cy), _ = make_room(port, ["ada", "bob", "cy"])
    cy.frozen = True  # socket stays open, nothing is sent: a hung machine or pulled cable
    frozen_at = time.monotonic()
    left = ada.expect("PLAYER_LEFT")
    elapsed = time.monotonic() - frozen_at
    assert (left["player_id"], left["reason"]) == (cy.player_id, "timeout")
    assert 1.5 < elapsed < 3.5, f"detected after {elapsed:.2f}s, expected about 2s"
    bob.expect_room(2)


def test_bye_is_reported_as_bye(port):
    (ada, bob), _ = make_room(port, ["ada", "bob"])
    bob.send("BYE")
    left = ada.expect("PLAYER_LEFT")
    assert (left["player_id"], left["reason"]) == (bob.player_id, "bye")


def test_reconnect_with_token_keeps_player_id(port):
    (ada, bob), code = make_room(port, ["ada", "bob"])
    old_id, token = bob.player_id, bob.token
    bob.kill()
    ada.expect("PLAYER_LEFT")  # the server has noticed
    bob = Client(port, "bob", token)
    assert bob.player_id == old_id and bob.token == token
    bob.send("JOIN_ROOM", code=code)
    assert [p["id"] for p in bob.expect_room(2)["players"]] == [ada.player_id, old_id]


def test_name_is_reserved_for_the_token_holder(port):
    (ada, bob), _ = make_room(port, ["ada", "bob"])
    old_id = bob.player_id
    bob.kill()
    ada.expect("PLAYER_LEFT")
    thief = Client(port)
    for guess in ("", "ff" * 16, "not hex é"):
        thief.send("LOGIN", name="bob", token=guess)
        thief.expect_error(protocol.ERR_NAME_IN_USE)
    wait(1.2)  # the test server keeps reservations for 1 s
    thief.login("bob")
    assert thief.player_id != old_id, "an expired reservation must not hand over the old identity"


def test_token_holder_takes_over_a_live_session(port):
    (ada, bob), _ = make_room(port, ["ada", "bob"])
    bob.frozen = True  # bob's machine hangs; the server has not noticed yet
    bob_again = Client(port, "bob", bob.token)
    assert bob_again.player_id == bob.player_id
    left = ada.expect("PLAYER_LEFT")
    assert (left["player_id"], left["reason"]) == (bob.player_id, "replaced")


def test_simultaneous_joins(port):
    # Two free slots, two joins sent before either is answered: both get in.
    (ada,), code = make_room(port, ["ada"])
    bob, cy = Client(port, "bob"), Client(port, "cy")
    bob.send("JOIN_ROOM", code=code)
    cy.send("JOIN_ROOM", code=code)
    for client in (ada, bob, cy):
        assert {p["name"] for p in client.expect_room(3)["players"]} == {"ada", "bob", "cy"}
    settle()
    # One free slot, two joins at once: exactly one wins.
    cy.send("LEAVE_ROOM")
    ada.expect_room(2)
    settle()
    dee, eve = Client(port, "dee"), Client(port, "eve")
    dee.send("JOIN_ROOM", code=code)
    eve.send("JOIN_ROOM", code=code)
    replies = [c.expect(("ROOM_STATE", "ERROR")) for c in (dee, eve)]
    assert sorted(m["type"] for m in replies) == ["ERROR", "ROOM_STATE"]
    assert next(m for m in replies if m["type"] == "ERROR")["code"] == protocol.ERR_ROOM_FULL
    assert len(ada.expect_room(3)["players"]) == 3


def test_game_logic_never_imports_a_transport(port):
    root = pathlib.Path(__file__).parent.parent
    files = list((root / "common").glob("*.py")) + [
        root / "server" / "server.py", root / "server" / "room.py", root / "client" / "core.py"]
    for path in files:
        imports = re.findall(r"^\s*(?:from|import)\s+transport\b.*$", path.read_text(encoding="utf-8"), re.MULTILINE)
        assert not imports, f"{path.name} imports a transport: {imports}"


# --- TCP only: framing ----------------------------------------------------------

def frame(message):
    payload = encode(message)
    return struct.pack(">I", len(payload)) + payload


def read_frames(sock, count):
    """Blocking read of `count` framed messages from a raw socket."""
    buffer, messages = b"", []
    while len(messages) < count:
        data = sock.recv(4096)
        assert data, "server closed the connection"
        buffer += data
        while len(buffer) >= 4 and len(buffer) >= 4 + struct.unpack(">I", buffer[:4])[0]:
            length = struct.unpack(">I", buffer[:4])[0]
            messages.append(decode(buffer[4:4 + length]))
            buffer = buffer[4 + length:]
    return messages


def test_tcp_split_and_merged_frames(port):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for byte in frame({"type": "LOGIN", "name": "raw", "token": ""}):  # one message over many segments
            sock.sendall(bytes([byte]))
            time.sleep(0.001)
        token = read_frames(sock, 1)[0]["token"]
        # two messages in one segment
        sock.sendall(frame({"type": "PING", "t": 7, "rtt_ms": 0, "token": token}) + frame({"type": "LIST_ROOMS", "token": token}))
        replies = read_frames(sock, 2)
        assert [m["type"] for m in replies] == ["PONG", "ROOM_LIST"]
        assert replies[0]["t"] == 7


def test_tcp_garbage_drops_only_the_sender(port):
    bystander = Client(port, "ada")
    for garbage in (b"\xff\xff\xff\xff", struct.pack(">I", 9) + b"not json!", b"\x00\x00\x00\x00"):
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.sendall(garbage)
            try:
                assert sock.recv(4096) == b"", "server should close the connection"
            except ConnectionResetError:
                pass  # also a close, just a less polite one
    bystander.send("LIST_ROOMS")
    bystander.expect("ROOM_LIST")


def test_tcp_preflight_tells_open_from_closed(port):
    assert preflight.check_tcp("127.0.0.1", port)[0]
    with socket.socket() as unused:  # bound but not listening: connections are refused
        unused.bind(("127.0.0.1", 0))
        ok, detail = preflight.check_tcp("127.0.0.1", unused.getsockname()[1])
    assert not ok and "REFUSED" in detail, detail


# --- UDP only: the reliability layer ---------------------------------------------

def test_udp_preflight_tells_open_from_closed(port):
    ok, detail = preflight.check_udp("127.0.0.1", port)
    assert ok, detail
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as unused:
        unused.bind(("127.0.0.1", 0))
        closed_port = unused.getsockname()[1]
    ok, detail = preflight.check_udp("127.0.0.1", closed_port)  # nobody there any more
    assert not ok and ("REFUSED" in detail or "NO ANSWER" in detail), detail


class Pair:
    """A bare server transport and client transport on loopback, already logged
    in, with no game server in between. Tests wrap _transmit to impair the link."""

    def __init__(self):
        self.server = UdpTransport.listen("127.0.0.1", 0)
        self.client = UdpTransport.connect("127.0.0.1", self.server.address[1])
        self.at_server, self.at_client, self.lost = [], [], []
        self.server.on_disconnect(lambda peer, reason: self.lost.append(("server", reason)))
        self.client.on_disconnect(lambda peer, reason: self.lost.append(("client", reason)))
        self.peer = None  # the server's id for the client
        self.to_server("LOGIN", name="pair")
        self.pump(lambda: self.at_server)
        token = "ab" * 16
        self.server.authenticate(self.peer, token)
        self.client.authenticate(self.client.server_peer, token)
        self.to_server("PING", t=0, rtt_ms=0)  # first datagram carrying the token proves the address
        self.pump(lambda: len(self.at_server) == 2)
        self.at_server.clear()

    def to_server(self, msg_type, **fields):
        self.client.send(self.client.server_peer, {"type": msg_type, "token": "", **fields})

    def to_client(self, msg_type, **fields):
        self.server.send(self.peer, {"type": msg_type, **fields})

    def step(self):
        for peer, message in self.server.recv(0.002):
            self.peer = peer
            self.at_server.append(message)
        self.at_client += [message for _, message in self.client.recv(0.002)]

    def pump(self, until, timeout=20.0):
        deadline = time.monotonic() + timeout
        while not until():
            assert time.monotonic() < deadline, f"timed out; lost={self.lost}"
            self.step()

    def pump_for(self, seconds):
        deadline = time.monotonic() + seconds
        self.pump(lambda: time.monotonic() > deadline)

    def close(self):
        self.client.close()
        self.server.close()


def lose(transport, rate, rng):
    """Drop that fraction of the datagrams this transport sends."""
    original = transport._transmit
    transport._transmit = lambda data, address: original(data, address) if rng.random() >= rate else None


def duplicate(transport):
    original = transport._transmit
    transport._transmit = lambda data, address: (original(data, address), original(data, address))


def reorder(transport, batch):
    """Hold datagrams back and release them `batch` at a time, newest first."""
    original, held = transport._transmit, []

    def transmit(data, address):
        held.append((data, address))
        if len(held) >= batch:
            while held:
                original(*held.pop())
    transport._transmit = transmit


def numbers(messages, msg_type, field):
    return [int(m[field]) for m in messages if m["type"] == msg_type]


def test_udp_seq_diff_handles_wraparound(port):
    diff = udp_transport.seq_diff
    assert diff(5, 3) == 2 and diff(3, 5) == -2 and diff(7, 7) == 0
    assert diff(2, 65534) == 4 and diff(65534, 2) == -4
    assert diff(0, 65535) == 1 and diff(65535, 0) == -1


def test_udp_loss_is_repaired_by_retransmission(port):
    pair, rng = Pair(), random.Random(1)
    lose(pair.server, 0.15, rng)
    lose(pair.client, 0.15, rng)
    for i in range(60):
        pair.to_server("CREATE_ROOM", room_name=str(i))
        pair.to_client("ERROR", code="X", detail=str(i))
    pair.pump(lambda: len(pair.at_server) >= 60 and len(numbers(pair.at_client, "ERROR", "detail")) >= 60)
    assert numbers(pair.at_server, "CREATE_ROOM", "room_name") == list(range(60)), "lost, repeated or out of order"
    assert numbers(pair.at_client, "ERROR", "detail") == list(range(60))
    assert pair.client.stats()["retransmits"] > 0 and pair.server.stats()["retransmits"] > 0
    assert not pair.lost
    pair.close()


def test_udp_retransmit_timer_backs_off_and_gives_up_at_the_peer_timeout(port):
    pair = Pair()
    server = pair.client.server_peer
    for _ in range(20):
        pair.client.rtt_sample(server, 10)  # a fast, steady link: the timer sits at its 100 ms floor
    assert pair.client.stats(server)["rto_ms"] == 100
    attempts = []
    pair.client._transmit = lambda data, address: attempts.append(time.monotonic()) if data[2] == udp_transport.KIND_RELIABLE else None
    pair.to_server("CREATE_ROOM", room_name="into the void")
    # The server hears nothing either and times the client out at the same 2 s,
    # so wait for the client's own verdict, whichever comes first.
    pair.pump(lambda: any(side == "client" for side, _ in pair.lost))
    assert ("client", "unreachable") in pair.lost, pair.lost
    gaps = [round(b - a, 2) for a, b in zip(attempts, attempts[1:])]
    # 100 ms, then doubling, capped at 500 ms
    assert len(gaps) == 5 and all(abs(gap - want) < 0.06 for gap, want in zip(gaps, [0.1, 0.2, 0.4, 0.5, 0.5])), gaps
    gave_up_after = time.monotonic() - attempts[0]
    assert 1.95 < gave_up_after < 2.3, f"gave up after {gave_up_after:.2f}s, expected 2s"
    pair.close()


def test_udp_rto_follows_measured_rtt(port):
    pair = Pair()
    server = pair.client.server_peer
    assert pair.client.stats(server)["rto_ms"] == 200  # before any sample
    pair.client.rtt_sample(server, 60)                 # first sample: srtt 60, variation 30
    assert pair.client.stats(server)["rto_ms"] == 180
    for _ in range(50):
        pair.client.rtt_sample(server, 60)             # steady: variation decays, timer tightens
    assert pair.client.stats(server)["rto_ms"] == 100
    for rtt in (60, 400) * 10:
        pair.client.rtt_sample(server, rtt)            # jittery: timer backs away, up to the cap
    assert pair.client.stats(server)["rto_ms"] == 500
    pair.close()


def test_udp_token_travels_in_the_header_only(port):
    ada = Client(port, "ada")
    sent = []
    original = ada.transport._transmit
    ada.transport._transmit = lambda data, address: (sent.append(data), original(data, address))
    ada.send("LIST_ROOMS")       # the test client builds every message with its token...
    ada.expect("ROOM_LIST")      # ...and the server accepted it as ada's
    listing = next(d for d in sent if b"LIST_ROOMS" in d)
    assert listing[udp_transport.HEADER.size:] == b'{"type":"LIST_ROOMS"}'
    assert listing[9:25] == bytes.fromhex(ada.token)


def test_udp_duplicates_are_suppressed(port):
    pair = Pair()
    duplicate(pair.server)
    duplicate(pair.client)
    for i in range(20):
        pair.to_server("CREATE_ROOM", room_name=str(i))
        pair.to_server("PING", t=i, rtt_ms=0)
        pair.to_client("ERROR", code="X", detail=str(i))
        pair.to_client("PONG", t=i, server_time_ms=0)
    pair.pump_for(0.5)
    assert numbers(pair.at_server, "CREATE_ROOM", "room_name") == list(range(20))
    assert numbers(pair.at_server, "PING", "t") == list(range(20))
    assert numbers(pair.at_client, "ERROR", "detail") == list(range(20))
    assert numbers(pair.at_client, "PONG", "t") == list(range(20))
    assert pair.server.stats()["duplicates_dropped"] >= 40 and pair.client.stats()["duplicates_dropped"] >= 40
    pair.close()


def test_udp_reliable_stays_ordered_and_stale_snapshots_are_discarded(port):
    pair = Pair()
    reorder(pair.server, batch=4)
    for i in range(40):
        pair.to_client("ERROR", code="X", detail=str(i))
        pair.to_client("SNAPSHOT", round_number=1, tick=i + 1, server_time_ms=0, players=[])
    pair.pump(lambda: len(numbers(pair.at_client, "ERROR", "detail")) >= 40)
    pair.pump_for(0.6)
    # Reliable: delivered in the order sent, though they arrived shuffled.
    assert numbers(pair.at_client, "ERROR", "detail") == list(range(40))
    # Unreliable: handed over as they arrive, shuffled...
    arrived = [m for m in pair.at_client if m["type"] == "SNAPSHOT"]
    ticks = [m["tick"] for m in arrived]
    assert ticks != sorted(ticks), "the link did not reorder anything"
    # ...so the client applies a snapshot only if it is newer than the last one applied.
    applied, newest, stale = [], (0, 0), 0
    for snapshot in arrived:
        if protocol.snapshot_key(snapshot) > newest:
            newest = protocol.snapshot_key(snapshot)
            applied.append(snapshot["tick"])
        else:
            stale += 1
    assert stale > 0 and applied == sorted(set(applied))
    pair.close()


def test_udp_sequence_numbers_wrap_around(port):
    UdpTransport.INITIAL_SEQ = 65500
    try:
        pair, rng = Pair(), random.Random(2)
        lose(pair.server, 0.1, rng)
        lose(pair.client, 0.1, rng)
        for i in range(100):
            pair.to_server("CREATE_ROOM", room_name=str(i))
            pair.to_client("ERROR", code="X", detail=str(i))
        pair.pump(lambda: len(pair.at_server) >= 100 and len(pair.at_client) >= 100)
        assert numbers(pair.at_server, "CREATE_ROOM", "room_name") == list(range(100))
        assert numbers(pair.at_client, "ERROR", "detail") == list(range(100))
        peer = pair.client._peers[pair.client.server_peer]
        assert peer.next_seq == (65500 + 101) % 65536 and peer.next_pkt < 65500, "counters did not wrap"
        lost = pair.server.stats(pair.peer)["datagrams_lost"]  # the loss estimate must survive the wrap too
        assert 0 < lost < pair.client.stats()["datagrams_sent"]
        pair.close()
    finally:
        UdpTransport.INITIAL_SEQ = 0


def test_udp_address_change_mid_match(port):
    (ada, bob), _ = make_room(port, ["ada", "bob"])
    ready_up([ada, bob])

    def move_bob():
        """Give bob a new source port, as a NAT that re-maps the flow would."""
        old = bob.transport._sock
        new = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        new.bind(("", 0))
        new.setblocking(False)
        bob.transport._sock = new
        old.close()

    ends = play_round([ada, bob], {ada: 6, bob: 5}, 1, midway=(0.5, move_bob))
    assert all(end["finish_order"] == [ada.player_id, bob.player_id] for end in ends)
    assert SERVER.stats()["rebinds"] == 1
    assert not ada.find("PLAYER_LEFT") and ada.lost is None and bob.lost is None
    after_move = [m for m in bob.inbox if m["type"] == "SNAPSHOT" and m["tick"] > 30]
    assert after_move, "bob stopped receiving snapshots after the move"


def test_udp_junk_is_dropped_and_counted(port):
    bystander = Client(port, "ada")
    header = udp_transport.HEADER
    ping = encode({"type": "PING", "t": 1, "rtt_ms": 0, "token": ""})
    junk = [
        b"hello",                                                    # too short
        os.urandom(100),                                             # wrong magic
        os.urandom(2000),                                            # too long
        header.pack(b"DR", 9, 0, 0, 0, bytes(16)) + ping,            # unknown kind
        header.pack(b"DR", 1, 0, 0, 0, bytes(16)) + b"not json",     # bad payload
        header.pack(b"DR", 1, 0, 0, 0, os.urandom(16)) + ping,       # token nobody was given
        header.pack(b"DR", 1, 0, 0, 0, bytes(16)) + ping,            # no token, and not a LOGIN
    ]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for datagram in junk + [b"flood"] * 200:
            sock.sendto(datagram, ("127.0.0.1", port))
        wait(0.3)
        sock.setblocking(False)
        try:
            reply = sock.recvfrom(2048)
        except BlockingIOError:
            reply = None
        assert reply is None, "junk must not be answered"
    stats = SERVER.stats()
    assert stats["dropped_malformed"] >= 5 and stats["dropped_unauthenticated"] >= 2, stats
    assert stats["dropped_rate_limited"] >= 150, stats
    bystander.send("LIST_ROOMS")  # authenticated traffic is not rate limited, and nobody was dropped
    bystander.expect("ROOM_LIST")
    assert bystander.lost is None


def test_udp_probe_is_echoed_once_per_second(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.3)
        probes = [udp_transport.PROBE_MAGIC + os.urandom(8) for _ in range(5)]
        sock.sendto(udp_transport.PROBE_MAGIC + bytes(40), ("127.0.0.1", port))  # oversized: ignored
        for probe in probes:
            sock.sendto(probe, ("127.0.0.1", port))
        replies = []
        try:
            while True:
                replies.append(sock.recvfrom(2048)[0])
        except socket.timeout:
            pass
    # one reply per source IP per second (two if the burst straddled a second boundary)
    assert 1 <= len(replies) <= 2 and all(reply in probes for reply in replies), replies


def test_udp_unproven_address_is_sent_at_most_three_times_what_it_sent(port):
    login = udp_transport.HEADER.pack(b"DR", udp_transport.KIND_RELIABLE, 0, 0, 0, bytes(16)) \
        + encode({"type": "LOGIN", "name": "spoofed", "token": ""})
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(1.5)
        sock.sendto(login, ("127.0.0.1", port))  # and then never answer, like a forged source would
        replies = []
        try:
            while True:
                replies.append(sock.recvfrom(2048)[0])
        except socket.timeout:
            pass
    payloads = [reply[udp_transport.HEADER.size:] for reply in replies]
    assert any(decode(payload)["type"] == "LOGIN_OK" for payload in payloads if payload)
    assert sum(len(reply) for reply in replies) <= 3 * len(login), [len(r) for r in replies]


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_")]
    ran = 0
    for KIND in TRANSPORTS:
        for name, fn in tests:
            only = name.split("_")[1]
            if only in TRANSPORTS and only != KIND:
                continue
            if sys.argv[1:] and not any(word in name for word in sys.argv[1:]):
                continue
            started = time.monotonic()
            with running_server() as server_port:
                fn(server_port)
            ran += 1
            print(f"ok   {KIND}  {name}  ({time.monotonic() - started:.1f}s)", flush=True)
    print(f"{ran} passed")
