"""The game simulation. The ONE implementation of the rules.

The server steps a Race to produce the authoritative state; the client steps a
Car with its own key presses to predict its position between snapshots. This
module is pure: no sockets, no clocks, no randomness. Time only advances when
step() is called, so the same inputs always give the same race.
"""

from collections import deque

from common.constants import (
    FINISH_GRACE_TICKS, HEAT_COOL, HEAT_GAIN, HEAT_MAX, ROUND_POINTS,
    HEAT_THRESHOLD_RATE, ROUND_TIMEOUT_TICKS, ROUND_WINS_TO_WIN, ROUNDS_PER_MATCH,
    MAX_PRESS_RATE, PRESS_WINDOW_SECONDS, PRESS_WINDOW_TICKS,
    SPEED_PER_PRESS_RATE, STALL_TICKS, TICK_DT, TRACK_LENGTH,
)


class Car:
    def __init__(self):
        self.distance = 0.0
        self.speed = 0.0
        self.heat = 0.0             # engine heat, 0..100
        self.presses = 0            # total presses counted so far
        self.stall_ticks = 0        # ticks of stall remaining; 0 = running
        self.finish_time_ms = None  # race time at the line, None until finished
        # presses seen in each of the last PRESS_WINDOW_TICKS ticks
        self.window = deque([0] * PRESS_WINDOW_TICKS, maxlen=PRESS_WINDOW_TICKS)

    @property
    def stalled(self):
        return self.stall_ticks > 0

    @property
    def finished(self):
        return self.finish_time_ms is not None

    def step(self, new_presses, tick, track_length):
        """Advance one tick. `tick` is the tick being computed (first tick is 1)."""
        if self.finished:
            return
        self.presses += new_presses
        self.window.append(new_presses)
        rate = min(sum(self.window) / PRESS_WINDOW_SECONDS, MAX_PRESS_RATE)

        if self.stalled:
            self.stall_ticks -= 1
            self.heat = max(0.0, self.heat - HEAT_MAX / STALL_TICKS)
            self.speed = 0.0
            return

        if rate > HEAT_THRESHOLD_RATE:
            self.heat += HEAT_GAIN * (rate - HEAT_THRESHOLD_RATE) * TICK_DT
        else:
            self.heat = max(0.0, self.heat - HEAT_COOL * TICK_DT)
        if self.heat >= HEAT_MAX:
            self.heat = HEAT_MAX
            self.stall_ticks = STALL_TICKS
            self.speed = 0.0
            return

        self.speed = rate * SPEED_PER_PRESS_RATE
        advance = self.speed * TICK_DT
        remaining = track_length - self.distance
        if advance > 0 and advance >= remaining:
            # Interpolate inside the tick, so finish times are finer than the
            # 33 ms tick and two cars crossing in the same tick are still ordered.
            self.finish_time_ms = (tick - 1 + remaining / advance) * TICK_DT * 1000.0
            self.distance = track_length
        else:
            self.distance += advance


class Race:
    """One round."""

    def __init__(self, player_ids, track_length=TRACK_LENGTH):
        self.track_length = track_length
        self.tick = 0
        self.cars = {pid: Car() for pid in player_ids}
        self.reported = {pid: 0 for pid in player_ids}  # press total each client has told us
        self.first_finish_tick = None

    def report_cumulative(self, pid, total):
        """INPUT message. max() makes duplicated and reordered packets harmless."""
        self.reported[pid] = max(self.reported[pid], total)

    def report_events(self, pid, count):
        """PRESS message (--event-mode). A packet that never arrives is never counted."""
        self.reported[pid] += count

    def remove_player(self, pid):
        """A player left mid-round. The race carries on without them."""
        del self.cars[pid]
        del self.reported[pid]

    def step(self):
        self.tick += 1
        for pid, car in self.cars.items():
            car.step(self.reported[pid] - car.presses, self.tick, self.track_length)
            if car.finished and self.first_finish_tick is None:
                self.first_finish_tick = self.tick

    @property
    def is_over(self):
        if all(car.finished for car in self.cars.values()):  # also true with no cars left
            return True
        if self.tick >= ROUND_TIMEOUT_TICKS:
            return True
        return (self.first_finish_tick is not None
                and self.tick - self.first_finish_tick >= FINISH_GRACE_TICKS)

    def finish_order(self):
        """Player ids, best first: finishers by time, then the rest by distance."""
        def key(pid):
            car = self.cars[pid]
            if car.finished:
                return (0, car.finish_time_ms, pid)
            return (1, -car.distance, pid)
        return sorted(self.cars, key=key)

    def time_ms(self, pid):
        """Finish time rounded for the wire, or None."""
        t = self.cars[pid].finish_time_ms
        return None if t is None else round(t, 1)

    def snapshot_players(self):
        """The `players` field of a SNAPSHOT message."""
        return [
            {"id": pid, "distance": round(car.distance, 2), "speed": round(car.speed, 2),
             "heat": round(car.heat, 1), "presses": car.presses, "stalled": car.stalled}
            for pid, car in self.cars.items()
        ]


class Match:
    """Best of three rounds. Ranking: round wins, then points, then total time."""

    def __init__(self, player_ids):
        self.rounds_played = 0
        self.wins = {pid: 0 for pid in player_ids}
        self.points = {pid: 0 for pid in player_ids}
        self.total_ms = {pid: 0.0 for pid in player_ids}

    def record_round(self, race):
        self.rounds_played += 1
        order = [pid for pid in race.finish_order() if pid in self.wins]
        for place, pid in enumerate(order):
            car = race.cars[pid]
            self.points[pid] += ROUND_POINTS[place] if place < len(ROUND_POINTS) else 0
            # a player who did not finish is charged the full round timeout
            self.total_ms[pid] += car.finish_time_ms if car.finished else ROUND_TIMEOUT_TICKS * TICK_DT * 1000.0
        if order and race.cars[order[0]].finished:
            self.wins[order[0]] += 1

    def remove_player(self, pid):
        del self.wins[pid], self.points[pid], self.total_ms[pid]

    @property
    def is_over(self):
        return (self.rounds_played >= ROUNDS_PER_MATCH
                or len(self.wins) < 2                      # nobody left to race against
                or any(w >= ROUND_WINS_TO_WIN for w in self.wins.values()))

    def ranking(self):
        return sorted(self.wins, key=lambda pid: (-self.wins[pid], -self.points[pid], self.total_ms[pid], pid))

    def scores(self):
        """The `scores` field of MATCH_END, in ranking order."""
        return [{"id": pid, "wins": self.wins[pid], "points": self.points[pid]} for pid in self.ranking()]
