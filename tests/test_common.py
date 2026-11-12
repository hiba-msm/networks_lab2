"""Phase 1 checks: protocol and game rules. Run with:  python -m tests.test_common"""

import random

from common import protocol
from common.constants import (
    FINISH_GRACE_TICKS, ROUND_TIMEOUT_TICKS, MAX_PAYLOAD_BYTES, SPEED_PER_PRESS_RATE,
    STALL_TICKS, TICK_RATE, TRACK_LENGTH,
)
from common.game import Match, Race
from common.protocol import ProtocolError, decode, encode

TOKEN = "00112233445566778899aabbccddeeff"

# One valid example of every message type.
SAMPLES = [
    {"type": "LOGIN", "name": "ada", "token": ""},
    {"type": "LIST_ROOMS", "token": TOKEN},
    {"type": "CREATE_ROOM", "room_name": "lab", "token": TOKEN},
    {"type": "JOIN_ROOM", "code": "ABC234", "token": TOKEN},
    {"type": "LEAVE_ROOM", "token": TOKEN},
    {"type": "READY", "ready": True, "token": TOKEN},
    {"type": "START_MATCH", "token": TOKEN},
    {"type": "INPUT", "round_number": 1, "tick": 7, "cumulative_presses": 42, "client_time_ms": 1000, "token": TOKEN},
    {"type": "PRESS", "round_number": 1, "tick": 7, "count": 2, "client_time_ms": 1000, "token": TOKEN},
    {"type": "PING", "t": 5, "rtt_ms": 0, "token": TOKEN},
    {"type": "BYE", "token": TOKEN},
    {"type": "LOGIN_OK", "player_id": 1, "token": TOKEN, "server_time_ms": 99},
    {"type": "ROOM_LIST", "rooms": []},
    {"type": "ROOM_STATE", "room_id": 1, "code": "ABC234", "room_name": "lab", "creator_id": 1,
     "players": [{"id": 1, "name": "ada", "ready": False}]},
    {"type": "MATCH_START", "round_number": 1, "start_at_server_time_ms": 5000,
     "track_length": 200.0, "players": [{"id": 1, "name": "ada"}]},
    {"type": "SNAPSHOT", "round_number": 1, "tick": 3, "server_time_ms": 5100, "players": []},
    {"type": "ROUND_END", "round_number": 1, "finish_order": [1, 2], "times_ms": [15000.5, None]},
    {"type": "MATCH_END", "final_ranking": [1, 2], "scores": [], "winner": 1},
    {"type": "PLAYER_LEFT", "player_id": 2, "reason": "timeout"},
    {"type": "PONG", "t": 5, "server_time_ms": 99},
    {"type": "ERROR", "code": protocol.ERR_ROOM_FULL, "detail": "room is full"},
]


def rejects(data):
    try:
        decode(data)
    except ProtocolError:
        return True
    return False


def test_every_message_round_trips():
    assert {m["type"] for m in SAMPLES} == set(protocol.MESSAGES)
    for msg in SAMPLES:
        assert decode(encode(msg)) == msg


def test_every_message_is_classified():
    assert protocol.RELIABLE | protocol.UNRELIABLE == set(protocol.MESSAGES)
    assert not protocol.RELIABLE & protocol.UNRELIABLE
    assert protocol.is_reliable("MATCH_START") and not protocol.is_reliable("SNAPSHOT")


def test_bad_messages_are_rejected():
    assert rejects(b"\xff\xfe not json")
    assert rejects(b"[1,2,3]")
    assert rejects(b'{"type":"NOPE"}')
    assert rejects(b'{"name":"ada"}')                                   # no type
    assert rejects(b'{"type":"LOGIN","name":"ada"}')                    # missing field (token)
    assert rejects(b'{"type":"LOGIN","name":"ada","token":"","admin":true}')  # extra field
    assert rejects(b'{"type":"LOGIN","name":5,"token":""}')             # wrong type
    assert rejects(b'{"type":"PING","t":true,"rtt_ms":0,"token":""}')   # bool is not a number
    assert rejects(b'{"type":"PRESS","round_number":1,"tick":1,"count":-5,"client_time_ms":0,"token":""}')
    assert rejects(b'{"type":"LOGIN","token":"","name":"' + b"a" * MAX_PAYLOAD_BYTES + b'"}')


def test_full_snapshot_fits_in_one_datagram():
    race = Race([101, 102, 103])
    for pid in race.cars:  # worst-case digit counts
        race.cars[pid].distance, race.cars[pid].speed, race.cars[pid].presses = 199.99, 59.99, 99999
    snap = {"type": "SNAPSHOT", "round_number": 3, "tick": ROUND_TIMEOUT_TICKS,
            "server_time_ms": 10**13, "players": race.snapshot_players()}
    assert len(encode(snap)) < MAX_PAYLOAD_BYTES / 2


def test_token_can_travel_outside_the_json():
    msg = {"type": "READY", "ready": True, "token": TOKEN}
    wire = encode(msg, omit_token=True)
    assert b"token" not in wire and rejects(wire)       # incomplete without the transport's help
    assert decode(wire, token=TOKEN) == msg
    forged = encode({**msg, "token": "f" * 32})
    assert decode(forged, token=TOKEN) == msg           # the authenticated token always wins
    login = {"type": "LOGIN", "name": "ada", "token": "f" * 32}
    assert decode(encode(login), token=TOKEN) == login  # LOGIN's token is data, left alone


def test_snapshot_key_orders_across_rounds():
    key = lambda r, t: protocol.snapshot_key({"round_number": r, "tick": t})
    assert key(1, 5) < key(1, 6) < key(2, 1)


def test_room_codes_and_join_strings():
    for _ in range(200):
        code = protocol.make_room_code()
        assert protocol.is_room_code(code) and not set(code) & set("0O1IL")
    assert protocol.parse_join_string(" 203.0.113.7:5055:abc234 ") == ("203.0.113.7", 5055, "ABC234")
    assert protocol.parse_join_string("2001:db8::1:5055:ABC234") == ("2001:db8::1", 5055, "ABC234")
    assert protocol.parse_join_string(protocol.format_join_string("h", 1, "ABC234")) == ("h", 1, "ABC234")
    for bad in ["", "host:5055", "host:99999:ABC234", "host:port:ABC234", "host:5055:ABC10O", ":5055:ABC234"]:
        try:
            protocol.parse_join_string(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def run(race, presses_at, ticks=None):
    """Step a race; presses_at(pid, tick) returns that player's new presses for the tick."""
    totals = {pid: 0 for pid in race.cars}
    while not race.is_over and (ticks is None or race.tick < ticks):
        for pid in race.cars:
            totals[pid] += presses_at(pid, race.tick)
            race.report_cumulative(pid, totals[pid])
        race.step()


def steady(rate):
    """A press pattern of `rate` presses per second, evenly spaced."""
    return lambda pid, tick: (tick + 1) * rate // TICK_RATE - tick * rate // TICK_RATE


def test_steady_pressing_finishes_on_time_without_stalling():
    race = Race([1])
    run(race, steady(6))
    car = race.cars[1]
    expected_ms = TRACK_LENGTH / (6 * SPEED_PER_PRESS_RATE) * 1000
    assert car.finished and car.heat == 0.0
    assert abs(car.finish_time_ms - expected_ms) < 500  # the window takes 0.5 s to fill


def test_mashing_overheats_and_stalls_for_the_stall_time():
    assert STALL_TICKS / TICK_RATE == 1.2, "a stall is 1.2 seconds"
    race = Race([1])
    total = 0
    while not race.cars[1].stalled:
        total += steady(12)(1, race.tick)
        race.report_cumulative(1, total)
        race.step()
        assert race.tick < 10 * TICK_RATE, "never overheated"
    stalled_at = race.cars[1].distance
    for _ in range(STALL_TICKS):
        assert race.cars[1].stalled and race.cars[1].speed == 0.0
        race.step()
    assert not race.cars[1].stalled
    assert race.cars[1].distance == stalled_at and race.cars[1].heat < 0.001


def test_cumulative_input_survives_packet_loss_but_events_do_not():
    rng = random.Random(1)
    clean, lossy_cumulative, lossy_events = Race([1]), Race([1]), Race([1])
    total = 0
    for tick in range(10 * TICK_RATE):
        new = steady(6)(1, tick)
        total += new
        delivered = rng.random() >= 0.3 or tick == 10 * TICK_RATE - 1  # 30% loss
        clean.report_cumulative(1, total)
        if delivered:
            lossy_cumulative.report_cumulative(1, total)
            lossy_events.report_events(1, new)
        for race in (clean, lossy_cumulative, lossy_events):
            race.step()
    assert lossy_cumulative.cars[1].presses == clean.cars[1].presses == total
    assert abs(lossy_cumulative.cars[1].distance - clean.cars[1].distance) < 3.0
    assert lossy_events.cars[1].presses < total * 0.8
    assert lossy_events.cars[1].distance < clean.cars[1].distance * 0.8


def test_duplicate_and_reordered_input_is_harmless():
    race = Race([1])
    for total in (5, 3, 5, 4):
        race.report_cumulative(1, total)
    race.step()
    assert race.cars[1].presses == 5


def test_identical_inputs_give_identical_results():
    race = Race([1, 2, 3])
    run(race, steady(6))
    times = {race.cars[pid].finish_time_ms for pid in race.cars}
    assert len(times) == 1 and None not in times


def test_finish_order_grace_period_and_leaving():
    race = Race([1, 2, 3])
    rates = {1: steady(5), 2: steady(6), 3: steady(0)}  # player 3 never presses
    run(race, lambda pid, tick: rates[pid](pid, tick))
    assert race.finish_order() == [2, 1, 3]
    assert race.time_ms(2) < race.time_ms(1) and race.time_ms(3) is None
    assert race.tick <= race.first_finish_tick + FINISH_GRACE_TICKS

    race = Race([1, 2, 3])
    run(race, steady(6), ticks=30)
    race.remove_player(2)
    run(race, steady(6))
    assert race.is_over and race.finish_order() == [1, 3]
    assert [p["id"] for p in race.snapshot_players()] == [1, 3]


def played_round(winner_rates):
    race = Race(list(winner_rates))
    run(race, lambda pid, tick: steady(winner_rates[pid])(pid, tick))
    return race


def test_match_ends_early_on_two_wins():
    match = Match([1, 2, 3])
    match.record_round(played_round({1: 6, 2: 5, 3: 4}))
    assert not match.is_over
    match.record_round(played_round({1: 6, 2: 5, 3: 4}))
    assert match.is_over and match.ranking() == [1, 2, 3]
    assert match.scores()[0] == {"id": 1, "wins": 2, "points": 6}


def test_match_three_way_tie_is_broken_by_total_time():
    match = Match([1, 2, 3])
    match.record_round(played_round({1: 6, 2: 5, 3: 4}))
    match.record_round(played_round({1: 4, 2: 6, 3: 5}))
    assert not match.is_over
    match.record_round(played_round({1: 5, 2: 5, 3: 6}))
    assert match.is_over and match.wins == {1: 1, 2: 1, 3: 1}
    assert match.points == {1: 6, 2: 6, 3: 6}
    # player 2 is the only one who finished every round inside the grace period
    assert match.ranking()[0] == 2


def test_match_ends_when_one_player_remains():
    match = Match([1, 2, 3])
    match.remove_player(2)
    assert not match.is_over
    match.remove_player(3)
    assert match.is_over and match.ranking() == [1]


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"{len(tests)} passed")
