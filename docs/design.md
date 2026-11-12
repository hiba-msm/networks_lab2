# Visual design

The look is a motorsport timing broadcast (F1 / Formula E timing graphics): a
dark page, thin rules, small wide-tracked captions, large numerals. It must not
read as a student pygame project. Everything is drawn procedurally: no sprites,
no images, no clip art.

Code: `client/theme.py` (palette, type, canvas), `client/screens.py` (layout).

## Rules

### Colour

| Role | Hex | Use |
|---|---|---|
| Background | `#0D0F12` | The page. Never pure black. |
| Foreground | `#E8EAED` | Primary text, numerals, the finish line, lit start lights. |
| Secondary | foreground at 55% (`#85878A`) | Captions, units, hints. |
| Hairline | foreground at 12% (`#27292C`) | Rules, empty bars, unlit start lights. |
| Far layer | foreground at 5% (`#181A1D`) | The slow background lines behind the track. |
| Player 1 | `#4C8DD9` | Lane 1 accent. |
| Player 2 | `#D98A4C` | Lane 2 accent. |
| Player 3 | `#5FB88F` | Lane 3 accent. |

- No gradients, no drop shadows, no glow, no outlines around filled shapes.
- "Transparent" tones are pre-mixed with the background (`mix()` in
  `theme.py`), because the page is flat and nothing else is ever behind them.
- An accent means "this player" and nothing else. Do not use accents for
  decoration, warnings or emphasis.
- No colours outside this table.

### Type

| Face | File | Use |
|---|---|---|
| Space Grotesk Medium | `assets/fonts/SpaceGrotesk-Medium.ttf` | Captions, names, titles. |
| JetBrains Mono Regular | `assets/fonts/JetBrainsMono-Regular.ttf` | Every numeral and every code, address and key name. |

- **Tabular figures rule:** anything containing digits that can change is set
  in the mono face, so readouts do not jitter sideways as digits change. Use
  `canvas.number()`.
- Readouts have a fixed width. Pad with leading zeros and pass
  `dim_zeros=True`, which draws the padding faintly.
- Captions are upper case, 11 px, tracked 0.14 em, secondary colour. Use
  `canvas.label()`.
- Text `y` is the baseline, so a caption and a numeral on one line share a baseline.
- Both fonts are loaded with `pygame.font.Font` from the repo. Never fall back
  to a system font.

### Rendering

- The whole frame is drawn on a 2560 x 1440 surface (2x) and scaled to the
  1280 x 720 window with `pygame.transform.smoothscale` once per frame. That
  anti-aliases every edge. Never draw directly on the window.
- All coordinates in `screens.py` are window pixels; the canvas multiplies by 2.
- **Every animated value is interpolated, never snapped:**
  `current += (target - current) * 0.2` per frame (`EASE` in `theme.py`,
  `canvas.ease(key, target)`). Counters (packets, presses) are exact values and
  are not eased.

### Layout grid

- Page 1280 x 720, margin 48 px on all sides.
- Header: caption baseline 44, title baseline 78, rule at y = 100.
- Key hints: baseline 688.
- Race screen:
  - Lanes start at y = 116, each 112 px high, separated by hairlines.
  - Lane gutter x = 48..264: accent bar, position, name, speed, heat bar.
  - Track x = 264..1232. Scale 11 px per metre. The leader sits 60% of the way
    across; the camera follows the leader and the track scrolls.
  - Whole-track strip under the lanes.
  - Rule at y = 500. Below it, driver panel on the left, vertical rule at
    x = 632, telemetry panel from x = 664 in 4 columns of 142 px, rows 44 px apart.
- Generous empty space is intended. Do not fill it.

### Motion and the track

- Cars are long flat shapes with a tapered nose and a short trail of four
  fading dashes whose length follows speed. They are not illustrations.
- Two layers scroll: track ticks (every 10 m, taller and numbered every 50 m)
  at full speed, and faint vertical lines at half speed for depth.
- A stalled car is drawn at 40% of its accent.
- Start: five lights in the header go out one at a time, 700 ms apart, each
  fading over 250 ms; the last goes out at `MATCH_START`'s timestamp on the
  estimated server clock, so all clients agree.
- Finish: the clock freezes at the winner's time. Each other lane counts up to
  its gap in milliseconds.

## The two diagnostic visuals

These are the selling point of the demo. Do not remove, hide or restyle them
into a debug overlay.

### Ghost car

Every car is drawn twice:

- **Solid** at the server-authoritative position: the newest snapshot.
- **Outline** at the client's predicted position: for our own car the local
  simulation, for others the newest snapshot carried forward at its speed.

On a clean link they overlap. Under UDP loss the outline pulls ahead while
snapshots are missing and returns when one arrives. It must stay visible
against both the page and the solid car.

### Telemetry panel

Always visible, bottom right, styled like the rest. It must show: RTT (current,
p50, p95), the retransmit timer, snapshots received per second, packets sent /
received / lost, bytes per second up and down, time since the last ack,
discarded stale snapshots, the desync in metres for each player in that
player's accent, and input-to-acknowledgement latency. Where a transport cannot
know a value (TCP hides loss, acks and its own timer), show a dash, never a zero.

## Known rough edges

Written to be argued with. Most important first.

1. **Nobody has played it by hand yet.** Every screen was produced by a script
   and judged from screenshots. Key feel, typing, paste and copy are unproven.
2. **The ghost is too subtle.** It is a 1 px light outline. On a clean link it
   reads as a faint border on the car; on a projector it may vanish. The solid
   car and the ghost are both eased with the same constant, which softens the
   "tear away and snap back" that the demo is supposed to show. First change:
   make the ghost heavier (2 px, or a hatched fill) and ease it less than the
   solid car, or not at all.
3. **Pressing SPACE gives no feedback.** Nothing on screen acknowledges a key
   press except the numbers changing. A press before the lights go out is
   silently ignored. A short pulse on the car or the press counter would help.
4. **Heat is hard to read.** The lane heat bar is 2 px high and 160 px wide,
   with no mark for the stall point and no warning as it approaches. A stall is
   signalled only by the word STALL and a dimmer car. The big heat readout
   helps only for your own car.
5. **The start lights are small and stay within the palette**, so they are
   white, not red, and there is no "go" moment. They are easy to miss in the header.
6. **The finish is flat.** The gap count-up finishes in about a third of a
   second, too quick to read as "resolving". The cars just stop at the line.
   The speed readout keeps showing the crossing speed after the finish.
7. **The telemetry panel has no hierarchy.** Sixteen cells of equal weight.
   RTT, loss and desync matter most for the demo and do not stand out. Its
   values jump rather than ease. Counters over 9999 switch format (`12.3k`),
   which changes their width.
8. **Fixed 1280 x 720 window.** Not resizable, no fullscreen, no awareness of
   display scaling. On a high-DPI laptop or a projector it may be small or blurred.
9. **Long names overflow.** A 16-character name in the lane gutter runs into
   the track. Names are not truncated or scaled anywhere.
10. **The lobby and name screens are mostly empty**, and the text field's caret
    and placeholder are positioned by eye. There is no mouse support, and the
    room list cannot be selected from; you must type the code.
11. **Sans text is drawn glyph by glyph**, so it has no kerning. It shows in
    titles such as a room name.
12. **Easing is per frame, not per second.** At a frame rate other than 60 the
    motion speeds up or slows down.
13. **A two-player match leaves the third lane as blank space.**
14. **Error text comes straight from the server** and is shown as an upper-case
    caption for 6 seconds. It is accurate but not friendly.
15. **The inverted QR is unverified.** Some phone scanners cannot read
    light-on-dark codes. The module size is rounded to whole pixels, so the
    code is slightly smaller than its 200 px box.
16. **Performance is unmeasured on slow machines.** Each frame scales a
    2560 x 1440 surface. While a frame is being drawn the network is not read,
    which adds a few milliseconds to the RTT the panel shows (p95 5..11 ms on
    loopback, against a p50 of 2 ms).
17. **Hairlines at 12% may disappear on a projector.** Check on the demo
    hardware before the day.

Screenshots of every screen are in `docs/screenshots/`: `udp_*` and `tcp_*` on
a clean link, `udp_loss30_*` with 30% of server-to-client datagrams dropped.
Regenerate them after a visual change with `python -m tools.screenshots`
(see the top of `tools/screenshots.py`).
