# Visual and sound design

The client has two skins, chosen on the command line:

| Flag | Look |
|---|---|
| `--skin arcade` (default) | Side-on pixel-art racer in the style of a 16-bit console game: sprite cars on a scrolling road, saturated colours, chunky HUD. |
| `--skin broadcast` | The earlier look: a dark motorsport timing page drawn with lines and type only. Kept as the fallback; see the last section. |

Both skins show exactly the same information and run the same game. The skin
changes drawing only: nothing in `client/core.py`, `common/`, `server/` or
`transport/` knows it exists, and the server needs neither pygame nor any asset.

Code: `client/theme.py` (palettes, fonts, sprite loading, the canvas),
`client/screens.py` (every screen, once per skin), `client/sound.py` (sound),
`client/app.py` (which screen is showing, keys, when a sound plays).

## Assets, sources and licences

Everything is in `assets/`, committed, and loaded by relative path. Nothing is
downloaded at run time.

| Asset | Files | Source | Licence |
|---|---|---|---|
| Kenney "Pixel Vehicle Pack" 1.0 (2019) | `assets/sprites/*.png`: 3 cars, barrier, street light, signs, gantry | https://kenney.nl/assets/pixel-vehicle-pack | CC0 1.0 (public domain). Text in `assets/sprites/Kenney-License.txt`. |
| Press Start 2P | `assets/fonts/PressStart2P-Regular.ttf` | https://github.com/google/fonts/tree/main/ofl/pressstart2p | SIL Open Font License 1.1. Text in `assets/fonts/PressStart2P-OFL.txt`. |
| Space Grotesk, JetBrains Mono (broadcast skin) | `assets/fonts/` | Google Fonts | SIL Open Font License 1.1, texts alongside. |

- **One sprite pack only.** Every sprite is from the Kenney pack, unmodified on
  disk. Only the ten files the game uses are committed.
- The pack has no backgrounds. The sky, city skyline, hills, road, smoke and
  panels are drawn by code, at the pack's pixel size and in the pack's
  colours, so they read as part of the same set.
- **Sound uses no assets at all**: every sound is generated at start-up.

## Arcade skin

### Palette

Only colours that occur in the pack's sprites (read from the PNGs), so drawn
shapes cannot clash with sprites. Names are the constants in `theme.py`.

| Name | Hex | Use |
|---|---|---|
| `INK` | `#2C333A` | Unlit heat blocks, car shadow. |
| `SLATE` | `#3E4751` | The road, ladder rows, dim leading zeros, unlit start lamps. |
| `ICE` | `#C8E0FF` | Panel borders, captions, lane dashes, speed lines. |
| `WHITE` / `BLACK` | `#FFFFFF` / `#000000` | Text / text shadow, panel shadow, QR code. |
| `RED`, `RED_LIGHT` | `#C03E41`, `#F55C5C` | Player 1, lit start lamps, stall warnings. |
| `YELLOW`, `YELLOW_LIGHT` | `#DC9C24`, `#FFD83D` | Player 2, titles, the clock, key names, highlighted numbers. |
| `GREEN`, `GREEN_LIGHT` | `#44B25D`, `#5AE17C` | Player 3, hills, "GO", "ready". |
| `BLUE`, `BLUE_LIGHT` | `#377DBE`, `#4A9CF0` | Skyline and panel title bars, the sky. |
| `PAGE`, `PANEL` | `INK` darkened 45% / 15% | Behind the panels / inside a panel. |

- A player's colour is the colour of that player's car: lane 1 red, lane 2
  yellow, lane 3 green (`PLAYER_COLORS`, `CAR_SPRITES`). It marks the car's
  name tag, ladder row, desync readout and result row.
- Any other tone must be a `mix()` of two palette colours.

### Scaling rules

- **One sprite pixel is 3 x 3 window pixels (`PX = 3`), everywhere.** Sprites
  are enlarged once, when loaded, with `pygame.transform.scale`, which is
  nearest-neighbour. Never `smoothscale` a sprite: it blurs the pixels.
- Scenery drawn by code is drawn small (one unit per sprite pixel) and
  enlarged by the same `PX`, so its pixels match the sprites'.
- **The arcade skin has no supersampling.** The broadcast skin draws at 2x and
  smooths down; the arcade canvas has scale 1 and draws straight onto the
  1280 x 720 window (`Canvas(window, 1)`), so nothing is ever filtered.
- The pixel font is drawn without anti-aliasing and only at sizes that are a
  multiple of 8 (8, 16, 24, 48, 72), which make whole font pixels of 1, 2, 3, 6
  and 9 window pixels. Text is therefore finer than the sprites at sizes 8 and
  16; that is deliberate, the telemetry panel would not fit otherwise.
- Sprites move in whole window pixels, not whole sprite pixels, so motion is
  smooth rather than stepping 3 px at a time.

### Type

- One face, Press Start 2P, through `ptext()` in `screens.py`: upper case, with
  a hard black shadow one font pixel down and right.
- **Tabular figures rule:** every glyph of this face is exactly one em wide,
  digits included, so no readout shifts sideways when its digits change.
- Fixed-width readouts keep their leading zeros, drawn dim (`pnumber()`).
- Text `y` is the baseline, as in the broadcast skin.

### Layout (race screen)

- Top strip: round (left), race clock (centre), transport, address and our
  name or the latest notice (right).
- Track view x = 272..1264, y = 84..447: 126 px of scenery above three lanes of
  78 px. There are always three lanes of road, also with two players.
- Scale 11 px per metre. The leader sits 60% of the way across; the world scrolls.
- Position ladder x = 16..256, beside the track.
- "Your car" panel bottom left, "Link telemetry" panel bottom right.
- Panels: `ICE` border one sprite pixel thick, hard black shadow, blue title bar.

### Motion

- **Every animated value is interpolated, never snapped:** car positions, the
  camera, speeds, heat, gaps and desync go through `canvas.ease()`
  (`current += (target - current) * 0.2` per frame). Counters are exact.
- **Parallax, three layers,** all tied to the leader's distance:
  the road, barrier, street lights, signs and gantries move 1:1 with the
  track; the hills at 0.35 of that; the city skyline and clouds at 0.10.
- **Cars:** the body bobs one sprite pixel every 3 m travelled, so the bob
  quickens with speed, and three speed lines trail behind, longer the faster.
  The pack has no wheel frames, so the wheels do not turn.
- **Stall:** the car shakes 3 px left and right at 10 Hz and a column of five
  smoke puffs rises from the bonnet; the ladder row and the driver panel say
  STALL in red. A car on the finish line is drawn stopped.
- **Track furniture:** a gantry every 50 m with the distance above it, the
  last one marked FINISH over a chequered line.
- **Start lights:** five red lamps in a panel over the track go out one at a
  time, 700 ms apart, the last at `MATCH_START`'s timestamp on the estimated
  server clock, so all clients agree. Then all five show green with "GO!" for
  600 ms.
- **Other screens** (sign-in, lobby, result) carry a strip of the same scenery
  scrolling at 14 m/s with cars on it; the room screen shows each seat's car.

### Position ladder

The most important element for an audience: position changes must be readable
from across a room.

- One row per player, in race order, left of the track. Place numbers 1, 2, 3
  are fixed; the rows move.
- Order: during a round, by the newest authoritative distance (a tie keeps
  lane order); after it, the server's finish order. `race_order()` in
  `screens.py`; the overtake sound uses the same function.
- **On an overtake the two rows slide past each other over 250 ms**
  (`LADDER_SLIDE_MS`), eased in and out with `t * t * (3 - 2t)`. A slide
  interrupted by another overtake continues from where the row is.
- **The row that gained a place flashes white, fading over 400 ms**
  (`LADDER_FLASH_MS`), and is drawn on top while the rows cross.
- A row shows: the player's colour, name, gap, speed (or STALL), a YOU mark,
  and a ten-block heat bar.
- **Gap, in seconds:** the leader shows LEADER. Others show how long ago the
  leader was where they are now, at the leader's average speed so far:
  `elapsed * (1 - own distance / leader distance)`. After the round: the
  winner's time, everyone else's gap to it from the server's times, or DNF.

### Heat

Heat is a row of blocks: green up to 60%, yellow to 80%, red above. When the
last block lights, the engine stalls. Twenty blocks for our own car in the
driver panel, ten per player in the ladder.

### Lobby and QR

The room code is drawn at 72 px. The invite QR is black on a white panel with
the standard four-module quiet zone, each module a whole number of pixels.

## The two diagnostic visuals

These are the selling point of the demo. Do not remove, hide or restyle them
into a debug overlay. Both skins have both.

### Ghost car

Every car is drawn twice:

- **Solid** at the server-authoritative position: the newest snapshot.
- **Ghost** at the client's predicted position: for our own car the local
  simulation, for others the newest snapshot carried forward at its speed.

In the arcade skin the ghost is the car's own sprite at 40% opacity with its
edge pixels solid white (`sprite(name, ghost=True)`), drawn under the solid
car. On a clean link the two overlap and a thin white rim shows at the nose.
Under UDP loss the ghost pulls ahead while snapshots are missing and snaps
back when one arrives. The distance between them on screen is the real desync
at 11 px per metre; it is never exaggerated. See
`docs/screenshots/udp_loss30_*`.

### Telemetry panel

Always visible, bottom right. It must show: RTT (current, p50, p95), the
retransmit timer, snapshots received per second, packets sent / received /
lost, bytes per second up and down, time since the last ack, discarded stale
snapshots, the desync in metres for each player in that player's colour, and
input-to-acknowledgement latency. Where a transport cannot know a value (TCP
hides loss, acks and its own timer), show `--`, never a zero. RTT, packets
lost and stale snapshots are yellow: those are the numbers the comparison is
about.

## Sound

`client/sound.py`. No audio files: each sound is a list of 16-bit samples
built with the standard library's `array` module at start-up (about 0.2 s)
and played by `pygame.mixer` at 22050 Hz mono. Hear them all with
`python -m client.sound`.

| Sound | When | How it is made |
|---|---|---|
| Engine | From the grid to the finish, our own car only | A looping sawtooth plus a square wave an octave below. One loop per whole press/second, 45 Hz at rest rising 9 Hz per press/second; the loop matching our current press rate plays, cross-fading over 40 ms. |
| Press click | Each SPACE press that counted | 14 ms of 1200 Hz square wave. |
| Countdown beep | Each of the first four lights going out | 440 Hz square, 140 ms. |
| Go | The last light going out | 880 Hz square, 450 ms: one octave up. |
| Stall | Our engine stalls | Sawtooth sliding from 320 Hz down to 70 Hz over 500 ms. |
| Overtake | The race order changes | Two rising notes, 660 then 990 Hz. |
| Finish | The round ends | C, E, G, high C. |
| Bass line | While the round is running | Sixteen eighth-notes in A minor, square wave, looped. |

- **The engine is the key-press feedback:** its pitch follows our own press
  rate, so the ear confirms every press. A press before the lights go out
  does not count and makes no sound.
- **Mute:** `--mute` starts silent. `M` toggles on the room, race and result
  screens. On the sign-in and lobby screens `M` is a letter being typed, so it
  does not toggle there. Sound is on by default.
- **No audio device:** `pygame.mixer.init` is wrapped; if it fails the client
  logs one warning and runs silent. (Checked with `SDL_AUDIODRIVER` set to a
  driver that does not exist.)
- **Never blocks:** the mixer plays on its own thread. Every call from the
  game loop only starts or stops a sound and returns at once.
- Sounds are decided in `App.update_sound()`, which compares the round with
  what it was a frame earlier (lights out, stalled, order, finished).

## Performance

Measured on the development laptop (Windows 11, Python 3.14, pygame-ce 2.5.8)
by timing every frame of `App.run()` through one real round on 127.0.0.1:
three cars, parallax, ladder slides, a stall with smoke, sound on.

| Run | Frames | Draw cost per frame, mean / p99 | Frame rate held |
|---|---|---|---|
| arcade, UDP | 681 | 2.0 ms / 2.6 ms | 60.0 fps |
| arcade, UDP, 30% loss | 679 | 2.0 ms / 2.6 ms | 59.9 fps |
| arcade, TCP | 681 | 2.0 ms / 2.6 ms | 60.0 fps |
| broadcast, UDP | 681 | 4.7 ms / 5.6 ms | 60.0 fps |

A frame has a 16.7 ms budget, so the arcade skin uses about an eighth of it.
Across the arcade runs one frame took 138 ms (once, cause not found); nothing
else exceeded 10 ms. The loop paces frames on a fixed 60 Hz timetable; between
frames it keeps reading the socket about once a millisecond.

## Known rough edges

Written to be argued with. Most important first.

1. **Nobody has played it by hand yet.** Every screen was produced by a script
   driving the real window and judged from screenshots. Key feel, typing,
   paste, copy and the `M` key are unproven by a person.
2. **Nobody has listened to the sound critically.** The sounds were generated
   and played without error on one laptop; their balance (engine against bass
   line against clicks) was not tuned by ear. Three clients in one room will
   play three bass lines that are not in step.
3. **The ghost is only as visible as the desync is large.** On loopback with
   30% loss it reaches 1.5 to 3 m (16 to 33 px) and is obvious. On a clean
   link it is a white rim a few pixels wide. Both cars are eased by the same
   constant, which softens the snap back.
4. **The wheels do not turn.** The pack has one frame per car. Motion comes
   from the bob, the speed lines and the scenery.
5. **The engine pitch moves in steps** of one press/second, cross-faded. A
   slow change of rate is heard as steps, not a glide.
6. **The overtake sound is the same** whether we gained or lost the place.
7. **Text at sizes 8 and 16 has smaller pixels than the sprites.** A purist
   will see two pixel sizes. Size 8 captions may be hard to read on a
   projector; check on the demo hardware.
8. **Long names are cut**, to 10 characters in the ladder and on name tags and
   to 6 in the desync captions. Two names with the same first 6 letters look
   alike there.
9. **Cars far behind the leader leave the track view.** More than about 54 m
   back, a car is only in the ladder.
10. **Name tags and the start-light panel can overlap** scenery text such as
    the distance over a gantry.
11. **The dim leading zeros keep their black shadow**, so they read as
    outlined digits rather than faint ones.
12. **Fixed 1280 x 720 window.** Not resizable, no fullscreen, no awareness of
    display scaling.
13. **Easing is per frame, not per second.** At a frame rate other than 60 the
    motion speeds up or slows down. The ladder slide and the smoke use the
    clock and are not affected.
14. **No mouse support**; the room list cannot be selected from.
15. **Error text comes straight from the server**, shown for 6 seconds.
16. **The QR has not been scanned with a phone.** It is now black on white
    with a quiet zone, which is the form scanners expect, but it is untested.
17. **The broadcast skin's rough edges are unchanged** (thin ghost outline,
    small heat bar, white start lights). It has sound but no position ladder.

## Broadcast skin and the fallback rule

`--skin broadcast` is the earlier design: background `#0D0F12`, foreground
`#E8EAED`, accents `#4C8DD9` / `#D98A4C` / `#5FB88F`, Space Grotesk captions,
JetBrains Mono numerals, everything drawn at 2x and scaled down with
`smoothscale`, cars as flat shapes with a 1 px outline ghost. Its functions
are the `draw_*` half of `screens.py`; the arcade ones are `arcade_*`.

It exists so that a problem with the arcade skin on demo hardware costs one
command-line flag, not the demo. Keep both working: a new piece of information
on screen is added to both skins.

## Screenshots

`docs/screenshots/` holds every screen in the arcade skin: `udp_*` and `tcp_*`
on a clean link, `udp_loss30_*` with 30% of server-to-client datagrams
dropped. Regenerate them after a visual change with
`python -m tools.screenshots` (see the top of `tools/screenshots.py`).
