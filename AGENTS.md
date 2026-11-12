# Drag Race: project guide for coding agents

> This file exists twice, as `CLAUDE.md` and `AGENTS.md`, with identical content.
> If you change one, copy it over the other in the same commit.

## What this is

A university Computer Networks course project by a team of 4. It is a 3-player
button-mash drag race implemented twice: once over TCP and once over UDP, with
one shared game implementation. It is graded on **socket programming quality**
and on a **TCP vs UDP comparison backed by our own measurements**. Visual polish
is not graded, but we want it to look professional.

**The demo:** live, on 4 computers. One runs the server (a VPS with a public
IP), three run clients from any network and join the same room by a 6-character
code. We play a match on each transport, then show the comparison.

**Demo date:** not recorded here yet. Ask the team lead and write it into this line.

We have to defend every line of this code to an examiner. That shapes
everything below.

## Non-negotiables

Read these before you change anything. A change that breaks one of them is
wrong even if every test passes.

1. **Raw standard-library `socket` only.** No websockets, no HTTP, no asyncio
   frameworks, no networking libraries, no game-networking libraries.
   `threading` and `selectors` are allowed. The only third-party packages are
   `pygame-ce` (window and sound), `qrcode` (lobby QR) and `matplotlib` (report charts).
2. **The UDP reliability layer is ours and is the graded artifact.** It lives in
   `transport/udp_transport.py`. Never replace it with a library. Never
   "simplify" it away, and never route UDP traffic around it.
3. **One game implementation, two transports.** `common/` and `server/server.py`
   and `server/room.py` and `client/core.py` must never import a concrete
   transport. `test_game_logic_never_imports_a_transport` enforces this.
4. **Identical rules and features in both versions.** Only the transport
   differs. If a feature needs something from the network layer, add it to the
   transport interface and implement it in both transports. Never write
   `if transport is udp` in game, server or client code.
5. **Never mock, stub or simulate networking**, in code or in tests. Tests talk
   to a real server over real sockets on 127.0.0.1.
6. **No silently swallowed exceptions.** Every `except` either handles the
   error in a way a comment explains, or counts and logs it.

## Architecture

```
common/      protocol.py   message table, validation, JSON encode/decode, room codes
             game.py       the rules: Car, Race (one round), Match (best of 3). Pure.
             constants.py  every tuning value, shared by everything
transport/   base.py           the Transport interface
             tcp_transport.py  length-prefixed framing over stream sockets
             udp_transport.py  datagrams + our reliability layer
server/      server.py     login, sessions, lobby, the 30 Hz loop, disconnects
             room.py       one room: members and the round/match state machine
             __main__.py   command line; the only server file naming a transport
client/      core.py       headless client: connection, state, prediction, telemetry
             theme.py      palettes, fonts, sprite loading, the canvas (both skins)
             screens.py    what each screen draws: draw_* (broadcast skin), arcade_* (arcade skin)
             sound.py      every sound, generated at start-up; no audio files
             app.py        window, keyboard, which screen is showing, when a sound plays
             __main__.py   command line; the only client file naming a transport
tests/       test_common.py  protocol and rules (no network)
             test_server.py  end to end over real sockets, both transports
             test_client.py  three headless clients play a match, both transports
tools/       heat_sim.py     balance table for the engine-heat mechanic
             screenshots.py  plays a real match and regenerates docs/screenshots/
preflight.py   pre-demo checks, local and against a public server
assets/fonts/    Press Start 2P (arcade), Space Grotesk, JetBrains Mono (broadcast), OFL licences
assets/sprites/  ten sprites from Kenney's Pixel Vehicle Pack (CC0), and its licence
docs/          protocol.md, design.md, screenshots/
```

**Dependency direction** (an arrow means "may import"):

```
server/__main__, client/__main__  ->  transport/*  ->  transport/base  ->  common/
server/server, server/room        ->  common/            (handed a transport object)
client/core                       ->  common/            (handed a transport object)
client/app, client/screens        ->  client/core, client/theme, client/sound, common/
```

`common/` imports nothing of ours. Nothing imports `server/` or `client/`
except their own `__main__` and the tests.

**The transport interface** (`transport/base.py`, class `Transport`):

| Method | Meaning |
|---|---|
| `send(peer, message)` | Sends with the reliability class the protocol assigns to that message type. Callers use this, not the two below. |
| `send_reliable(peer, message)` | Must arrive, in order. |
| `send_unreliable(peer, message)` | Sent once; may be lost. |
| `recv(timeout)` | Does all socket work and timers. Returns `[(peer, message)]`. Must be called every few tens of milliseconds. |
| `on_disconnect(callback)` | `callback(peer, reason)` when a peer is lost. |
| `authenticate(peer, token)` | Binds the session token to the peer after login. Both sides call it. |
| `rtt_sample(peer, rtt_ms)` | A PING/PONG round-trip time, for the retransmit timer. No-op over TCP. |
| `stats(peer=None)` | Counters for the HUD and the benchmarks. |
| `close_peer(peer)`, `close()` | Drop one peer on purpose; shut down. |

A `peer` is an opaque integer. A message is a plain dict with a `"type"` key
(see `docs/protocol.md`). Everything is single-threaded: one loop calls
`recv()` and handles messages one at a time. That is why two clients acting "at
the same moment" cannot corrupt shared state, and it is the answer we give the
examiner about simultaneous players.

**Adding a feature** means: add or change a message in `common/protocol.py`
(table, reliability class, `docs/protocol.md`), handle it in `server/` and
`client/core.py`, and if it needs anything from the network layer, extend
`Transport` and implement it in both transports. It never means branching on
the transport type.

## Design decisions you must not undo

Each of these looks like something to "clean up". Each is deliberate.

- **Clients send the cumulative press count, not press events.** Every INPUT
  carries full state, so a lost UDP packet costs nothing; the next one repairs
  it. `--event-mode` (PRESS messages) exists only to demonstrate the naive
  design losing presses under packet loss.
- **A peer's identity is its session token, never `(ip, port)`.** A valid token
  from a new address re-binds the peer, which is how a client survives its NAT
  changing the mapping mid-match. Every rebind is logged.
- **The token travels in the UDP header only.** The UDP transport strips it
  from the JSON on send and injects the authenticated one on receive, so
  `server.py` reads `message["token"]` identically for both transports. This
  saves 43 bytes per input packet (160 down to 117). LOGIN keeps its JSON
  token: there it is data (the session to reclaim), not authentication.
- **The retransmit timer (RTO) is capped at 500 ms** and derived from measured
  RTT (RFC 6298 style, clamped to 100..500 ms, doubling per resend). TCP backs
  off without limit; a game prefers failing fast over eventual delivery.
- **A reliable message unacked for 2000 ms drops the peer.** That is
  `PEER_TIMEOUT_MS`, the same bound as for silence, so there is one answer to
  "how long until a dead peer is noticed".
- **Stale snapshots are discarded in the client (`client/core.py`), not in the
  transport.** Only snapshots should be dropped when late. A late PONG is still
  a valid RTT sample, and a late PRESS is still a press.
- **Before an address is proven, the server sends it at most 3x the bytes it
  received from it.** A UDP source address can be forged; without the cap, one
  small forged LOGIN would make us send retransmissions at a victim. An address
  is proven when a datagram arrives carrying the token we sent there.
- **Unauthenticated datagrams are rate limited per source IP (30 per second),
  counted and dropped. They never disconnect anyone.** Garbage from the
  internet must not be able to kick a player.
- **LOGIN is the only message that can create a peer over UDP.** Anything else
  from an unknown address is dropped without a reply.
- **A full room (3 ready players) starts by itself; 2 ready players need
  START_MATCH from the room creator.** Otherwise two players who ready up early
  start without the third.
- **Peers silent for 2 s are dropped over TCP too.** A killed process closes
  its socket, but a pulled cable sends nothing, and TCP alone takes minutes to
  notice. The client's 250 ms PING is the heartbeat.
- **A malformed TCP frame disconnects that peer** (`protocol_error`): a byte
  stream cannot be re-synchronised after a bad length. A malformed UDP datagram
  is just dropped.
- **Finish times are interpolated inside the tick**, so gaps are finer than the
  33 ms tick and identical inputs give identical times.
- **The server clamps the press rate at 20 per second.** A modified client
  cannot send a huge count and teleport.
- **Names stay reserved for 60 s after a disconnect, then free up.** With the
  token, the same player_id is reclaimed; without it the name is "taken".
- **A stall lasts 1.2 s, not 2.** At 2 s a steady 8 presses/s beat mashing, so
  the best strategy was not to mash. At 1.2 s one stall at 10/s beats 8/s by
  about half a second, and two stalls at 12/s do not beat one. Check any
  change to the heat constants with `python -m tools.heat_sim`.
- **The bass line is off unless the client is started with `--music`.** Every
  other sound reports something about the race and is on by default.

## How to run everything

Python 3.11 or newer (developed on 3.14). From the repo root:

```
pip install -r requirements.txt

# server: one process per transport; both can share the port number
python -m server --transport tcp --host 0.0.0.0 --port 5055
python -m server --transport udp --host 0.0.0.0 --port 5055

# client (a window); repeat on each machine, or three times on one
python -m client --transport udp --host 127.0.0.1 --port 5055 --name Ada
python -m client --transport tcp --host 127.0.0.1 --port 5055 --name Ada
python -m client --transport udp --host 127.0.0.1 --event-mode     # the naive input design
python -m client --transport udp --skin broadcast --mute           # the plain look, sound off
python -m client --transport udp --music                           # adds the bass line (off by default)
python -m client.sound                                             # plays every sound in turn

# tests: run all three before saying anything works
python -m tests.test_common          # protocol and rules, under a second
python -m tests.test_server          # 48 end-to-end tests, about a minute
python -m tests.test_server udp_     # only tests whose name contains "udp_"
python -m tests.test_client          # three headless clients, both transports

# pre-demo checks
python preflight.py                                   # this machine
python preflight.py --public-test --host SERVER_IP    # reachability of both ports

# figure generators
python -m tools.heat_sim --stall-seconds 1.0 1.2 1.5 2.0   # finish time per press rate
python -m tools.screenshots --transport udp               # also: tcp, udp --loss 0.3, udp --skin broadcast
```

There is no bot client yet (see status). Until there is, a headless client is
`client.core.GameClient` driven from a script, as `tests/test_client.py` does.

Client keys: ENTER confirms, ESC goes back. In a room, SPACE toggles ready,
ENTER starts (creator, with 2 players), CTRL+C copies the invite. In a race,
hammer SPACE. M toggles sound in the room, the race and the result (on the
name and lobby screens M is a letter).

The client has two skins, `--skin arcade` (default, pixel art) and
`--skin broadcast` (the plain fallback). They differ in drawing only; see
`docs/design.md`. Anything new on screen goes into both.

## Project status

Update this section as the last step of any work. Last updated: 2026-11-12.

**Complete and tested**
- Protocol and shared game logic (`common/`).
- TCP transport with length-prefixed framing.
- UDP transport with the reliability layer: sequence numbers with wraparound,
  piggybacked cumulative acks, RTT-derived retransmission, duplicate
  suppression, ordered delivery for reliable messages, heartbeats, timeout,
  token identity with address rebinding, rate limiting, amplification cap, probe.
- Server: login, session tokens, lobby, room codes, start rules, rounds and
  match, every kind of disconnect.
- Client: headless core (clock sync, prediction, reconciliation, telemetry) and
  the pygame window (lobby, QR invite, start lights, ghost car, telemetry panel).
- Arcade skin (the default) and sound, merged into `main`: sprite cars,
  three-layer parallax, position ladder, generated sound, `--skin`, `--mute`
  and `--music`. A scripted match ran in the real window on both transports
  at 60 fps (numbers in `docs/design.md`). The broadcast skin is kept as the
  fallback.
- Heat balance: the stall is 1.2 s, chosen with `tools/heat_sim.py`.
- `docs/screenshots/`: 36 PNGs of the current build, every screen in the
  arcade skin (UDP, TCP, UDP with 30% loss) and in the broadcast skin (UDP).
- `preflight.py`, local and `--public-test`.

**In progress**
- A human has not yet played a full match by hand, in either skin. The window
  was driven by a script and checked from screenshots. Typing, CTRL+V, CTRL+C
  and M are untested by hand, and nobody has judged the sound by ear.
- The lobby QR has not been scanned with a phone. The arcade skin draws it
  black on a white panel with a quiet zone; the broadcast skin still draws it
  light-on-dark, which some scanners cannot read.
- `preflight.py` does not check `assets/sprites/` or the Press Start 2P font
  yet; a missing file would only show when the client starts.

**Not started**
- `bots/bot_client.py`: headless bot with scripted press patterns. It must run
  `client.core.GameClient`, not a separate fake client.
- Impairment flags in both transports: `--loss`, `--latency`, `--jitter`,
  `--bandwidth`. In UDP the hook is `UdpTransport._transmit`.
- `bench/`: the benchmark harness, the loss x latency matrix, CSV output.
- `bench/report.md` and its PDF, with matplotlib charts, and the slide outline.
- `deploy/`: systemd unit, ufw commands, security group rules, one setup script.

## House rules for agents

- **Run all three test suites before claiming anything works.** Paste the
  final line of each. "Should work" is not a result.
- **Never mock or simulate networking.** If something is hard to test over real
  sockets, wrap `UdpTransport._transmit` to lose, duplicate or reorder real
  datagrams, as `tests/test_server.py` does.
- **When a test fails, decide explicitly whether the test or the code is
  wrong.** Say which one you changed and why. Never loosen an assertion just to
  get a pass.
- **Prefer explainable code over clever code.** Plain names, short functions, a
  comment wherever the reason is not obvious. If you cannot explain a line to
  an examiner, rewrite it.
- **Comments say why, not what.** Match the density of the file you are in.
- **Keep the docs true.** A protocol change updates `docs/protocol.md`; a
  visual change updates `docs/design.md`; any work updates the status section
  above, in both `CLAUDE.md` and `AGENTS.md`.
- **Do not add dependencies.** If you think you need one, you probably need
  twenty lines of standard library instead. Ask the team first.
- **Never commit** tokens, keys, server IP addresses, SSH details or absolute
  paths from your own machine. The VPS address is passed with `--host` at run time.
- Engine "heat" is the overheating mechanic. One race is a "round". Do not call
  a round a heat.
