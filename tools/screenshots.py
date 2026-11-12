"""Regenerate the screenshots in docs/screenshots/ (slide and report material).

    python -m tools.screenshots --transport udp
    python -m tools.screenshots --transport tcp
    python -m tools.screenshots --transport udp --loss 0.3     ghost cars pulling away
    python -m tools.screenshots --transport udp --skin broadcast

The committed set is those four runs. Delete docs/screenshots/*.png first, so
no file from an older build is left beside the new ones.

It plays one real match: a real server on 127.0.0.1, the real client window as
player "Ada", and two headless clients (client.core.GameClient) as opponents.
A window opens and the match runs in real time, about 40 seconds, silently.
Nine PNGs are written, one per screen. The name states the skin, the transport
and the screen:  <skin>_<transport>_<n>_<screen>.png, or
<skin>_udp_loss<percent>_<n>_<screen>.png with --loss.

--loss drops that share of the datagrams the server sends, by wrapping the UDP
transport's _transmit. It needs --transport udp: the TCP transport has no
equivalent hook yet.
"""

import argparse
import os
import pathlib
import random
import threading
import time

import pygame

from client.app import App
from client.core import GameClient
from server.server import GameServer
from transport.tcp_transport import TcpTransport
from transport.udp_transport import UdpTransport

TRANSPORTS = {"tcp": TcpTransport, "udp": UdpTransport}
MY_RATE = 6.8                      # presses per second for the windowed player
OPPONENTS = {"Bob": 6.2, "Cyrus": 7.6}

parser = argparse.ArgumentParser(description="Regenerate docs/screenshots")
parser.add_argument("--transport", choices=sorted(TRANSPORTS), required=True)
parser.add_argument("--loss", type=float, default=0.0, help="share of server datagrams to drop (udp only)")
parser.add_argument("--skin", choices=["arcade", "broadcast"], default="arcade")
parser.add_argument("--out", default="docs/screenshots")
args = parser.parse_args()
if args.loss and args.transport != "udp":
    parser.error("--loss needs --transport udp")

out = pathlib.Path(args.out)
out.mkdir(parents=True, exist_ok=True)
prefix = args.skin + "_" + (f"udp_loss{round(args.loss * 100)}" if args.loss else args.transport)
# Play the match's sound into SDL's dummy device: the run is silent, and the
# screens still show the default state (sound on) rather than a muted client.
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
transport_class = TRANSPORTS[args.transport]

listener = transport_class.listen("127.0.0.1", 0)
if args.loss:
    rng, send = random.Random(3), listener._transmit
    listener._transmit = lambda data, address: send(data, address) if rng.random() >= args.loss else None
server = GameServer(listener)
threading.Thread(target=server.run, daemon=True).start()
port = listener.address[1]

app = App(transport_class, args.transport.upper(), "127.0.0.1", port, skin=args.skin)
bots = {GameClient(transport_class.connect("127.0.0.1", port)): name for name in OPPONENTS}
last_press = {bot: 0.0 for bot in bots}
my_last_press = 0.0


def shot(name):
    pygame.image.save(app.window, str(out / f"{prefix}_{name}.png"))
    print("wrote", out / f"{prefix}_{name}.png")


def spin(seconds=30.0, until=None, mash=False):
    """Keep the window and the opponents running for `seconds`, or until the
    condition holds. mash=True makes all three players press at their rates."""
    global my_last_press
    end = time.monotonic() + seconds
    while time.monotonic() < end and not (until and until()):
        now = time.monotonic()
        if mash:
            for bot, name in bots.items():
                if now - last_press[bot] > 1 / OPPONENTS[name] and bot.press():
                    last_press[bot] = now
            if now - my_last_press > 1 / MY_RATE and app.client.press():
                my_last_press = now
        app.frame()
        next_frame = time.monotonic() + 1 / 60
        while time.monotonic() < next_frame:  # the same between-frame pumping as App.run()
            if app.client is not None:
                app.client.update()
            for bot in bots:
                bot.update()
            time.sleep(0.001)


bob, cyrus = bots
spin(0.3)
shot("1_name_empty")
app.name_field.type("Ada")
app.connect("127.0.0.1", port)
for bot, name in bots.items():
    bot.login(name)
spin(until=lambda: app.client.player_id and all(bot.player_id for bot in bots))
cyrus.create_room("Practice")  # so the lobby has a room to list
spin(2.5)
shot("2_lobby")
cyrus.leave_room()
spin(0.3)
app.join("")                   # empty field: create a room
spin(until=lambda: app.client.room)
for bot in bots:
    bot.join_room(app.client.room["code"])
spin(until=lambda: len(app.client.room["players"]) == 3)
bob.set_ready(True)
spin(0.6)
shot("3_room")
cyrus.set_ready(True)
app.client.set_ready(True)     # three ready players: the match starts by itself
spin(until=lambda: app.client.round)
spin(1.6)
shot("4_lights")
spin(until=lambda: app.client.server_now_ms() > app.client.round.start_at_ms)
spin(4.0, mash=True)
shot("5_race")
spin(3.0, mash=True)
shot("6_race_late")
spin(until=lambda: app.client.round.result, mash=True)
spin(1.2)
shot("7_finish")
while not app.client.match_result:
    number = app.client.round.round_number
    spin(until=lambda: app.client.round.round_number != number or app.client.match_result)
    spin(until=lambda: app.client.round.result or app.client.match_result, mash=True)
spin(3.2)
shot("8_result")
pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r"))
spin(0.5)
shot("9_back_in_room")

server.running = False
pygame.quit()
