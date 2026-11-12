"""Shared constants. Server, client, bots and benchmarks all import from here,
so the TCP and UDP versions cannot drift apart on a tuning value."""

# --- Network ---------------------------------------------------------------
DEFAULT_PORT = 5055          # same number for TCP and UDP (5000 clashes with macOS AirPlay)
MAX_DATAGRAM_BYTES = 1200    # stays under typical internet path MTU, so no IP fragmentation
MAX_PAYLOAD_BYTES = 1100     # one encoded message; leaves 100 bytes for the UDP reliability header
HEARTBEAT_INTERVAL_MS = 250  # also keeps NAT mappings alive for UDP clients
PEER_TIMEOUT_MS = 2000       # no traffic for this long means the peer is gone

# --- Simulation ------------------------------------------------------------
TICK_RATE = 30               # server simulation ticks per second
TICK_DT = 1.0 / TICK_RATE    # seconds per tick
TRACK_LENGTH = 200.0         # world units (metres); about 11 s at 6 presses/second

PRESS_WINDOW_TICKS = 15      # sliding window for press rate: 15 ticks = 500 ms
PRESS_WINDOW_SECONDS = PRESS_WINDOW_TICKS * TICK_DT
SPEED_PER_PRESS_RATE = 3.0   # metres/second gained per press/second
MAX_PRESS_RATE = 20.0        # presses/second; anything faster is not a human, so it is clamped

# Tuned by simulation with human-like timing jitter: 9-10 presses/second stalls
# once per round, 8 rarely (15% of rounds), 7 and below never.
HEAT_THRESHOLD_RATE = 6.5    # presses/second above which the engine heats up
HEAT_GAIN = 8.0              # heat % per second, per press/second above the threshold
HEAT_COOL = 15.0             # heat % per second lost while at or below the threshold
HEAT_MAX = 100.0
# 1.2 s, chosen with tools/heat_sim.py: mashing at 10 presses/second into one
# stall then beats a careful 8/s by about half a second. At 2 s the careful
# driver always won, so the best strategy was not to mash.
STALL_TICKS = round(1.2 * TICK_RATE)  # 36 ticks; heat drains to 0 over the stall

# --- Match -----------------------------------------------------------------
ROOM_SIZE = 3
MIN_PLAYERS_TO_START = 2         # a full room starts by itself; fewer need START_MATCH from the creator
ROUNDS_PER_MATCH = 3
ROUND_WINS_TO_WIN = 2             # best of 3
ROUND_POINTS = (3, 2, 1)          # points for 1st, 2nd, 3rd in a round (tie-breaker)
ROUND_TIMEOUT_TICKS = 60 * TICK_RATE   # a round never runs longer than 60 s
FINISH_GRACE_TICKS = 5 * TICK_RATE    # after the first finisher, the rest get 5 s
COUNTDOWN_MS = 4000              # MATCH_START is sent this long before the lights go out
INTERMISSION_MS = 3000           # pause on the result between rounds
ROOM_LIST_MAX = 10               # keeps ROOM_LIST inside one datagram
SESSION_KEEP_MS = 60_000         # after a disconnect, name and player_id stay reserved this long

# --- Limits on client-supplied strings --------------------------------------
MAX_NAME_LEN = 16
MAX_ROOM_NAME_LEN = 24
