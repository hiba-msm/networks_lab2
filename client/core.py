"""The client without a window: connection, lobby state, the round in progress,
prediction and telemetry. The pygame client draws what this object holds; the
bot client runs the very same object with no drawing at all.

Like the server it is handed a transport and never imports one.
Call update() often (every frame); everything happens inside it.
"""

import time
from collections import deque

from common import protocol
from common.constants import HEARTBEAT_INTERVAL_MS, STALL_TICKS, TICK_DT
from common.game import Car

TICK_MS = TICK_DT * 1000
RECONCILE = 0.3  # share of the prediction error removed by each snapshot


def now_ms():
    return int(time.monotonic() * 1000)


def percentile(samples, fraction):
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))] if ordered else 0


class Round:
    """One round as this client sees it."""

    def __init__(self, start):
        self.round_number = start["round_number"]
        self.start_at_ms = start["start_at_server_time_ms"]  # on the server's clock
        self.track_length = start["track_length"]
        self.players = start["players"]  # [{id, name}] in lane order
        self.presses = 0           # our own key presses this round
        self.tick = 0              # local simulation ticks run so far
        self.car = Car()           # prediction of our own car
        self.cars = {}             # player id -> that player's entry in the newest snapshot
        self.newest_key = (0, 0)   # snapshot_key of the newest snapshot applied
        self.snapshot_at_ms = 0    # local time it arrived
        self.result = None         # the ROUND_END message, once the round is over
        self.ended_at_ms = 0

    def name_of(self, player_id):
        return next((p["name"] for p in self.players if p["id"] == player_id), "?")


class GameClient:
    def __init__(self, transport, event_mode=False):
        self.transport = transport
        self.peer = transport.server_peer
        self.event_mode = event_mode  # send PRESS events instead of cumulative INPUT
        transport.on_disconnect(self._on_disconnect)

        self.name = None
        self.player_id = None      # None until LOGIN_OK
        self.token = ""
        self.lost = None           # disconnect reason once the server is gone
        self.error = None          # the last ERROR message from the server
        self.rooms = []            # last ROOM_LIST
        self.room = None           # last ROOM_STATE while in a room
        self.round = None          # Round while a match is on
        self.match_result = None   # the MATCH_END message
        self.notices = deque(maxlen=4)  # (local ms, text), e.g. "BOB LEFT (timeout)"

        # clock and RTT
        self.rtt_ms = 0
        self.rtt_samples = deque(maxlen=200)
        self.clock_offset_ms = None      # server clock minus local clock
        self._offset_samples = deque(maxlen=16)  # (rtt, offset)
        self._last_ping_ms = 0

        # telemetry
        self.snapshot_times = deque()    # local arrival times within the last second
        self.stale_snapshots = 0         # discarded because a newer one was already applied
        self.input_ack_ms = deque(maxlen=200)  # key press -> snapshot that includes it
        self.desync_samples = deque(maxlen=2000)  # |predicted - authoritative| of our car, per snapshot
        self._press_log = deque()        # (press count, local ms) not yet seen in a snapshot
        self.up_bps = self.down_bps = 0
        self._rate_mark = (now_ms(), 0, 0)  # (time, bytes sent, bytes received) one second ago

    # --- actions ---------------------------------------------------------------

    def _send(self, msg_type, **fields):
        fields.setdefault("token", self.token)
        self.transport.send(self.peer, {"type": msg_type, **fields})

    def login(self, name, token=""):
        """token: a saved one reclaims the same player_id after a disconnect."""
        self.name = name
        self._send("LOGIN", name=name, token=token)

    def list_rooms(self):
        self._send("LIST_ROOMS")

    def create_room(self, room_name):
        self._send("CREATE_ROOM", room_name=room_name)

    def join_room(self, code):
        self._send("JOIN_ROOM", code=code)

    def leave_room(self):
        self._send("LEAVE_ROOM")

    def set_ready(self, ready):
        self._send("READY", ready=ready)

    def start_match(self):
        self._send("START_MATCH")

    def bye(self):
        """Leave politely, then close. The server reports 'bye', not a timeout."""
        if self.lost is None:
            self._send("BYE")
        self.transport.close()
        self.lost = self.lost or "closed_by_us"

    def press(self):
        """One hit of the key. Counts only while the round is actually running."""
        r = self.round
        if r is None or r.result is not None or self.server_now_ms() < r.start_at_ms:
            return False
        r.presses += 1
        self._press_log.append((r.presses, now_ms()))
        return True

    # --- clock -----------------------------------------------------------------

    def server_now_ms(self):
        """Our estimate of the server's clock. Every client turns the same
        MATCH_START timestamp into its own local time with this."""
        return now_ms() + (self.clock_offset_ms or 0)

    # --- the loop --------------------------------------------------------------

    def update(self):
        for _, message in self.transport.recv(0):
            getattr(self, "_on_" + message["type"].lower())(message)
        if self.lost is not None:
            return
        now = now_ms()
        if now - self._last_ping_ms >= HEARTBEAT_INTERVAL_MS:
            # PING is the heartbeat, the RTT probe and the clock sync in one.
            self._last_ping_ms = now
            self._send("PING", t=now, rtt_ms=self.rtt_ms)
        self._run_round()
        if now - self._rate_mark[0] >= 1000:
            stats = self.transport.stats(self.peer)
            sent, received = stats.get("wire_bytes_sent", 0), stats.get("wire_bytes_received", 0)
            seconds = (now - self._rate_mark[0]) / 1000
            self.up_bps = int((sent - self._rate_mark[1]) / seconds)
            self.down_bps = int((received - self._rate_mark[2]) / seconds)
            self._rate_mark = (now, sent, received)

    def _run_round(self):
        """Run our own 30 Hz simulation in step with the server's clock."""
        r = self.round
        if r is None or r.result is not None:
            return
        due = int((self.server_now_ms() - r.start_at_ms) / TICK_MS)  # ticks that should have run
        if due - r.tick > 15:
            r.tick = due - 1  # we were frozen (window dragged, laptop slept): do not replay it all
        while r.tick < due:
            r.tick += 1
            new_presses = r.presses - r.car.presses
            r.car.step(new_presses, r.tick, r.track_length)  # predict: same rules as the server
            if not self.event_mode:
                self._send("INPUT", round_number=r.round_number, tick=r.tick,
                           cumulative_presses=r.presses, client_time_ms=now_ms())
            elif new_presses:
                self._send("PRESS", round_number=r.round_number, tick=r.tick,
                           count=new_presses, client_time_ms=now_ms())

    # --- prediction, as the renderer and the benchmarks read it -----------------

    def authoritative(self, player_id):
        """Distance in the newest snapshot."""
        entry = self.round.cars.get(player_id)
        return entry["distance"] if entry else 0.0

    def predicted(self, player_id):
        """Where we think the car is right now. Ours: the local simulation.
        Others: the newest snapshot carried forward at its speed."""
        r = self.round
        if player_id == self.player_id:
            return r.car.distance
        entry = r.cars.get(player_id)
        if entry is None:
            return 0.0
        age_s = 0.0 if r.result is not None else (now_ms() - r.snapshot_at_ms) / 1000
        return min(r.track_length, entry["distance"] + entry["speed"] * age_s)

    def desync(self, player_id):
        return abs(self.predicted(player_id) - self.authoritative(player_id))

    def telemetry(self):
        """Everything the HUD panel shows, also what the benchmark records."""
        stats = self.transport.stats(self.peer)
        now = now_ms()
        while self.snapshot_times and now - self.snapshot_times[0] > 1000:
            self.snapshot_times.popleft()
        return {
            "rtt_ms": self.rtt_ms,
            "rtt_p50_ms": percentile(self.rtt_samples, 0.50),
            "rtt_p95_ms": percentile(self.rtt_samples, 0.95),
            "rto_ms": stats.get("rto_ms"),                      # None over TCP: the kernel's own
            "snapshots_per_s": len(self.snapshot_times),
            "packets_sent": stats.get("datagrams_sent", stats.get("messages_sent", 0)),
            "packets_received": stats.get("datagrams_received", stats.get("messages_received", 0)),
            "packets_lost": stats.get("datagrams_lost"),        # None over TCP: loss is hidden from us
            "up_bps": self.up_bps,
            "down_bps": self.down_bps,
            "ms_since_last_ack": stats.get("ms_since_last_ack"),  # None over TCP
            "stale_snapshots": self.stale_snapshots,
            "input_ack_ms": percentile(self.input_ack_ms, 0.50),
        }

    # --- incoming messages -------------------------------------------------------

    def _on_disconnect(self, peer, reason):
        self.lost = reason

    def _on_login_ok(self, message):
        self.player_id = message["player_id"]
        self.token = message["token"]
        self.transport.authenticate(self.peer, self.token)
        if self.clock_offset_ms is None:
            self.clock_offset_ms = message["server_time_ms"] - now_ms()  # rough, until the first PONG

    def _on_pong(self, message):
        now = now_ms()
        self.rtt_ms = now - message["t"]
        self.rtt_samples.append(self.rtt_ms)
        self.transport.rtt_sample(self.peer, self.rtt_ms)
        # The server stamped its clock halfway through the round trip (if the
        # path is symmetric). The sample with the shortest RTT has the least
        # room for error, so that one sets the offset.
        self._offset_samples.append((self.rtt_ms, message["server_time_ms"] + self.rtt_ms / 2 - now))
        self.clock_offset_ms = round(min(self._offset_samples)[1])

    def _on_error(self, message):
        self.error = message

    def _on_room_list(self, message):
        self.rooms = message["rooms"]

    def _on_room_state(self, message):
        self.room = message

    def _on_player_left(self, message):
        if message["player_id"] == self.player_id:
            self.room = self.round = self.match_result = None  # our own leave, confirmed
            return
        players = self.round.players if self.round else self.room["players"] if self.room else []
        name = next((p["name"] for p in players if p["id"] == message["player_id"]), "?")
        self.notices.append((now_ms(), f"{name} left ({message['reason']})"))

    def _on_match_start(self, message):
        self.round = Round(message)
        self.match_result = None
        self._press_log.clear()

    def _on_snapshot(self, message):
        r = self.round
        if r is None or message["round_number"] != r.round_number:
            return
        key = protocol.snapshot_key(message)
        if key <= r.newest_key:
            # Over UDP a snapshot can arrive after a newer one. Applying it would
            # move every car backwards, so it is dropped.
            self.stale_snapshots += 1
            return
        now = now_ms()
        r.newest_key = key
        r.snapshot_at_ms = now
        r.cars = {p["id"]: p for p in message["players"]}
        self.snapshot_times.append(now)

        mine = r.cars.get(self.player_id)
        if mine is None:
            return
        self.desync_samples.append(abs(r.car.distance - mine["distance"]))
        # Reconcile: the server is right. Pull the prediction part of the way
        # there on every snapshot, so a correction is a glide and not a jump.
        r.car.distance += (mine["distance"] - r.car.distance) * RECONCILE
        r.car.heat += (mine["heat"] - r.car.heat) * RECONCILE
        if mine["stalled"] and not r.car.stalled:
            r.car.stall_ticks = STALL_TICKS       # the server stalled us; we had not predicted it
        elif not mine["stalled"] and r.car.stalled and mine["heat"] < 50:
            r.car.stall_ticks = 0                 # the server's stall is over (or never happened)
        # How long did each key press take to show up in the authoritative state?
        while self._press_log and self._press_log[0][0] <= mine["presses"]:
            self.input_ack_ms.append(now - self._press_log.popleft()[1])

    def _on_round_end(self, message):
        if self.round is not None and message["round_number"] == self.round.round_number:
            self.round.result = message
            self.round.ended_at_ms = now_ms()

    def _on_match_end(self, message):
        self.match_result = message
