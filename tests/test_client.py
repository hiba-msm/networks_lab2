"""The headless client core against a real server, over both transports.

Run with:  python -m tests.test_client
"""

import threading
import time

from client.core import GameClient
from server.server import GameServer
from transport.tcp_transport import TcpTransport
from transport.udp_transport import UdpTransport

TRANSPORTS = {"tcp": TcpTransport, "udp": UdpTransport}


def run_until(clients, condition, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        for client in clients:
            client.update()
        time.sleep(0.002)


def test_three_clients_play_a_match(kind):
    listener = TRANSPORTS[kind].listen("127.0.0.1", 0)
    server = GameServer(listener, track_length=40.0, countdown_ms=600, intermission_ms=300)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        clients = [GameClient(TRANSPORTS[kind].connect("127.0.0.1", listener.address[1])) for _ in range(3)]
        ada, bob, cy = clients
        for client, name in zip(clients, ["ada", "bob", "cy"]):
            client.login(name)
        run_until(clients, lambda: all(c.player_id for c in clients))
        ada.create_room("core test")
        run_until(clients, lambda: ada.room)
        bob.join_room(ada.room["code"])
        cy.join_room(ada.room["code"].lower())
        run_until(clients, lambda: all(c.room and len(c.room["players"]) == 3 for c in clients))
        assert ada.room["creator_id"] == ada.player_id
        for client in clients:
            client.set_ready(True)
        run_until(clients, lambda: all(c.round for c in clients))

        # Clock sync: all three must turn the same server timestamp into (almost) the same instant.
        run_until(clients, lambda: all(len(c.rtt_samples) >= 2 for c in clients))
        offsets = [c.clock_offset_ms for c in clients]  # same machine, so same local clock
        assert max(offsets) - min(offsets) <= 5, f"clients disagree on the server clock: {offsets}"
        assert not ada.press(), "a press before the lights go out must not count"

        rates = {ada: 6, bob: 5, cy: 4}  # presses per second, all below the heat threshold
        pressed_at = {c: 0.0 for c in clients}

        def mash():
            now = time.monotonic()
            for c in clients:
                if now - pressed_at[c] >= 1 / rates[c] and c.press():
                    pressed_at[c] = now

        for round_number in (1, 2):
            run_until(clients, lambda: all(c.round.round_number == round_number for c in clients))
            run_until(clients, lambda: mash() or all(c.round.result for c in clients))
            for c in clients:
                assert c.round.result["finish_order"] == [ada.player_id, bob.player_id, cy.player_id]
        run_until(clients, lambda: all(c.match_result for c in clients))
        assert all(c.match_result["winner"] == ada.player_id for c in clients)

        for c in clients:
            telemetry = c.telemetry()
            # On loopback the prediction must sit close to the authoritative state...
            mean_desync = sum(c.desync_samples) / len(c.desync_samples)
            assert mean_desync < 1.5, f"{c.name}: mean desync {mean_desync:.2f} m on a clean link"
            # ...the car must have ended up on the line, and nothing may be stale or lost.
            assert abs(c.round.car.distance - 40.0) < 0.5 and c.authoritative(c.player_id) == 40.0
            assert c.stale_snapshots == 0 and not telemetry["packets_lost"]
            assert telemetry["rtt_p95_ms"] < 50 and telemetry["input_ack_ms"] < 150, telemetry
            assert c.lost is None and c.error is None

        cy.bye()
        run_until([ada, bob], lambda: ada.notices and bob.notices)
        assert ada.notices[-1][1] == "cy left (bye)"
        assert thread.is_alive(), "the server crashed"
    finally:
        server.running = False
        thread.join(timeout=2)
        listener.close()


if __name__ == "__main__":
    for kind in TRANSPORTS:
        started = time.monotonic()
        test_three_clients_play_a_match(kind)
        print(f"ok   {kind}  test_three_clients_play_a_match  ({time.monotonic() - started:.1f}s)")
