"""What each screen looks like. Pure drawing: reads the app and the client
core, changes nothing. Layout is a fixed grid on a 1280 x 720 page with a
48 px margin; the look is a motorsport timing broadcast.
"""

import pygame
import qrcode

from client.core import now_ms
from client.theme import ACCENTS, BG, DIM, FAINT, FG, HAIR, S, W, mix
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


def qr_surface(text, size):
    """The invite as a QR code, drawn light-on-dark so it sits in the page with
    no white box around it. The dark page itself is the quiet zone."""
    if text not in _qr_cache:
        code = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data(text)
        code.make()
        module = size * S // code.modules_count
        surface = pygame.Surface((module * code.modules_count,) * 2)
        surface.fill(BG)
        for row, line in enumerate(code.modules):
            for column, dark in enumerate(line):
                if dark:
                    surface.fill(FG, (column * module, row * module, module, module))
        _qr_cache[text] = surface
    return _qr_cache[text]


def draw_room(c, app):
    client, room = app.client, app.client.room
    header(c, app, "Room", room["room_name"])
    invite = protocol.format_join_string(app.host, app.port, room["code"])

    c.label("Room code", M, 160)
    c.number(room["code"], M - 4, 270, 104)
    c.blit(qr_surface(invite, 200), M, 330)
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
    c.surface.set_clip((TRACK_X0 * S, LANE_TOP * S, TRACK_W * S, lanes_h * S))
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
