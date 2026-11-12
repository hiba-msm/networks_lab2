"""Application protocol: message definitions, validation, JSON encode/decode.

A message is a plain dict with a "type" key plus the fields listed in MESSAGES,
for example {"type": "LOGIN", "name": "ada"}. On the wire it is compact JSON
(readable in Wireshark). This module knows nothing about sockets: both
transports carry the bytes produced by encode() unchanged.
"""

import json
import secrets

from common.constants import MAX_PAYLOAD_BYTES

NUMBER = (int, float)

# type -> {field: expected Python type}. A message must carry exactly these fields.
CLIENT_TO_SERVER = {
    "LOGIN":       {"name": str},
    "LIST_ROOMS":  {},
    "CREATE_ROOM": {"room_name": str},
    "JOIN_ROOM":   {"code": str},
    "LEAVE_ROOM":  {},
    "READY":       {"ready": bool},
    # Room creator only: start with fewer than a full room. A full, ready room starts by itself.
    "START_MATCH": {},
    # Default input: the TOTAL presses so far in this round. Every packet carries
    # full state, so a lost one is repaired by the next one.
    "INPUT":       {"round_number": int, "tick": int, "cumulative_presses": int, "client_time_ms": int},
    # --event-mode input: presses since the previous packet. A lost packet loses
    # those presses for good. Exists to demonstrate the naive design.
    "PRESS":       {"round_number": int, "tick": int, "count": int, "client_time_ms": int},
    # rtt_ms: the round trip the client last measured (0 = none yet). It lets the
    # server's transport time its retransmissions to this client.
    "PING":        {"t": int, "rtt_ms": int},
    "BYE":         {},
}
# Every client message also carries the session token issued in LOGIN_OK, and the
# server acts on a message only if the token matches the session of the sender.
# Before login the token is "". In LOGIN, a saved token reclaims that player_id.
for _fields in CLIENT_TO_SERVER.values():
    _fields["token"] = str

SERVER_TO_CLIENT = {
    # token: 16 random bytes as hex. Whoever holds it is this player.
    "LOGIN_OK":    {"player_id": int, "token": str, "server_time_ms": int},
    "ROOM_LIST":   {"rooms": list},        # [{room_id, code, room_name, player_count, in_match}]
    # players: [{id, name, ready}]
    "ROOM_STATE":  {"room_id": int, "code": str, "room_name": str, "creator_id": int, "players": list},
    "MATCH_START": {"round_number": int, "start_at_server_time_ms": int, "track_length": NUMBER, "players": list},
    # players: [{id, distance, speed, heat, presses, stalled}]
    "SNAPSHOT":    {"round_number": int, "tick": int, "server_time_ms": int, "players": list},
    # times_ms is in the same order as finish_order; null for a player who did not finish
    "ROUND_END":    {"round_number": int, "finish_order": list, "times_ms": list},
    # scores: [{id, wins, points}] in ranking order
    "MATCH_END":   {"final_ranking": list, "scores": list, "winner": int},
    "PLAYER_LEFT": {"player_id": int, "reason": str},
    # server_time_ms lets the client estimate the server clock (needed for the start lights)
    "PONG":        {"t": int, "server_time_ms": int},
    "ERROR":       {"code": str, "detail": str},
}

MESSAGES = {**CLIENT_TO_SERVER, **SERVER_TO_CLIENT}

# Reliability classification. Over UDP, RELIABLE messages are acked, retransmitted
# and delivered in order. UNRELIABLE ones are sent once and the newest wins: each
# is superseded by the next one a few milliseconds later, so resending a lost one
# would only deliver stale data.
UNRELIABLE = {"INPUT", "PRESS", "SNAPSHOT", "PING", "PONG"}
RELIABLE = set(MESSAGES) - UNRELIABLE

# ERROR codes
ERR_BAD_MESSAGE = "BAD_MESSAGE"
ERR_NOT_LOGGED_IN = "NOT_LOGGED_IN"
ERR_ALREADY_LOGGED_IN = "ALREADY_LOGGED_IN"
ERR_BAD_NAME = "BAD_NAME"
ERR_NAME_IN_USE = "NAME_IN_USE"
ERR_ROOM_NOT_FOUND = "ROOM_NOT_FOUND"
ERR_ROOM_FULL = "ROOM_FULL"
ERR_ALREADY_IN_ROOM = "ALREADY_IN_ROOM"
ERR_NOT_IN_ROOM = "NOT_IN_ROOM"
ERR_MATCH_IN_PROGRESS = "MATCH_IN_PROGRESS"
ERR_NOT_CREATOR = "NOT_CREATOR"
ERR_NOT_READY = "NOT_READY"


# The only message a peer without a token may open a conversation with.
BOOTSTRAP_TYPE = "LOGIN"
# Client messages whose token a transport may carry outside the JSON. LOGIN is
# excluded: its token field is data (the session to reclaim), not authentication.
TOKEN_IN_HEADER = set(CLIENT_TO_SERVER) - {BOOTSTRAP_TYPE}


def snapshot_key(snapshot):
    """Orders snapshots. A client applies one only if its key is greater than the
    key of the newest snapshot already applied; anything else is stale."""
    return (snapshot["round_number"], snapshot["tick"])


class ProtocolError(Exception):
    """Raised for any message that does not match the protocol."""


def is_reliable(msg_type):
    return msg_type in RELIABLE


def validate(msg):
    """Check msg against MESSAGES. Raises ProtocolError; returns nothing."""
    if not isinstance(msg, dict):
        raise ProtocolError("message is not a JSON object")
    msg_type = msg.get("type")
    if not isinstance(msg_type, str) or msg_type not in MESSAGES:
        raise ProtocolError(f"unknown message type: {msg_type!r}")
    schema = MESSAGES[msg_type]
    fields = set(msg) - {"type"}
    if fields != set(schema):
        raise ProtocolError(f"{msg_type}: expected fields {sorted(schema)}, got {sorted(fields)}")
    for field, expected in schema.items():
        value = msg[field]
        # bool is a subclass of int in Python, so True would pass as a number
        if isinstance(value, bool) and expected is not bool:
            raise ProtocolError(f"{msg_type}.{field}: expected a number, got a bool")
        if not isinstance(value, expected):
            raise ProtocolError(f"{msg_type}.{field}: wrong type {type(value).__name__}")
        # every integer in the protocol is an id, a counter or a timestamp
        if expected is int and value < 0:
            raise ProtocolError(f"{msg_type}.{field}: must not be negative")


def encode(msg, omit_token=False):
    """dict -> bytes. Validates first, so a malformed message never leaves this process.
    omit_token leaves the token out of the JSON, for a transport that carries it itself."""
    validate(msg)
    if omit_token:
        msg = {key: value for key, value in msg.items() if key != "token"}
    data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_PAYLOAD_BYTES:
        raise ProtocolError(f"{msg['type']} is {len(data)} bytes, limit is {MAX_PAYLOAD_BYTES}")
    return data


def decode(data, token=None):
    """bytes -> dict. Everything from the network goes through here before it is trusted.
    token: the sender's token as the transport authenticated it. It is put into
    client messages, replacing anything the sender wrote there itself."""
    if len(data) > MAX_PAYLOAD_BYTES:
        raise ProtocolError(f"payload is {len(data)} bytes, limit is {MAX_PAYLOAD_BYTES}")
    try:
        msg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"not valid JSON: {exc}") from exc
    if token is not None and isinstance(msg, dict) and msg.get("type") in TOKEN_IN_HEADER:
        msg["token"] = token
    validate(msg)
    return msg


# --- Room codes --------------------------------------------------------------

ROOM_CODE_LEN = 6
# Uppercase letters and digits without the look-alikes 0/O and 1/I/L.
ROOM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def make_room_code():
    return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LEN))


def normalize_room_code(text):
    """Accept what a person types: any case, surrounding spaces."""
    return text.strip().upper()


def is_room_code(text):
    return len(text) == ROOM_CODE_LEN and all(c in ROOM_CODE_ALPHABET for c in text)


def format_join_string(host, port, code):
    """The string the lobby QR encodes."""
    return f"{host}:{port}:{code}"


def parse_join_string(text):
    """'host:port:code' -> (host, port, code). Raises ValueError with a readable reason."""
    # rsplit so that an IPv6 host, which contains colons itself, stays in one piece
    parts = text.strip().rsplit(":", 2)
    if len(parts) != 3 or not parts[0]:
        raise ValueError("expected host:port:code")
    host, port_text, code = parts[0], parts[1], normalize_room_code(parts[2])
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValueError(f"bad port: {port_text!r}")
    if not is_room_code(code):
        raise ValueError(f"bad room code: {code!r}")
    return host, int(port_text), code
