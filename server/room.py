"""One room: who is in it, and the match it is running.

States:  LOBBY -> COUNTDOWN -> RACING -> INTERMISSION -> COUNTDOWN ... -> LOBBY
A match is up to three rounds. MATCH_START opens every round.
"""

import logging

from common.constants import MIN_PLAYERS_TO_START, ROOM_SIZE
from common.game import Match, Race

log = logging.getLogger("server.room")

LOBBY, COUNTDOWN, RACING, INTERMISSION = "lobby", "countdown", "racing", "intermission"


class Room:
    def __init__(self, server, room_id, code, name, creator):
        self.server = server
        self.room_id = room_id
        self.code = code
        self.name = name
        self.creator = creator  # the only player allowed to send START_MATCH
        self.players = []       # Player objects in join order
        self.state = LOBBY
        self.match = None
        self.race = None
        self.round_number = 0
        self.deadline_ms = 0    # server time at which COUNTDOWN or INTERMISSION ends

    def broadcast(self, message):
        for player in self.players:
            self.server.send(player, message)

    def broadcast_state(self):
        self.broadcast({
            "type": "ROOM_STATE", "room_id": self.room_id, "code": self.code, "room_name": self.name,
            "creator_id": self.creator.player_id,
            "players": [{"id": p.player_id, "name": p.name, "ready": p.ready} for p in self.players],
        })

    # --- membership ----------------------------------------------------------

    def add(self, player):
        player.room = self
        player.ready = False
        self.players.append(player)
        self.broadcast_state()

    def remove(self, player, reason):
        """Used for LEAVE_ROOM, BYE and every kind of disconnect alike."""
        # Broadcast before removing: the leaver's own copy is its confirmation.
        self.broadcast({"type": "PLAYER_LEFT", "player_id": player.player_id, "reason": reason})
        self.players.remove(player)
        player.room = None
        player.ready = False
        if not self.players:
            return  # the server deletes an empty room
        if player is self.creator:
            self.creator = self.players[0]  # the longest-standing member takes over
        if self.state == LOBBY:
            self.broadcast_state()
            return
        # Mid-match: the race carries on without them.
        self.match.remove_player(player.player_id)
        self.race.remove_player(player.player_id)
        if self.match.is_over:  # only one player left
            self.end_match()

    def set_ready(self, player, ready):
        player.ready = ready
        self.broadcast_state()
        if len(self.players) == ROOM_SIZE and self.everyone_ready():
            self.start_match()

    # --- match ---------------------------------------------------------------

    def everyone_ready(self):
        return len(self.players) >= MIN_PLAYERS_TO_START and all(p.ready for p in self.players)

    def start_match(self):
        self.match = Match([p.player_id for p in self.players])
        self.round_number = 0
        log.info("room %s: match started with %d players", self.code, len(self.players))
        self.start_round()

    def start_round(self):
        self.round_number += 1
        self.race = Race([p.player_id for p in self.players], self.server.track_length)
        self.state = COUNTDOWN
        self.deadline_ms = self.server.now_ms() + self.server.countdown_ms
        # Every client gets the same future timestamp, so the start lights are
        # in sync no matter when each copy of this message arrives.
        self.broadcast({
            "type": "MATCH_START", "round_number": self.round_number,
            "start_at_server_time_ms": self.deadline_ms, "track_length": self.server.track_length,
            "players": [{"id": p.player_id, "name": p.name} for p in self.players],
        })

    def on_input(self, player, message):
        # Input outside a running round, or tagged with another round, is a late
        # or early packet. Dropping it is correct, not an error.
        if self.state != RACING or message["round_number"] != self.round_number:
            return
        if message["type"] == "INPUT":
            self.race.report_cumulative(player.player_id, message["cumulative_presses"])
        else:
            self.race.report_events(player.player_id, message["count"])

    def tick(self, now_ms):
        """Called by the server 30 times a second."""
        if self.state == COUNTDOWN and now_ms >= self.deadline_ms:
            self.state = RACING
        elif self.state == RACING:
            self.race.step()
            self.broadcast({
                "type": "SNAPSHOT", "round_number": self.round_number, "tick": self.race.tick,
                "server_time_ms": now_ms, "players": self.race.snapshot_players(),
            })
            if self.race.is_over:
                self.end_round(now_ms)
        elif self.state == INTERMISSION and now_ms >= self.deadline_ms:
            self.start_round()

    def end_round(self, now_ms):
        order = self.race.finish_order()
        self.broadcast({
            "type": "ROUND_END", "round_number": self.round_number, "finish_order": order,
            "times_ms": [self.race.time_ms(pid) for pid in order],
        })
        self.match.record_round(self.race)
        if self.match.is_over:
            self.end_match()
        else:
            self.state = INTERMISSION
            self.deadline_ms = now_ms + self.server.intermission_ms

    def end_match(self):
        ranking = self.match.ranking()
        log.info("room %s: match over, winner is player %s", self.code, ranking[0])
        self.broadcast({"type": "MATCH_END", "final_ranking": ranking,
                        "scores": self.match.scores(), "winner": ranking[0]})
        self.state = LOBBY
        self.match = None
        self.race = None
        for player in self.players:
            player.ready = False
        self.broadcast_state()
