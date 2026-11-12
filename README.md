# Drag Race

A 3-player button-mash drag race for our Computer Networks course. Each player
hammers SPACE; the faster you press, the faster your car goes, but press too
fast for too long and the engine overheats and stalls for two seconds. First
across the line wins the round; best of three rounds wins the match. The game
exists twice, once over TCP and once over UDP with a reliability layer we wrote
ourselves, sharing one implementation of the rules, so we can compare the two
transports with our own measurements.

For how the code is organised and the rules for changing it, read `CLAUDE.md`
(same content as `AGENTS.md`). The message reference is `docs/protocol.md`; the
visual rules are `docs/design.md`.

## Install

Python 3.11 or newer (developed and tested on 3.14).

```
pip install -r requirements.txt
python preflight.py
```

`preflight.py` checks Python, the packages, the fonts and whether port 5055 is free.

The fonts are committed in `assets/fonts/`, so there is nothing to download:
**Space Grotesk** by Florian Karsten (labels) and **JetBrains Mono** by
JetBrains (numerals). Both are under the SIL Open Font License 1.1; the licence
texts are next to the font files.

## Quickstart on one machine

Four terminals, from the repo root:

```
python -m server --transport udp --port 5055
python -m client --transport udp --name Ada
python -m client --transport udp --name Bob
python -m client --transport udp --name Cy
```

Use `--transport tcp` on all four for the TCP version. The client defaults to
`--host 127.0.0.1 --port 5055`.

In each window:

1. Press ENTER to sign in with the name shown.
2. In one window, press ENTER on the empty field to create a room. Its
   6-character code appears.
3. In the other two, type the code and press ENTER.
4. Everyone presses SPACE to ready up. With three ready players the match
   starts by itself. With two, the room creator presses ENTER.
5. When the last start light goes out, hammer SPACE.

ESC goes back a step. In a room, CTRL+C copies the invite (`host:port:code`);
pasting an invite into the join field (CTRL+V) connects to that server and
joins the room.

## The demo setup

The server runs on a VPS with a public IP. Clients run anywhere.

On the VPS, start one server per transport (they share the port number):

```
python -m server --transport tcp --host 0.0.0.0 --port 5055
python -m server --transport udp --host 0.0.0.0 --port 5055
```

Open the port for **both** protocols in **both** places:

- the cloud provider's security group / firewall: TCP 5055 and UDP 5055
- the VM's own firewall: `sudo ufw allow 5055/tcp` and `sudo ufw allow 5055/udp`

From each client machine, check the path first, then play:

```
python preflight.py --public-test --host SERVER_IP
python -m client --transport udp --host SERVER_IP --name YourName
```

One player creates a room and reads out the code; the others type it.

The server needs only the standard library. `pygame-ce` and `qrcode` are for
the client.

## Troubleshooting

**The UDP game cannot connect, but TCP works.** Almost always a firewall rule
for UDP is missing in one of the two places. This fails silently: a blocked
UDP port gives no error, the client just reports `connection lost (timeout)`
or `(unreachable)` after 2 seconds. Run the public test:

```
python preflight.py --public-test --host SERVER_IP
```

What it prints, and what each line means:

| Output | Meaning | Fix |
|---|---|---|
| `ok    TCP ...: open` / `ok    UDP ...: open` | That port is reachable from this network. | |
| `FAIL  TCP ...: REFUSED` | The VM answered, but nothing listens on that TCP port. | Start `python -m server --transport tcp`, or fix the port number. |
| `FAIL  TCP ...: NO ANSWER` | Packets to that TCP port are dropped. | Add the TCP rule to the security group and to ufw. |
| `FAIL  UDP ...: REFUSED` | The VM answered, but nothing listens on that UDP port. | Start `python -m server --transport udp`. |
| `FAIL  UDP ...: NO ANSWER after 3 probes` | Silence. Either the UDP server is not running, or UDP is blocked somewhere. | Check in order: the UDP server process; the security group's **UDP** rule; `sudo ufw allow 5055/udp`; then your own network (next item). |
| `RESULT: UDP blocked or not running` | Summary: which protocol failed. Exit status 1. | |

`REFUSED` for UDP is mostly seen from Windows clients; other systems often show
`NO ANSWER` for the same cause.

**TCP and UDP both pass from home but UDP fails on campus.** Some campus and
corporate networks filter UDP to unusual ports. Try a phone hotspot to confirm.
The fallback is UDP port 443, which such networks usually allow: run the UDP
server with `--port 443` (needs root on Linux: `sudo python -m server
--transport udp --port 443`), open 443/udp in the security group and ufw, and
pass `--port 443` to the clients.

**Why port 5055?** Port 5000, the usual choice, is taken by AirPlay Receiver on
macOS, and the server would fail to start there.

**"name is taken" after a crash.** A name stays reserved for 60 seconds after a
disconnect. Reconnecting from the same window reclaims it (ENTER on the sign-in
screen); a fresh window has to wait, or use another name.

**A player is dropped during a match.** Over UDP a client that sends nothing
for 2 seconds is dropped, and the match carries on without it. The others see
`<name> left (timeout)` in the top right corner.

## Tests

```
python -m tests.test_common
python -m tests.test_server
python -m tests.test_client
```

All three talk to real sockets on 127.0.0.1; nothing is mocked.
