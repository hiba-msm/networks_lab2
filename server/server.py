"""The game server: login, sessions, lobby, rooms, the 30 Hz loop, disconnects.

It is handed a transport object and only uses the interface in transport/base.py,
so this file is identical for the TCP and UDP versions. One thread does
everything: messages are handled one at a time in arrival order, which is why
two clients acting "at the same moment" can never corrupt shared state.
"""

import hmac
import logging
import secrets
import time

from common import protocol
from common.constants import (
    COUNTDOWN_MS, INTERMISSION_MS, MAX_NAME_LEN, MAX_ROOM_NAME_LEN, ROOM_LIST_MAX,
    ROOM_SIZE, SESSION_KEEP_MS, TICK_DT, TRACK_LENGTH,
)
from server.room import LOBBY, Room

log = logging.getLogger("server")


class Player:
    """One session. It outlives the connection, so a client can come back to it."""

    def __init__(self, player_id, name, token):
        self.player_id = player_id
        self.name = name
        self.token = token             # secret; whoever presents it is this player
        self.peer = None               # transport peer, None while disconnected
        self.disconnected_at_ms = 0
        self.room = None
        self.ready = False


def clean_name(text, max_len):
    """Returns the trimmed name, or None if it is not acceptable."""
    text = text.strip()
    return text if 0 < len(text) <= max_len and text.isprintable() else None


class GameServer:
    def __init__(self, transport, track_length=TRACK_LENGTH, countdown_ms=COUNTDOWN_MS,
                 intermission_ms=INTERMISSION_MS, session_keep_ms=SESSION_KEEP_MS):
        self.transport = transport
        self.track_length = track_length
        self.countdown_ms = countdown_ms
        self.intermission_ms = intermission_ms
        self.session_keep_ms = session_keep_ms
        self.sessions = {}    # token -> Player, connected or recently disconnected
        self.players = {}     # peer -> Player, connected only
        self.rooms = {}       # code -> Room
        self.next_player_id = 1
        self.next_room_id = 1
        self.running = False
        self.started = time.monotonic()
        transport.on_disconnect(self.on_disconnect)
        self.handlers = {
            "LIST_ROOMS": self.on_list_rooms,
            "CREATE_ROOM": self.on_create_room,
            "JOIN_ROOM": self.on_join_room,
            "LEAVE_ROOM": self.on_leave_room,
            "READY": self.on_ready,
            "START_MATCH": self.on_start_match,
            "INPUT": self.on_input,
            "PRESS": self.on_input,
            "BYE": self.on_bye,
        }

    def now_ms(self):
        """Server clock: milliseconds since the server started."""
        return int((time.monotonic() - self.started) * 1000)

    def send(self, player, message):
        self.transport.send(player.peer, message)

    def send_error(self, peer, code, detail):
        self.transport.send(peer, {"type": "ERROR", "code": code, "detail": detail})

    # --- main loop -----------------------------------------------------------

    def run(self):
        self.running = True
        next_tick = time.monotonic()
        while self.running:
            # Wait for network traffic, but never past the next tick.
            timeout = max(0.0, next_tick - time.monotonic())
            for peer, message in self.transport.recv(timeout):
                self.handle(peer, message)
            now = time.monotonic()
            if now - next_tick > 5 * TICK_DT:
                # Fell far behind (the machine stalled). Skip the missed ticks
                # rather than running a burst of them back to back.
                log.warning("server loop is %.0f ms late, skipping ticks", (now - next_tick) * 1000)
                next_tick = now
            while now >= next_tick:
                self.tick()
                next_tick += TICK_DT

    def tick(self):
        now_ms = self.now_ms()
        for room in list(self.rooms.values()):
            room.tick(now_ms)

    # --- message dispatch ----------------------------------------------------

    def handle(self, peer, message):
        msg_type = message["type"]
        if msg_type == "LOGIN":
            self.on_login(peer, message)
        elif msg_type == "PING":
            # Answered for anyone: it changes no state, and it is the heartbeat.
            if message["rtt_ms"] > 0:
                self.transport.rtt_sample(peer, message["rtt_ms"])
            self.transport.send(peer, {"type": "PONG", "t": message["t"], "server_time_ms": self.now_ms()})
        elif msg_type not in self.handlers:
            self.send_error(peer, protocol.ERR_BAD_MESSAGE, f"{msg_type} is not a client message")
        else:
            # The player is whoever the token says, and only on the connection
            # that logged in with it. Name and network address are never trusted.
            player = self.sessions.get(message["token"])
            if player is None or player.peer != peer:
                self.send_error(peer, protocol.ERR_NOT_LOGGED_IN, "send LOGIN first")
            else:
                self.handlers[msg_type](player, message)

    def on_login(self, peer, message):
        if peer in self.players:
            self.send_error(peer, protocol.ERR_ALREADY_LOGGED_IN, f"already logged in as {self.players[peer].name}")
            return
        name = clean_name(message["name"], MAX_NAME_LEN)
        if name is None:
            self.send_error(peer, protocol.ERR_BAD_NAME, f"name must be 1 to {MAX_NAME_LEN} printable characters")
            return
        # Reservations of players who disconnected long ago lapse here.
        now_ms = self.now_ms()
        for stale in [p for p in self.sessions.values()
                      if p.peer is None and now_ms - p.disconnected_at_ms > self.session_keep_ms]:
            del self.sessions[stale.token]

        player = next((p for p in self.sessions.values() if p.name == name), None)
        if player is None:
            # Registration: a new name gets a new id and a fresh secret token.
            player = Player(self.next_player_id, name, secrets.token_hex(16))
            self.next_player_id += 1
            self.sessions[player.token] = player
        elif not hmac.compare_digest(message["token"].encode(), player.token.encode()):
            # Without the token, a known name is simply taken. (compare_digest
            # takes the same time whether or not the guess was close.)
            self.send_error(peer, protocol.ERR_NAME_IN_USE, f"{name} is taken")
            return
        elif player.peer is not None:
            # The token holder is back before the old connection was noticed dead.
            old_peer = player.peer
            self.drop_player(player, "replaced")
            self.transport.close_peer(old_peer)

        player.peer = peer
        self.players[peer] = player
        log.info("player %s (%s) logged in", player.player_id, name)
        self.send(player, {"type": "LOGIN_OK", "player_id": player.player_id,
                           "token": player.token, "server_time_ms": now_ms})
        self.transport.authenticate(peer, player.token)

    def on_list_rooms(self, player, message):
        # Only the first ROOM_LIST_MAX rooms, so the reply always fits one datagram.
        # Rooms are joined by code anyway; add paging if a full room browser is ever needed.
        rooms = [
            {"room_id": r.room_id, "code": r.code, "room_name": r.name,
             "player_count": len(r.players), "in_match": r.state != LOBBY}
            for r in list(self.rooms.values())[:ROOM_LIST_MAX]
        ]
        self.send(player, {"type": "ROOM_LIST", "rooms": rooms})

    def on_create_room(self, player, message):
        if player.room is not None:
            self.send_error(player.peer, protocol.ERR_ALREADY_IN_ROOM, "leave your room first")
            return
        name = clean_name(message["room_name"], MAX_ROOM_NAME_LEN)
        if name is None:
            self.send_error(player.peer, protocol.ERR_BAD_NAME,
                            f"room name must be 1 to {MAX_ROOM_NAME_LEN} printable characters")
            return
        code = protocol.make_room_code()
        while code in self.rooms:
            code = protocol.make_room_code()
        room = Room(self, self.next_room_id, code, name, creator=player)
        self.next_room_id += 1
        self.rooms[code] = room
        log.info("room %s (%s) created by player %s", code, name, player.player_id)
        room.add(player)

    def on_join_room(self, player, message):
        if player.room is not None:
            self.send_error(player.peer, protocol.ERR_ALREADY_IN_ROOM, "leave your room first")
            return
        code = protocol.normalize_room_code(message["code"])
        room = self.rooms.get(code)
        if room is None:
            self.send_error(player.peer, protocol.ERR_ROOM_NOT_FOUND, f"no room with code {code}")
        elif len(room.players) >= ROOM_SIZE:
            self.send_error(player.peer, protocol.ERR_ROOM_FULL, f"room {code} is full")
        elif room.state != LOBBY:
            self.send_error(player.peer, protocol.ERR_MATCH_IN_PROGRESS, f"room {code} is mid-match")
        else:
            room.add(player)

    def on_leave_room(self, player, message):
        if player.room is None:
            self.send_error(player.peer, protocol.ERR_NOT_IN_ROOM, "you are not in a room")
            return
        self.leave_room(player, "left")

    def on_ready(self, player, message):
        if player.room is None:
            self.send_error(player.peer, protocol.ERR_NOT_IN_ROOM, "join a room first")
        elif player.room.state != LOBBY:
            self.send_error(player.peer, protocol.ERR_MATCH_IN_PROGRESS, "the match has already started")
        else:
            player.room.set_ready(player, message["ready"])

    def on_start_match(self, player, message):
        room = player.room
        if room is None:
            self.send_error(player.peer, protocol.ERR_NOT_IN_ROOM, "join a room first")
        elif room.state != LOBBY:
            self.send_error(player.peer, protocol.ERR_MATCH_IN_PROGRESS, "the match has already started")
        elif player is not room.creator:
            self.send_error(player.peer, protocol.ERR_NOT_CREATOR, "only the room creator can start the match")
        elif not room.everyone_ready():
            self.send_error(player.peer, protocol.ERR_NOT_READY, "needs at least 2 players, all ready")
        else:
            room.start_match()

    def on_input(self, player, message):
        if player.room is not None:
            player.room.on_input(player, message)

    def on_bye(self, player, message):
        peer = player.peer
        self.drop_player(player, "bye")
        self.transport.close_peer(peer)

    # --- leaving and disconnects ---------------------------------------------

    def leave_room(self, player, reason):
        room = player.room
        room.remove(player, reason)
        if not room.players:
            del self.rooms[room.code]  # the code expires with the room
            log.info("room %s closed", room.code)

    def drop_player(self, player, reason):
        """The connection is gone. The session stays, so the token can reclaim it."""
        log.info("player %s (%s) gone: %s", player.player_id, player.name, reason)
        if player.room is not None:
            self.leave_room(player, reason)
        del self.players[player.peer]
        player.peer = None
        player.disconnected_at_ms = self.now_ms()

    def on_disconnect(self, peer, reason):
        """Transport callback: the peer vanished without a BYE."""
        player = self.players.get(peer)
        if player is not None:  # None: it never logged in, or already said BYE
            self.drop_player(player, reason)
