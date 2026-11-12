# Protocol reference

The source of truth is `common/protocol.py`. This page mirrors it; change both
together. `tests/test_common.py` fails if a message type has no sample or no
reliability class.

## Messages

A message is a JSON object with a `"type"` and exactly the fields listed here:
no missing fields, no extra ones. Integers are never negative. One encoded
message is at most 1100 bytes (`MAX_PAYLOAD_BYTES`).

**Reliable** messages must arrive, in order. **Unreliable** ones are sent once;
a lost one is superseded by the next a few milliseconds later, so resending it
would only deliver stale data. Over TCP both classes are sent the same way.
Over UDP, reliable messages are acked and retransmitted.

### Client to server

Every client message also carries `token` (string): the session token from
`LOGIN_OK`, or `""` before login. It is omitted from the tables below.

| Type | Fields | Class | Meaning |
|---|---|---|---|
| `LOGIN` | `name` | reliable | Register or log in. With a saved `token`, reclaims that `player_id`. |
| `LIST_ROOMS` | | reliable | Ask for `ROOM_LIST`. |
| `CREATE_ROOM` | `room_name` | reliable | Create a room and join it as creator. |
| `JOIN_ROOM` | `code` | reliable | Join by 6-character code (any case, spaces trimmed). |
| `LEAVE_ROOM` | | reliable | Leave the room, also mid-match. |
| `READY` | `ready` (bool) | reliable | Set or withdraw ready. A full, ready room starts. |
| `START_MATCH` | | reliable | Creator only: start with 2 ready players. |
| `INPUT` | `round_number`, `tick`, `cumulative_presses`, `client_time_ms` | unreliable | Total presses so far this round. Sent every tick. |
| `PRESS` | `round_number`, `tick`, `count`, `client_time_ms` | unreliable | `--event-mode` only: presses since the last packet. |
| `PING` | `t`, `rtt_ms` | unreliable | Heartbeat, RTT probe and clock sync. `rtt_ms` is the client's last measured RTT (0 = none). |
| `BYE` | | reliable | Leaving on purpose. |

### Server to client

| Type | Fields | Class | Meaning |
|---|---|---|---|
| `LOGIN_OK` | `player_id`, `token`, `server_time_ms` | reliable | `token` is 16 random bytes as hex. |
| `ROOM_LIST` | `rooms`: `[{room_id, code, room_name, player_count, in_match}]` | reliable | At most 10 rooms. |
| `ROOM_STATE` | `room_id`, `code`, `room_name`, `creator_id`, `players`: `[{id, name, ready}]` | reliable | Sent to the room on every change. |
| `MATCH_START` | `round_number`, `start_at_server_time_ms`, `track_length`, `players`: `[{id, name}]` | reliable | Opens every round. The start time is in the future, on the server's clock. |
| `SNAPSHOT` | `round_number`, `tick`, `server_time_ms`, `players`: `[{id, distance, speed, heat, presses, stalled}]` | unreliable | Authoritative state, 30 per second. |
| `ROUND_END` | `round_number`, `finish_order`, `times_ms` | reliable | `times_ms` matches `finish_order`; `null` = did not finish. |
| `MATCH_END` | `final_ranking`, `scores`: `[{id, wins, points}]`, `winner` | reliable | Then the room is back in the lobby. |
| `PLAYER_LEFT` | `player_id`, `reason` | reliable | Sent to the room, including the leaver. |
| `PONG` | `t`, `server_time_ms` | unreliable | Echoes `t`. |
| `ERROR` | `code`, `detail` | reliable | A request was refused. |

`PLAYER_LEFT.reason` is one of: `left` (LEAVE_ROOM), `bye`, `replaced` (the
token holder logged in again), or a transport reason: `closed`, `reset`,
`timeout`, `unreachable`, `protocol_error`, `send_buffer_full`.

### Error codes

| Code | When |
|---|---|
| `BAD_MESSAGE` | A server-to-client message type was sent by a client. |
| `NOT_LOGGED_IN` | No valid token for this connection. |
| `ALREADY_LOGGED_IN` | LOGIN on a connection that is already logged in. |
| `BAD_NAME` | Player or room name empty, too long (16 / 24) or unprintable. |
| `NAME_IN_USE` | The name belongs to a session and the token does not match. |
| `ROOM_NOT_FOUND` | No room has that code. |
| `ROOM_FULL` | The room already has 3 players. |
| `ALREADY_IN_ROOM` | CREATE_ROOM or JOIN_ROOM while in a room. |
| `NOT_IN_ROOM` | LEAVE_ROOM, READY or START_MATCH outside a room. |
| `MATCH_IN_PROGRESS` | JOIN_ROOM, READY or START_MATCH while a match is running. |
| `NOT_CREATOR` | START_MATCH from someone other than the room creator. |
| `NOT_READY` | START_MATCH with fewer than 2 players or someone not ready. |

## TCP framing

```
offset  size  field
0       4     payload length, big-endian unsigned (1..1100)
4       n     payload: one JSON message
```

TCP is a byte stream: one `send()` can arrive split across several `recv()`
calls, and several can arrive merged. The receiver buffers bytes per connection
and takes out complete frames. A length of 0 or above 1100, or a payload that
does not decode, disconnects that peer (`protocol_error`).

## UDP datagram

One datagram is a 25-byte header followed by at most one JSON message. The
largest datagram is 25 + 1100 = 1125 bytes, under the 1200-byte cap that keeps
us below typical internet path MTU. All multi-byte fields are big-endian.
`struct` format: `!2sBHHH16s`.

```
offset  size  field
0       2     magic   b"DR"; anything else is dropped
2       1     kind    0 = ack / heartbeat (no payload)
                      1 = unreliable message
                      2 = reliable message
3       2     pkt     number of this datagram, +1 per datagram sent to this peer
5       2     seq     sequence number of this reliable message (kind 2 only)
7       2     ack     next reliable seq expected from the other side
9       16    token   session token from LOGIN_OK; all zero before login
25      n     payload one JSON message (kinds 1 and 2)
```

- `pkt`, `seq` and `ack` are 16-bit and wrap around. Compare them only with
  `seq_diff()`.
- `ack` is cumulative: it acknowledges every reliable message before it. Every
  datagram carries it. If nothing is going the other way, an ack-only datagram
  is sent at once.
- Reliable messages are resent when unacked for one RTO. RTO = smoothed RTT +
  4 x RTT variation (RFC 6298), clamped to 100..500 ms, 200 ms before the first
  sample. The wait doubles after each resend of the same message, up to 500 ms.
  Unacked for 2000 ms: the peer is dropped (`unreachable`).
- The receiver delivers reliable messages in `seq` order, holding early ones
  back, and drops repeats. Unreliable messages are delivered as they arrive.
- Repeated datagrams (same `pkt` among the last 64) are dropped.
- A heartbeat (kind 0) is sent after 250 ms without sending anything. No
  datagram received for 2000 ms: the peer is dropped (`timeout`).
- **Token.** For client messages other than LOGIN, the `token` field is not in
  the JSON on the wire. The server side of the transport writes the header's
  token into the decoded message (as hex, or `""` if all zero), replacing
  anything the sender put there.
- **Who is accepted (server).** A known token: accepted from any address, and
  the peer moves to that address if the datagram is newer than any seen. An
  all-zero token: accepted only as a reliable LOGIN from a new address, or from
  an address whose LOGIN_OK may still be in flight. Everything else is counted
  and dropped. Unauthenticated datagrams are limited to 30 per source IP per second.
- **Amplification cap.** Until a datagram with the right token arrives from an
  address, the server sends that address at most 3x the bytes it received from it.

### Probe

`preflight.py --public-test` sends `b"DRAGRACE-PROBE-1"` plus 8 random bytes.
The UDP server echoes the datagram unchanged. Probes over 32 bytes are ignored,
and each source IP gets one reply per second.

## Adding a message

1. Add it to `CLIENT_TO_SERVER` or `SERVER_TO_CLIENT` in `common/protocol.py`.
2. If it may be lost, add it to `UNRELIABLE`. Otherwise it is reliable.
3. Add a sample to `SAMPLES` in `tests/test_common.py`.
4. Handle it in `server/server.py` (`self.handlers`) or `client/core.py`
   (a method named `_on_<type in lower case>`).
5. Add it to this page.

Do not touch either transport for a new message. They carry any message in the table.
