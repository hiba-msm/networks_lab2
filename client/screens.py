"""What each screen looks like, once per skin. Pure drawing: reads the app and
the client core, changes nothing in either. The first half of the file is the
"broadcast" skin (draw_*): a motorsport timing page on a fixed grid with a
48 px margin. The second half is the "arcade" skin (arcade_*): pixel art, see
docs/design.md. The window is 1280 x 720 in both.
"""

import math
import random

import pygame
import qrcode

from client.core import now_ms
from client.theme import (ACCENTS, BG, BLACK, BLUE, BLUE_LIGHT, CAR_SPRITES, DIM, FAINT, FG, GREEN,
                          GREEN_LIGHT, HAIR, ICE, INK, PAGE, PANEL, PLAYER_COLORS, PX, RED, RED_LIGHT,
                          SLATE, W, WHITE, YELLOW_LIGHT, mix, sprite)
from common import protocol
from common.constants import HEAT_MAX, PRESS_WINDOW_SECONDS, ROOM_SIZE, ROUNDS_PER_MATCH

M = 48                    # page margin
RIGHT = W - M
TRACK_X0, TRACK_X1 = 264, RIGHT   # the scrolling part of the lanes
TRACK_W = TRACK_X1 - TRACK_X0
LANE_TOP, LANE_H = 116, 112
PPM = 11.0                # pixels per metre of track
ANCHOR = TRACK_W * 0.6    # where the leader sits, measured from the left of the track
LIGHT_STEP_MS = 700       # one start light goes out every 700 ms; the last one at the start


def header(c, app, caption, title):
    """Top strip shared by every screen. Left: where we are. Right: the link."""
    c.label(caption, M, 44)
    c.text(title, M, 78, 26)
    link(c, app)
    c.hline(M, 100, W - 2 * M)


def link(c, app):
    """Top right: transport and server address; under it our name, or the latest
    notice (a player leaving) for four seconds."""
    width = c.number(f"{app.host}:{app.port}", RIGHT, 44, 11, DIM, align="right")
    c.label(app.transport_label, RIGHT - width - 12, 44, align="right")
    client = app.client
    notice = client.notices[-1] if client and client.notices else None
    if notice and now_ms() - notice[0] < 4000:
        c.label(notice[1], RIGHT, 74, FG, 12, align="right")
    elif client and client.name and client.player_id:
        c.label(client.name, RIGHT, 74, FG, 12, align="right")


def hints(c, pairs):
    """Bottom strip: key in mono, what it does as a label."""
    x = M
    for key, action in pairs:
        x += c.number(key, x, 688, 13) + 10
        x += c.label(action, x, 688) + 36


def field(c, text, x, y, size, placeholder=""):
    """A one-line text field: the text, a blinking caret, a rule under it."""
    width = c.number(text, x, y, size) if text else 0
    if not text:
        c.label(placeholder, x + 14, y - size * 0.22, DIM, 13)
    if now_ms() // 500 % 2 == 0:
        c.rect(x + width + 3, y - size * 0.78, 2, size * 0.92, FG)
    c.hline(x, y + 18, 620, DIM)


def message_line(c, app, y):
    """The server's last ERROR, or a local problem, in plain words."""
    if app.message and now_ms() - app.message[0] < 6000:
        c.label(app.message[1], M, y, FG, 12)


# --- before the race -------------------------------------------------------------

def draw_name(c, app):
    header(c, app, "Drag race", "Driver sign-in")
    c.label("Driver name", M, 250)
    field(c, app.name_field.text, M, 320, 44, "type your name")
    message_line(c, app, 380)
    hints(c, [("ENTER", "connect"), ("ESC", "quit")])


def draw_lobby(c, app):
    header(c, app, "Lobby", "Join or create a room")
    c.label("Room code, or a pasted invite  host:port:code", M, 250)
    field(c, app.join_field.text, M, 320, 44, "empty = create a new room")
    message_line(c, app, 380)

    x = 760
    c.label("Open rooms", x, 250)
    c.hline(x, 264, RIGHT - x)
    if not app.client.rooms:
        c.label("none yet", x, 296)
    for i, room in enumerate(app.client.rooms[:8]):
        y = 300 + i * 44
        c.number(room["code"], x, y, 20)
        c.text(room["room_name"], x + 120, y, 16, DIM)
        if room["in_match"]:
            c.label("racing", RIGHT, y, align="right")
        else:
            c.number(f"{room['player_count']}/{ROOM_SIZE}", RIGHT, y, 14, align="right")
        c.hline(x, y + 16, RIGHT - x)
    hints(c, [("ENTER", "join / create"), ("CTRL+V", "paste invite"), ("ESC", "sign out")])


_qr_cache = {}


def qr_surface(text, size, dark=FG, light=BG, border=0):
    """The invite as a QR code, at most size pixels square; every module is a
    whole number of pixels. The broadcast skin draws it light-on-dark with no
    border (the dark page is the quiet zone); the arcade skin draws it
    dark-on-white with the standard 4-module quiet zone, which every scanner reads."""
    key = (text, size, dark, light, border)
    if key not in _qr_cache:
        code = qrcode.QRCode(border=border, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data(text)
        code.make()
        modules = code.get_matrix()  # includes the border
        module = size // len(modules)
        surface = pygame.Surface((module * len(modules),) * 2)
        surface.fill(light)
        for row, line in enumerate(modules):
            for column, on in enumerate(line):
                if on:
                    surface.fill(dark, (column * module, row * module, module, module))
        _qr_cache[key] = surface
    return _qr_cache[key]


def draw_room(c, app):
    client, room = app.client, app.client.room
    header(c, app, "Room", room["room_name"])
    invite = protocol.format_join_string(app.host, app.port, room["code"])

    c.label("Room code", M, 160)
    c.number(room["code"], M - 4, 270, 104)
    c.blit(qr_surface(invite, 200 * c.s), M, 330)
    c.label("Scan or paste to join", M + 232, 348)
    c.number(invite, M + 232, 378, 16)
    c.label("CTRL+C copies the invite", M + 232, 410)

    x = 760
    c.label("Grid", x, 160)
    players = room["players"]
    for slot in range(ROOM_SIZE):
        y = 176 + slot * 96
        c.hline(x, y, RIGHT - x)
        if slot >= len(players):
            c.label("open", x + 20, y + 54)
            continue
        player = players[slot]
        c.rect(x, y + 24, 3, 48, ACCENTS[slot])
        c.text(player["name"], x + 20, y + 58, 26)
        tags = [tag for tag, on in (("host", player["id"] == room["creator_id"]),
                                    ("you", player["id"] == client.player_id)) if on]
        c.label("  ".join(tags), x + 20, y + 80)
        c.label("ready" if player["ready"] else "not ready", RIGHT, y + 54,
                FG if player["ready"] else DIM, 12, align="right")
    c.hline(x, 176 + ROOM_SIZE * 96, RIGHT - x)

    everyone_ready = len(players) >= 2 and all(p["ready"] for p in players)
    if len(players) < 2:
        status = "waiting for another driver"
    elif not everyone_ready:
        status = "waiting for everyone to be ready"
    elif room["creator_id"] == client.player_id:
        status = "press ENTER to start with two drivers"
    else:
        status = "waiting for the host to start"
    c.label(status, x, 500, FG, 12)
    message_line(c, app, 600)
    hints(c, [("SPACE", "ready"), ("ENTER", "start (host)"), ("ESC", "leave")])


# --- the race ----------------------------------------------------------------------

def car_shape(nose_x, y):
    """A long flat body with a tapered nose. No wheels, no drawing of a car."""
    length, half, taper = 64, 8, 14
    return [(nose_x - length, y - half), (nose_x - taper, y - half), (nose_x, y - 3),
            (nose_x, y + 3), (nose_x - taper, y + half), (nose_x - length, y + half)]


def draw_lights(c, elapsed_ms):
    """Five start lights, going out one at a time; the last goes out at the start.
    elapsed_ms is server time relative to MATCH_START's timestamp, so every
    client shows the same light at the same moment."""
    for i in range(5):
        out_at = -(4 - i) * LIGHT_STEP_MS
        fade = min(1.0, max(0.0, (elapsed_ms - (out_at - 250)) / 250))  # goes out over the last 250 ms
        brightness = (1.0 - fade) ** 2                                  # eased, not switched
        c.circle(W / 2 + (i - 2) * 44, 60, 14, mix(HAIR, FG, brightness))


def draw_race(c, app):
    client, r = app.client, app.client.round
    players = r.players
    elapsed = client.server_now_ms() - r.start_at_ms
    times = dict(zip(r.result["finish_order"], r.result["times_ms"])) if r.result else {}
    finish_times = sorted(t for t in times.values() if t is not None)

    # header: round, lights or clock, link
    c.label("Round", M, 44)
    c.number(f"{r.round_number}/{ROUNDS_PER_MATCH}", M, 80, 30)
    link(c, app)
    if elapsed < 400 and not r.result:
        draw_lights(c, elapsed)
    else:
        if finish_times:
            shown = finish_times[0]  # time freezes at the winner's finish
        elif r.result:
            shown = r.ended_at_ms + client.clock_offset_ms - r.start_at_ms  # nobody finished
        else:
            shown = elapsed
        c.label("Race time", W / 2, 40, align="center")
        c.number(f"{max(0, shown) / 1000:06.3f}", W / 2, 82, 40, align="center")
    c.hline(M, 100, W - 2 * M)

    # Where everything is. Solid car: newest authoritative snapshot. Ghost: prediction.
    solid = {p["id"]: c.ease(("solid", p["id"]), client.authoritative(p["id"])) for p in players}
    ghost = {p["id"]: c.ease(("ghost", p["id"]), client.predicted(p["id"])) for p in players}
    places = r.result["finish_order"] if r.result else sorted(solid, key=lambda pid: -solid[pid])
    camera = max(solid.values())  # the leader stays put; the track moves

    def screen_x(metres):
        return TRACK_X0 + ANCHOR + (metres - camera) * PPM

    lanes_h = len(players) * LANE_H
    c.surface.set_clip((TRACK_X0 * c.s, LANE_TOP * c.s, TRACK_W * c.s, lanes_h * c.s))
    # far layer: scrolls at half speed, which is what gives the sense of depth
    x = TRACK_X0 - (camera * PPM * 0.5) % 220
    while x < TRACK_X1:
        c.vline(x, LANE_TOP, lanes_h, FAINT)
        x += 220
    for lane, player in enumerate(players):
        pid, accent = player["id"], ACCENTS[lane]
        y0 = LANE_TOP + lane * LANE_H
        centre = y0 + LANE_H / 2 - 6
        # near layer: a tick every 10 m, a taller numbered one every 50 m
        first = max(0, int((camera - ANCHOR / PPM) // 10) * 10)
        for metres in range(first, int(min(r.track_length, camera + TRACK_W / PPM)) + 1, 10):
            tall = metres % 50 == 0
            c.vline(screen_x(metres), y0 + LANE_H - (20 if tall else 7), 20 if tall else 7, mix(BG, FG, 0.22))
            if tall and 0 < metres < r.track_length:
                c.number(str(metres), screen_x(metres) + 6, y0 + LANE_H - 9, 10, DIM)
        c.rect(screen_x(r.track_length), y0, 2, LANE_H, FG)  # the finish line

        entry = r.cars.get(pid, {})
        speed = c.ease(("speed", pid), entry.get("speed", 0.0))
        nose = screen_x(solid[pid])
        # motion trail: four fading dashes whose length follows the speed
        for i, strength in enumerate((0.42, 0.26, 0.14, 0.07)):
            dash = speed * 1.1
            c.rect(nose - 64 - (i + 1) * (dash + 3), centre - 2, dash, 4, mix(BG, accent, strength))
        c.polygon(car_shape(nose, centre), mix(BG, accent, 0.4) if entry.get("stalled") else accent)
        c.polygon(car_shape(screen_x(ghost[pid]), centre), mix(BG, FG, 0.8), width=1)

        if pid in times:  # the round is over: time for the winner, gap for the rest
            if times[pid] is None:
                c.label("did not finish", TRACK_X1 - 16, centre + 8, align="right")
            elif times[pid] == finish_times[0]:
                c.number(f"{times[pid] / 1000:.3f}", TRACK_X1 - 16, centre + 10, 30, align="right")
            else:
                gap = c.ease(("gap", pid), times[pid] - finish_times[0])  # counts up, then settles
                c.number(f"+{gap / 1000:.3f}", TRACK_X1 - 16, centre + 10, 30, align="right")
    c.surface.set_clip(None)

    # lane gutters: who, where, how fast, how hot
    for lane, player in enumerate(players):
        pid, accent = player["id"], ACCENTS[lane]
        y0 = LANE_TOP + lane * LANE_H
        entry = r.cars.get(pid, {})
        if lane:
            c.hline(M, y0, W - 2 * M)
        c.rect(M, y0 + 20, 3, LANE_H - 40, accent)
        if pid in places:
            c.number(f"P{places.index(pid) + 1}", M + 16, y0 + 36, 12, DIM)
        width = c.text(player["name"].upper(), M + 44, y0 + 36, 13, FG, tracking=0.08)
        if pid == client.player_id:
            c.label("you", M + 54 + width, y0 + 36)
        width = c.number(f"{c.ease(('speed', pid), entry.get('speed', 0.0)):04.1f}", M + 16, y0 + 76, 30, dim_zeros=True)
        c.label("stall" if entry.get("stalled") else "m/s", M + 24 + width, y0 + 76,
                FG if entry.get("stalled") else DIM)
        heat = c.ease(("heat", pid), entry.get("heat", 0.0))
        c.rect(M + 16, y0 + 90, 160, 2, HAIR)
        c.rect(M + 16, y0 + 90, 160 * heat / HEAT_MAX, 2, accent)
    c.hline(M, LANE_TOP + lanes_h, W - 2 * M)

    # whole-track strip: the three cars on the full 0..finish scale
    strip_y = LANE_TOP + lanes_h + 22
    c.label("Track", M, strip_y + 4)
    c.hline(TRACK_X0, strip_y, TRACK_W)
    c.vline(TRACK_X1 - 1, strip_y - 6, 13, FG)
    for lane, player in enumerate(players):
        c.rect(TRACK_X0 + TRACK_W * solid[player["id"]] / r.track_length - 1, strip_y - 5, 3, 11, ACCENTS[lane])

    draw_driver_panel(c, client, elapsed)
    draw_telemetry(c, client)


def draw_driver_panel(c, client, elapsed):
    """Bottom left: our own car, from the local prediction (it reacts to a key
    press at once, where the authoritative values arrive a round trip later)."""
    r = client.round
    lane = next((i for i, p in enumerate(r.players) if p["id"] == client.player_id), 0)
    c.hline(M, 500, W - 2 * M)
    heat = c.ease("my heat", r.car.heat)
    rate = c.ease("my rate", sum(r.car.window) / PRESS_WINDOW_SECONDS)
    for x, caption, value, unit in ((M, "Engine heat", f"{heat:03.0f}", "%"),
                                    (M + 200, "Press rate", f"{rate:04.1f}", "/s"),
                                    (M + 400, "Presses", f"{r.presses:03d}", "")):
        c.label(caption, x, 532)
        width = c.number(value, x, 592, 50, dim_zeros=True)
        c.label(unit, x + width + 8, 592)
    c.rect(M, 614, 540, 4, HAIR)
    c.rect(M, 614, 540 * heat / HEAT_MAX, 4, ACCENTS[lane])
    if r.result:
        status = "round over"
    elif elapsed < 0:
        status = "wait for lights out"
    elif r.car.stalled:
        status = "stalled: engine cooling"
    elif r.car.finished:
        status = "finished"
    else:
        status = "hammer SPACE"
    c.label(status, M, 664, FG, 12)
    c.vline(632, 516, 172)


def compact(value):
    """12345 -> '12.3k', so byte rates keep to a few digits."""
    return f"{value / 1000:.1f}k" if value >= 10000 else str(value)


def draw_telemetry(c, client):
    """Bottom right: the state of the link, always on screen."""
    t = client.telemetry()
    dash = "—"  # shown where a transport cannot know the value (TCP hides loss and acks)

    def shown(value):
        return dash if value is None else compact(round(value))

    rows = [
        [("RTT ms", shown(t["rtt_ms"])), ("p50", shown(t["rtt_p50_ms"])),
         ("p95", shown(t["rtt_p95_ms"])), ("RTO ms", shown(t["rto_ms"]))],
        [("Snap/s", shown(t["snapshots_per_s"])), ("Pkt tx", shown(t["packets_sent"])),
         ("Pkt rx", shown(t["packets_received"])), ("Pkt lost", shown(t["packets_lost"]))],
        [("Up B/s", shown(t["up_bps"])), ("Down B/s", shown(t["down_bps"])),
         ("Last ack ms", shown(t["ms_since_last_ack"])), ("Stale", shown(t["stale_snapshots"]))],
    ]
    for row, cells in enumerate(rows):
        for column, (caption, value) in enumerate(cells):
            x, y = 664 + column * 142, 532 + row * 44
            c.label(caption, x, y, size=10)
            c.number(value, x, y + 21, 17)
    # last row: how far each prediction is from the authoritative state, in metres
    y = 532 + 3 * 44
    for lane, player in enumerate(client.round.players):
        x = 664 + lane * 142
        c.label(f"Desync {player['name'][:6]}", x, y, size=10)
        c.number(f"{c.ease(('desync', player['id']), client.desync(player['id'])):.2f}", x, y + 21, 17, ACCENTS[lane])
    c.label("Input ack ms", 664 + 3 * 142, y, size=10)
    c.number(shown(t["input_ack_ms"]), 664 + 3 * 142, y + 21, 17)


def draw_result(c, app):
    client, r, result = app.client, app.client.round, app.client.match_result
    lane_of = {p["id"]: i for i, p in enumerate(r.players)}
    header(c, app, "Match over", "Final classification")
    winner = result["winner"]
    c.label("Winner", M, 200)
    c.text(r.name_of(winner).upper(), M - 4, 290, 96, ACCENTS[lane_of.get(winner, 0)])
    for place, score in enumerate(result["scores"]):
        y = 380 + place * 64
        c.hline(M, y - 40, 760)
        c.number(str(place + 1), M, y, 30)
        c.rect(M + 44, y - 24, 3, 28, ACCENTS[lane_of.get(score["id"], 0)])
        c.text(r.name_of(score["id"]), M + 64, y, 26)
        c.number(str(score["wins"]), M + 520, y, 26, align="right")
        c.label("wins", M + 530, y)
        c.number(str(score["points"]), M + 680, y, 26, align="right")
        c.label("pts", M + 690, y)
    hints(c, [("ENTER", "back to the room")])


# ====================================================================================
# The arcade skin. Sprites and the pixel font only; see docs/design.md.
# ====================================================================================

A_M = 16                                  # page margin
A_TRACK_X0, A_TRACK_X1 = 272, W - A_M     # the scrolling view of the track
A_TRACK_W = A_TRACK_X1 - A_TRACK_X0
A_SKY_Y, A_SKY_H = 84, 42 * PX            # the strip of scenery above the road
A_LANE_H = 26 * PX
A_ANCHOR = round(A_TRACK_W * 0.6)         # where the leader sits, from the left of the view
LAMP_EVERY = round(25 * PPM)              # a street light every 25 m
LADDER_ROW_H = 106
LADDER_SLIDE_MS = 250                     # an overtaken row takes this long to change places
LADDER_FLASH_MS = 400                     # the row that gained a place flashes this long


def ptext(c, string, x, y, size=8, color=WHITE, align="left", upper=True):
    """Pixel-font text with a hard black shadow one font pixel down and right,
    which keeps it readable on the sky, the road and the panels alike. Every
    glyph is one em wide, so numerals never shift sideways. y is the baseline."""
    string = string.upper() if upper else string
    c.text(string, x + size // 8, y + size // 8, size, BLACK, "pixel", align=align)
    return c.text(string, x, y, size, color, "pixel", align=align)


def pnumber(c, string, x, y, size, color=WHITE):
    """A fixed-width readout: leading zeros are drawn dim, as in the broadcast skin."""
    body = string.lstrip("0")
    if not body or not body[0].isdigit():
        body = "0" + body
    width = ptext(c, string[:len(string) - len(body)], x, y, size, SLATE)
    return width + ptext(c, body, x + width, y, size, color)


def panel(c, x, y, w, h, title=""):
    """The arcade frame: a light border one sprite pixel thick, a hard shadow,
    and a blue title bar."""
    c.rect(x + PX, y + PX, w, h, BLACK)
    c.rect(x, y, w, h, ICE)
    c.rect(x + PX, y + PX, w - 2 * PX, h - 2 * PX, PANEL)
    if title:
        c.rect(x + PX, y + PX, w - 2 * PX, 21, BLUE)
        ptext(c, title, x + 12, y + 18)


def a_page(c, app, title, size=48):
    """Background and top strip shared by every arcade screen except the race."""
    c.surface.fill(PAGE)
    ptext(c, title, W / 2, 70 if size == 48 else 62, size, YELLOW_LIGHT, "center")
    a_link(c, app)


def a_link(c, app):
    """Top right: transport and server address; under it our name, or the latest
    notice (a player leaving) for four seconds."""
    ptext(c, f"{app.transport_label} {app.host}:{app.port}", W - A_M, 28, 8, ICE, "right")
    client = app.client
    notice = client.notices[-1] if client and client.notices else None
    if notice and now_ms() - notice[0] < 4000:
        ptext(c, notice[1], W - A_M, 60, 16, YELLOW_LIGHT, "right")
    elif client and client.name and client.player_id:
        ptext(c, client.name, W - A_M, 60, 16, align="right")


def a_hints(c, app, pairs, x=A_M, y=704):
    """Bottom strip: the key in yellow, what it does in blue-white."""
    for key, action in pairs:
        x += ptext(c, key, x, y, 8, YELLOW_LIGHT) + 8
        x += ptext(c, action, x, y, 8, ICE) + 24


def sound_hint(app):
    """M is offered on the screens where the keyboard is not used for typing."""
    return ("M", "sound on" if app.sound.muted else "sound off")


def a_field(c, text, x, y, chars, placeholder):
    """A one-line text box, chars characters wide. Longer text shows its end."""
    c.rect(x - 12, y - 39, chars * 24 + 48, 54, ICE)
    c.rect(x - 9, y - 36, chars * 24 + 42, 48, BLACK)
    shown = text[-chars:]
    width = ptext(c, shown, x, y, 24, upper=False) if shown else 0
    if not shown:
        ptext(c, placeholder, x + 30, y - 8, 8, mix(ICE, BLACK, 0.45))
    if now_ms() // 400 % 2 == 0:
        c.rect(x + width + 3, y - 24, 18, 24, YELLOW_LIGHT)  # block caret


def a_message(c, app, x, y):
    """The server's last ERROR, or a local problem, in plain words."""
    if app.message and now_ms() - app.message[0] < 6000:
        ptext(c, app.message[1], x, y, 8, YELLOW_LIGHT)


# --- scenery -------------------------------------------------------------------------

_layers = {}


def scenery_layer(kind):
    """One repeating tile of a background layer. It is drawn small, in sprite
    pixels, and enlarged PX times exactly like the sprites, so the scenery and
    the cars are made of pixels of one size."""
    if kind not in _layers:
        w, h = 330, A_SKY_H // PX
        tile = pygame.Surface((w, h), pygame.SRCALPHA)
        if kind == "skyline":
            for x, y, length in ((20, 4, 26), (130, 9, 18), (235, 2, 30)):  # clouds
                tile.fill(WHITE, (x, y + 2, length, 3))
                tile.fill(WHITE, (x + 4, y, length - 10, 2))
            rng = random.Random(7)  # a fixed seed: the same city on every machine
            x = 0
            while x < w:
                width, height = rng.randint(8, 16), rng.randint(10, 24)
                tile.fill(BLUE, (x, h - height, width, height))
                for window_y in range(h - height + 2, h - 3, 4):
                    for window_x in range(x + 2, x + width - 2, 3):
                        if rng.random() < 0.5:
                            tile.fill(BLUE_LIGHT, (window_x, window_y, 1, 2))
                x += width
        else:  # hills: two sine waves whose lengths divide the tile, so it repeats without a seam
            for x in range(w):
                height = round(13 + 6 * math.sin(2 * math.pi * x / 110) + 3 * math.sin(2 * math.pi * x / 33 + 1))
                tile.fill(GREEN, (x, h - height, 1, height))
                tile.fill(GREEN_LIGHT, (x, h - height, 1, 1))
        _layers[kind] = pygame.transform.scale(tile, (w * PX, h * PX)).convert_alpha()
    return _layers[kind]


def tiled(c, image, x0, width, y, offset, period=None):
    """Repeat image across x0..x0+width. offset moves it; it wraps every period."""
    period = period or image.get_width()
    x = x0 + offset % period - period
    while x < x0 + width:
        c.blit(image, x, y)
        x += period


def scenery(c, x0, width, sky_y, shift, lanes):
    """Sky, three scrolling layers and the road. shift is how far the world has
    moved, in pixels: the road and what stands beside it move by all of it, the
    hills by a third, the skyline by a tenth. Distant things passing slowly is
    what reads as depth. The caller sets the clip rectangle."""
    road_y = sky_y + A_SKY_H
    c.rect(x0, sky_y, width, A_SKY_H, BLUE_LIGHT)
    tiled(c, scenery_layer("skyline"), x0, width, sky_y, shift * 0.1)
    tiled(c, scenery_layer("hills"), x0, width, sky_y, shift * 0.35)
    lamp, sign, barrier = sprite("light"), sprite("sign_red"), sprite("barrier")
    tiled(c, lamp, x0, width, road_y - 15 - lamp.get_height(), shift + 130, LAMP_EVERY)
    tiled(c, sign, x0, width, road_y - 15 - sign.get_height(), shift + 400, LAMP_EVERY * 3)
    tiled(c, barrier, x0, width, road_y - barrier.get_height(), shift)
    c.rect(x0, road_y, width, lanes * A_LANE_H, SLATE)
    c.rect(x0, road_y + lanes * A_LANE_H, width, PX, ICE)
    for lane in range(1, lanes):  # dashed lane lines
        x = x0 + shift % 60 - 60
        while x < x0 + width:
            c.rect(x, road_y + lane * A_LANE_H - 1, 30, PX, ICE)
            x += 60


def draw_car(c, lane, nose_x, lane_top, distance=0.0, speed=0.0, stalled=False, ghost_x=None):
    """One car in its lane. The solid sprite is where the server says the car
    is. ghost_x, if given, is where this client predicts it is: the ghost
    sprite, drawn first so it shows wherever the two disagree."""
    body = sprite(CAR_SPRITES[lane])
    w = body.get_width()
    y = lane_top + A_LANE_H - 18 - body.get_height()
    now = now_ms()
    if ghost_x is not None:
        c.blit(sprite(CAR_SPRITES[lane], ghost=True), ghost_x - w, y)
    x = nose_x - w
    c.rect(x + 6, y + body.get_height(), w - 12, PX, INK)  # shadow on the road
    if stalled:
        x += PX if now // 50 % 2 else -PX  # shake
    else:
        y -= PX * (int(distance / 3) % 2)  # bob one sprite pixel every 3 m: faster car, faster bob
        for i, dy in enumerate((9, 18, 27)):  # speed lines, longer the faster
            if speed > 1:
                c.rect(x - 9 - i * 6 - speed * 1.5, y + dy, speed * 1.5, PX, ICE)
    c.blit(body, x, y)
    if stalled:
        # Smoke off the bonnet: five puffs, each rising, drifting back, growing
        # and greying over 800 ms. Computed from the clock, so nothing is stored.
        for i in range(5):
            age = (now / 800 + i / 5) % 1.0
            size = PX * (3 + int(age * 4))
            c.rect(x + w - 27 + i % 2 * 9 - age * 24, y + 9 - size - age * 54, size, size, mix(WHITE, SLATE, age * 0.7))


def attract(c, y, lanes_of_cars):
    """A strip of scrolling scenery with cars cruising on it, for the screens
    that are not the race."""
    travelled = now_ms() / 1000 * 14  # metres, at a gentle 14 m/s
    c.surface.set_clip((0, y, W, A_SKY_H + A_LANE_H + PX))
    scenery(c, 0, W, y, -travelled * PPM, 1)
    for i, lane in enumerate(lanes_of_cars):
        draw_car(c, lane, W / 2 + 60 + (i - (len(lanes_of_cars) - 1) / 2) * 260, y + A_SKY_H, travelled + i, 14)
    c.surface.set_clip(None)


# --- before the race -----------------------------------------------------------------

def arcade_name(c, app):
    a_page(c, app, "Drag Race")
    attract(c, 96, [0, 1, 2])
    panel(c, 240, 336, 800, 230, "Driver sign-in")
    ptext(c, "Driver name", 270, 396, 8, ICE)
    a_field(c, app.name_field.text, 282, 456, 16, "type your name")
    a_message(c, app, 270, 528)
    a_hints(c, app, [("ENTER", "connect"), ("ESC", "quit")])


def arcade_lobby(c, app):
    a_page(c, app, "Drag Race")
    attract(c, 96, [0, 1, 2])
    panel(c, 16, 336, 640, 300, "Join or create a room")
    ptext(c, "Room code, or a pasted invite  host:port:code", 40, 396, 8, ICE)
    a_field(c, app.join_field.text, 52, 456, 23, "empty = create a new room")
    a_message(c, app, 40, 528)

    panel(c, 672, 336, 592, 300, "Open rooms")
    if not app.client.rooms:
        ptext(c, "none yet", 696, 396, 8, ICE)
    for i, room in enumerate(app.client.rooms[:6]):
        y = 400 + i * 40
        ptext(c, room["code"], 696, y, 16)
        ptext(c, room["room_name"], 816, y - 4, 8, ICE)
        if room["in_match"]:
            ptext(c, "racing", 1240, y - 4, 8, YELLOW_LIGHT, "right")
        else:
            ptext(c, f"{room['player_count']}/{ROOM_SIZE}", 1240, y, 16, align="right")
    a_hints(c, app, [("ENTER", "join / create"), ("CTRL+V", "paste invite"), ("ESC", "sign out")])


def arcade_room(c, app):
    client, room = app.client, app.client.room
    a_page(c, app, room["room_name"], 24)
    invite = protocol.format_join_string(app.host, app.port, room["code"])

    panel(c, 16, 96, 720, 560, "Invite")
    ptext(c, "Room code", 40, 152, 8, ICE)
    ptext(c, room["code"], 40, 250, 72, YELLOW_LIGHT)
    c.blit(qr_surface(invite, 252, BLACK, WHITE, border=4), 40, 290)
    ptext(c, "Scan or paste to join", 316, 320, 8, ICE)
    ptext(c, invite, 316, 356, 16 if len(invite) <= 25 else 8, upper=False)
    ptext(c, "CTRL+C copies the invite", 316, 392, 8, ICE)
    a_message(c, app, 316, 440)

    panel(c, 752, 96, 512, 560, "Grid")
    players = room["players"]
    for slot in range(ROOM_SIZE):
        y = 140 + slot * 130
        c.blit(sprite(CAR_SPRITES[slot], ghost=slot >= len(players)), 776, y + 24)  # an empty seat is a ghost
        if slot >= len(players):
            ptext(c, "open", 900, y + 46, 8, SLATE)
            continue
        player = players[slot]
        ptext(c, player["name"], 900, y + 34, 16, PLAYER_COLORS[slot])
        tags = [tag for tag, on in (("host", player["id"] == room["creator_id"]),
                                    ("you", player["id"] == client.player_id)) if on]
        ptext(c, "  ".join(tags), 900, y + 56, 8, ICE)
        ptext(c, "ready" if player["ready"] else "not ready", 900, y + 78, 8,
              GREEN_LIGHT if player["ready"] else RED_LIGHT)

    everyone_ready = len(players) >= 2 and all(p["ready"] for p in players)
    if len(players) < 2:
        status = "waiting for another driver"
    elif not everyone_ready:
        status = "waiting for everyone to be ready"
    elif room["creator_id"] == client.player_id:
        status = "press ENTER to start with two drivers"
    else:
        status = "waiting for the host to start"
    ptext(c, status, 776, 600, 8, YELLOW_LIGHT)
    a_hints(c, app, [("SPACE", "ready"), ("ENTER", "start (host)"), ("ESC", "leave"), sound_hint(app)])


# --- the race ------------------------------------------------------------------------

def race_order(client):
    """Player ids from first place to last. During a round: by the newest
    authoritative distance (a tie keeps lane order). After it: the server's
    finish order. The ladder and the overtake sound both use this."""
    r = client.round
    lanes = [p["id"] for p in r.players]
    if r.result:
        order = [pid for pid in r.result["finish_order"] if pid in lanes]
        return order + [pid for pid in lanes if pid not in order]  # someone who left mid-round goes last
    return sorted(lanes, key=lambda pid: -client.authoritative(pid))


def arcade_lights(c, elapsed_ms):
    """Five red lamps going out one at a time, then all green for 600 ms: GO.
    elapsed_ms is server time relative to MATCH_START's timestamp, so every
    client switches the same lamp at the same moment."""
    x0 = (A_TRACK_X0 + A_TRACK_X1) / 2 - 147
    panel(c, x0, 96, 294, 78)
    for i in range(5):
        if elapsed_ms >= 0:
            color = GREEN_LIGHT
        else:
            color = SLATE if elapsed_ms >= -(4 - i) * LIGHT_STEP_MS else RED_LIGHT
        c.rect(x0 + 18 + i * 54, 114, 42, 42, BLACK)
        c.rect(x0 + 21 + i * 54, 117, 36, 36, color)
    if elapsed_ms >= 0:
        ptext(c, "GO!", x0 + 147, 240, 48, GREEN_LIGHT, "center")


def arcade_race(c, app):
    client, r = app.client, app.client.round
    players = r.players
    lane_of = {p["id"]: lane for lane, p in enumerate(players)}
    elapsed = client.server_now_ms() - r.start_at_ms
    times = dict(zip(r.result["finish_order"], r.result["times_ms"])) if r.result else {}
    finish_times = sorted(t for t in times.values() if t is not None)

    # top strip: round, clock, link
    c.surface.fill(PAGE)
    ptext(c, "Round", A_M, 28, 8, ICE)
    ptext(c, f"{r.round_number}/{ROUNDS_PER_MATCH}", A_M, 64, 24)
    a_link(c, app)
    if finish_times:
        shown = finish_times[0]  # the clock freezes at the winner's finish
    elif r.result:
        shown = r.ended_at_ms + client.clock_offset_ms - r.start_at_ms  # nobody finished
    else:
        shown = elapsed
    ptext(c, "Race time", W / 2, 28, 8, ICE, "center")
    ptext(c, f"{max(0, shown) / 1000:06.3f}", W / 2, 64, 24, YELLOW_LIGHT, "center")

    # Where everything is. Solid car: newest authoritative snapshot. Ghost: prediction.
    solid = {p["id"]: c.ease(("solid", p["id"]), client.authoritative(p["id"])) for p in players}
    ghost = {p["id"]: c.ease(("ghost", p["id"]), client.predicted(p["id"])) for p in players}
    # A car on the finish line has stopped, though its snapshot keeps the speed it crossed at.
    speed = {p["id"]: c.ease(("speed", p["id"]), 0.0 if client.authoritative(p["id"]) >= r.track_length
                             else r.cars.get(p["id"], {}).get("speed", 0.0)) for p in players}
    camera = max(solid.values())  # the leader stays put; the world moves

    def screen_x(metres):
        return A_TRACK_X0 + A_ANCHOR + (metres - camera) * PPM

    road_y = A_SKY_Y + A_SKY_H
    road_h = ROOM_SIZE * A_LANE_H  # always three lanes of road, also with two players
    c.surface.set_clip((A_TRACK_X0, A_SKY_Y, A_TRACK_W, A_SKY_H + road_h + PX))
    scenery(c, A_TRACK_X0, A_TRACK_W, A_SKY_Y, A_ANCHOR - camera * PPM, ROOM_SIZE)
    gantry = sprite("highway")
    for metres in range(50, int(r.track_length) + 1, 50):  # a gantry every 50 m, the last one at the finish
        x = screen_x(metres)
        if A_TRACK_X0 - 100 < x < A_TRACK_X1 + 100:
            c.blit(gantry, x - gantry.get_width() / 2, road_y - 3 - gantry.get_height())
            ptext(c, "finish" if metres == r.track_length else f"{metres}m", x, road_y - 102, 8, align="center")
    c.rect(screen_x(0) - 6, road_y, 6, road_h, WHITE)  # start line
    for row in range(road_h // 6):                     # finish line: two columns of checks
        for column in range(2):
            c.rect(screen_x(r.track_length) + column * 6, road_y + row * 6, 6, 6, WHITE if (row + column) % 2 else BLACK)
    for lane, player in enumerate(players):
        pid = player["id"]
        lane_top = road_y + lane * A_LANE_H
        stalled = r.cars.get(pid, {}).get("stalled", False)
        draw_car(c, lane, screen_x(solid[pid]), lane_top, solid[pid], speed[pid], stalled, screen_x(ghost[pid]))
        if not stalled:  # a stalled car has smoke where its name tag would be
            tag_x = screen_x(solid[pid]) - sprite(CAR_SPRITES[lane]).get_width() / 2
            ptext(c, player["name"][:10], tag_x, lane_top + 14, 8, PLAYER_COLORS[lane], "center")
    if elapsed < 600 and not r.result:
        arcade_lights(c, elapsed)
    c.surface.set_clip(None)

    arcade_ladder(c, client, lane_of, solid, speed, elapsed, times, finish_times)
    arcade_driver_panel(c, app, elapsed)
    arcade_telemetry(c, client)


def arcade_ladder(c, client, lane_of, solid, speed, elapsed, times, finish_times):
    """Left of the track: one row per player, in race order. The place numbers
    stay put; when two players swap places their rows slide past each other
    over LADDER_SLIDE_MS, and the row that moved up flashes white."""
    r = client.round
    order = race_order(client)
    top = A_SKY_Y + 36
    panel(c, A_M, A_SKY_Y, 240, 36 + len(order) * LADDER_ROW_H + 6, "Positions")
    now = now_ms()
    rows = c.memo.setdefault("ladder", {})  # player id -> [place, y it slides from, slide start, flash start]

    def row_y(row):
        t = min(1.0, (now - row[2]) / LADDER_SLIDE_MS)
        t = t * t * (3 - 2 * t)  # ease in and out: starts and stops gently
        return row[1] + (top + row[0] * LADDER_ROW_H - row[1]) * t

    for place, pid in enumerate(order):
        ptext(c, str(place + 1), A_M + 12, top + place * LADDER_ROW_H + 64, 24, YELLOW_LIGHT)
        row = rows.setdefault(pid, [place, top + place * LADDER_ROW_H, 0, 0])
        if row[0] != place:
            gained = place < row[0]
            rows[pid] = [place, row_y(row), now, now if gained else row[3]]

    leader = order[0]
    for pid in sorted(order, key=lambda pid: rows[pid][3]):  # the row that gained last is drawn on top
        row, lane = rows[pid], lane_of[pid]
        x, y, w, h = A_M + 48, row_y(row), 186, LADDER_ROW_H - 6
        flash = max(0.0, 1 - (now - row[3]) / LADDER_FLASH_MS)
        c.rect(x, y, w, h, mix(SLATE, WHITE, flash))
        c.rect(x, y, 9, h, PLAYER_COLORS[lane])
        entry = r.cars.get(pid, {})
        ptext(c, r.name_of(pid)[:10], x + 18, y + 26, 16)
        if r.result:  # the round is over: the winner's time, everyone else's gap to it
            if times.get(pid) is None:
                gap = "DNF"
            elif times[pid] == finish_times[0]:
                gap = f"{times[pid] / 1000:.3f}"
            else:
                gap = f"+{c.ease(('gap', pid), times[pid] - finish_times[0]) / 1000:.3f}"
        elif pid == leader:
            gap = "leader"
        else:
            # Gap in seconds: how long ago the leader was where this car is
            # now, at the leader's average speed so far.
            share_behind = 1 - solid[pid] / solid[leader] if solid[leader] > 0 else 0.0
            gap = f"+{c.ease(('gap', pid), max(0, elapsed) / 1000 * share_behind):.2f}"
        ptext(c, gap, x + 18, y + 52, 16, YELLOW_LIGHT)
        if entry.get("stalled"):
            ptext(c, "stall", x + 18, y + 70, 8, RED_LIGHT)
        else:
            ptext(c, f"{speed[pid]:04.1f} m/s", x + 18, y + 70, 8, ICE)
        if pid == client.player_id:
            ptext(c, "you", x + w - 9, y + 70, 8, align="right")
        heat_bar(c, x + 18, y + 80, 13, 12, 10, c.ease(("heat", pid), entry.get("heat", 0.0)))


def heat_bar(c, x, y, segment_w, h, segments, heat):
    """Engine heat as a row of blocks: green, then yellow, the last fifth red.
    When the last block lights the engine stalls."""
    lit = math.ceil(segments * heat / HEAT_MAX - 0.001)
    for i in range(segments):
        color = GREEN if i < segments * 0.6 else YELLOW_LIGHT if i < segments * 0.8 else RED
        c.rect(x + i * (segment_w + 3), y, segment_w, h, color if i < lit else INK)


def arcade_driver_panel(c, app, elapsed):
    """Bottom left: our own car, from the local prediction (it reacts to a key
    press at once, where the authoritative values arrive a round trip later)."""
    r = app.client.round
    panel(c, A_M, 456, 592, 248, "Your car")
    heat = c.ease("my heat", r.car.heat)
    rate = c.ease("my rate", sum(r.car.window) / PRESS_WINDOW_SECONDS)
    # The press counter turns yellow for 90 ms on every press that counted.
    memo = c.memo.setdefault("presses", [r.presses, 0])  # [count last frame, when it last changed]
    if memo[0] != r.presses:
        memo[:] = [r.presses, now_ms()]
    pressed = now_ms() - memo[1] < 90
    for x, caption, value, unit, color in ((34, "Engine heat", f"{heat:03.0f}", "%", WHITE),
                                           (226, "Press rate", f"{rate:04.1f}", "/s", WHITE),
                                           (418, "Presses", f"{r.presses:03d}", "", YELLOW_LIGHT if pressed else WHITE)):
        ptext(c, caption, x, 504, 8, ICE)
        width = pnumber(c, value, x, 548, 24, color)
        ptext(c, unit, x + width + 6, 548, 8, ICE)
    heat_bar(c, 34, 566, 24, 30, 20, heat)
    if r.result:
        status, color = "round over", WHITE
    elif elapsed < 0:
        status, color = "wait for the lights", WHITE
    elif r.car.stalled:
        status, color = "stalled! cooling down", RED_LIGHT
    elif r.car.finished:
        status, color = "finished!", GREEN_LIGHT
    else:
        status, color = "hammer SPACE!", YELLOW_LIGHT if now_ms() // 250 % 2 else WHITE
    ptext(c, status, 34, 644, 16, color)
    a_hints(c, app, [("SPACE", "go"), ("ESC", "leave"), sound_hint(app)], 34, 686)


def arcade_telemetry(c, client):
    """Bottom right: the state of the link, always on screen. The same sixteen
    values as the broadcast skin."""
    t = client.telemetry()
    panel(c, 624, 456, 640, 248, "Link telemetry")

    def shown(value):  # "--" where a transport cannot know the value (TCP hides loss and acks)
        return "--" if value is None else compact(round(value))

    rows = [
        [("RTT ms", shown(t["rtt_ms"])), ("p50", shown(t["rtt_p50_ms"])),
         ("p95", shown(t["rtt_p95_ms"])), ("RTO ms", shown(t["rto_ms"]))],
        [("Snap/s", shown(t["snapshots_per_s"])), ("Pkt tx", shown(t["packets_sent"])),
         ("Pkt rx", shown(t["packets_received"])), ("Pkt lost", shown(t["packets_lost"]))],
        [("Up B/s", shown(t["up_bps"])), ("Down B/s", shown(t["down_bps"])),
         ("Last ack ms", shown(t["ms_since_last_ack"])), ("Stale", shown(t["stale_snapshots"]))],
    ]
    for row, cells in enumerate(rows):
        for column, (caption, value) in enumerate(cells):
            x, y = 642 + column * 154, 504 + row * 50
            ptext(c, caption, x, y, 8, ICE)
            # the three numbers the demo is about are yellow: delay, loss, late arrivals
            ptext(c, value, x, y + 24, 16, YELLOW_LIGHT if caption in ("RTT ms", "Pkt lost", "Stale") else WHITE)
    # last row: how far each prediction is from the authoritative state, in metres
    y = 504 + 3 * 50
    for lane, player in enumerate(client.round.players):
        x = 642 + lane * 154
        ptext(c, f"Desync {player['name'][:6]}", x, y, 8, ICE)
        ptext(c, f"{c.ease(('desync', player['id']), client.desync(player['id'])):.2f}", x, y + 24, 16, PLAYER_COLORS[lane])
    ptext(c, "Input ack ms", 642 + 3 * 154, y, 8, ICE)
    ptext(c, shown(t["input_ack_ms"]), 642 + 3 * 154, y + 24, 16)


def arcade_result(c, app):
    client, r, result = app.client, app.client.round, app.client.match_result
    lane_of = {p["id"]: i for i, p in enumerate(r.players)}
    winner = result["winner"]
    a_page(c, app, "Match over")
    attract(c, 96, [lane_of.get(winner, 0)])  # the winner's car takes a lap of honour
    panel(c, 16, 336, 1248, 330, "Final classification")
    ptext(c, "Winner", 40, 392, 8, ICE)
    ptext(c, r.name_of(winner), 40, 456, 48, PLAYER_COLORS[lane_of.get(winner, 0)])
    for place, score in enumerate(result["scores"]):
        y = 514 + place * 48
        ptext(c, str(place + 1), 40, y, 24, YELLOW_LIGHT)
        c.rect(82, y - 24, 9, 24, PLAYER_COLORS[lane_of.get(score["id"], 0)])
        ptext(c, r.name_of(score["id"]), 106, y, 16)
        ptext(c, str(score["wins"]), 620, y, 16, align="right")
        ptext(c, "wins", 630, y, 8, ICE)
        ptext(c, str(score["points"]), 800, y, 16, align="right")
        ptext(c, "pts", 810, y, 8, ICE)
    a_hints(c, app, [("ENTER", "back to the room"), sound_hint(app)])
