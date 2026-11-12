"""Balance check for the engine-heat mechanic: who wins, the masher or the careful driver?

    python -m tools.heat_sim                                  the values in common/constants.py
    python -m tools.heat_sim --stall-seconds 1.2              try a shorter stall
    python -m tools.heat_sim --stall-seconds 1.0 1.2 1.5 2.0  compare several

For each press rate it runs many solo rounds through the real rules
(common/game.py) with human-like timing jitter, and prints how often the car
stalls and how long the round takes. Candidate values are tried by overriding
the constants inside the game module for the run; constants.py is not edited.
"""

import argparse
import random

from common import constants, game
from common.constants import TICK_RATE, TRACK_LENGTH

RATES = (6, 8, 9, 10, 12)  # presses per second
ROUNDS = 200
JITTER = 0.35              # each gap between presses varies by +/-35%, like a real hand


def play_round(rate, seed):
    """One solo round at a steady (but jittery) press rate. Returns (stalls, finish seconds)."""
    rng = random.Random(seed)
    car = game.Car()
    tick = stalls = 0
    next_press = 0.0
    was_stalled = False
    while not car.finished:
        now = tick / TICK_RATE
        presses = 0
        while next_press <= now:
            presses += 1
            next_press += (1 / rate) * (1 + rng.uniform(-JITTER, JITTER))
        tick += 1
        car.step(presses, tick, TRACK_LENGTH)
        stalls += car.stalled and not was_stalled
        was_stalled = car.stalled
    return stalls, car.finish_time_ms / 1000


def table(stall_seconds, threshold, gain):
    """Returns {rate: (mean stalls per round, mean finish seconds)} for one set of values."""
    game.STALL_TICKS = round(stall_seconds * TICK_RATE)
    game.HEAT_THRESHOLD_RATE = threshold
    game.HEAT_GAIN = gain
    results = {}
    for rate in RATES:
        rounds = [play_round(rate, seed) for seed in range(ROUNDS)]
        results[rate] = (sum(r[0] for r in rounds) / ROUNDS, sum(r[1] for r in rounds) / ROUNDS)
    return results


def main():
    parser = argparse.ArgumentParser(description="Engine-heat balance simulation")
    parser.add_argument("--stall-seconds", type=float, nargs="+", default=[constants.STALL_TICKS / TICK_RATE])
    parser.add_argument("--threshold", type=float, default=constants.HEAT_THRESHOLD_RATE)
    parser.add_argument("--gain", type=float, default=constants.HEAT_GAIN)
    args = parser.parse_args()

    print(f"threshold {args.threshold} presses/s, heat gain {args.gain}, {ROUNDS} rounds per cell, "
          f"jitter +/-{JITTER:.0%}, track {TRACK_LENGTH:.0f} m")
    print("each cell: finish time in seconds (stalls per round)\n")
    print("stall    " + "".join(f"{rate:>3}/s          " for rate in RATES) + "fastest")
    for stall_seconds in args.stall_seconds:
        results = table(stall_seconds, args.threshold, args.gain)
        fastest = min(results, key=lambda rate: results[rate][1])
        cells = "".join(f"{seconds:5.2f} ({stalls:.2f})  " for stalls, seconds in results.values())
        print(f"{stall_seconds:.2f} s   {cells} {fastest}/s")


if __name__ == "__main__":
    main()
