"""The window: keyboard in, pictures out. All game and network state lives in
client/core.py; this file only decides which screen to show and turns keys into
calls on the core.
"""

import logging
import time

import pygame

from client import screens
from client.core import GameClient, now_ms
from client.theme import H, W, Canvas
from common import protocol
from common.constants import MAX_NAME_LEN, MAX_ROOM_NAME_LEN

log = logging.getLogger("client")

RESULT_DELAY_MS = 2500  # the last round's finish stays up this long before the classification


class TextField:
    def __init__(self, max_len, text=""):
        self.max_len = max_len
        self.text = text

    def type(self, text):
        self.text = (self.text + "".join(ch for ch in text if ch.isprintable()))[:self.max_len]

    def backspace(self):
        self.text = self.text[:-1]


class App:
    def __init__(self, transport_class, transport_name, host, port, name="", event_mode=False):
        pygame.init()
        self.window = pygame.display.set_mode((W, H))
        pygame.display.set_caption("Drag Race")
        self.canvas = Canvas(self.window)
        self.transport_class = transport_class
        self.transport_name = transport_name
        self.host, self.port = host, port
        self.event_mode = event_mode
        self.client = None
        self.saved_token = ""      # lets us reclaim our player after a lost connection
        self.join_after_login = None  # room code to join once LOGIN_OK arrives
        self.message = None        # (local ms, text) shown under the active field
        self.lost_reported = False  # so a lost connection is announced once
        self.name_field = TextField(MAX_NAME_LEN, name)
        self.join_field = TextField(64)
        self.shown_round = None    # to notice when a new round starts
        self.last_list_ms = 0
        self.running = True

    @property
    def transport_label(self):
        return self.transport_name + (" event mode" if self.event_mode else "")

    def say(self, text):
        self.message = (now_ms(), text)

    # --- which screen ---------------------------------------------------------------

    def screen(self):
        c = self.client
        if c is None or c.player_id is None or c.lost:
            return "name"
        if c.round is not None:
            if c.match_result and now_ms() - c.round.ended_at_ms > RESULT_DELAY_MS:
                return "result"
            return "race"
        return "room" if c.room else "lobby"

    # --- connecting -----------------------------------------------------------------

    def connect(self, host, port, join_code=None):
        name = self.name_field.text.strip()
        if not name:
            self.say("enter a name first")
            return
        if self.client is not None:
            self.client.bye()
        if (host, port) != (self.host, self.port):
            self.saved_token = ""  # a token means nothing to a different server
        self.host, self.port = host, port
        try:
            transport = self.transport_class.connect(host, port)
        except OSError as exc:  # refused, unreachable, unknown host, timed out
            log.warning("cannot connect to %s:%s: %s", host, port, exc)
            self.client = None
            self.say(f"cannot reach {host}:{port} ({exc.strerror or exc})")
            return
        self.client = GameClient(transport, self.event_mode)
        self.lost_reported = False
        self.client.login(name, self.saved_token)
        self.join_after_login = join_code

    def join(self, text):
        """The join field: empty creates a room, a code joins one on this server,
        a pasted host:port:code invite goes to that server first."""
        text = text.strip()
        if not text:
            self.client.create_room(f"{self.client.name}'s room"[:MAX_ROOM_NAME_LEN])
        elif ":" in text:
            try:
                host, port, code = protocol.parse_join_string(text)
            except ValueError as exc:
                self.say(f"not a valid invite: {exc}")
                return
            if (host, port) == (self.host, self.port):
                self.client.join_room(code)
            else:
                self.connect(host, port, join_code=code)
        elif protocol.is_room_code(protocol.normalize_room_code(text)):
            self.client.join_room(protocol.normalize_room_code(text))
        else:
            self.say("a room code is 6 letters and digits")
            return
        self.join_field.text = ""

    # --- input ----------------------------------------------------------------------

    def on_key(self, event, screen):
        c = self.client
        ctrl = event.mod & pygame.KMOD_CTRL
        active_field = {"name": self.name_field, "lobby": self.join_field}.get(screen)
        if active_field is not None and event.key == pygame.K_BACKSPACE:
            active_field.backspace()
        elif active_field is not None and ctrl and event.key == pygame.K_v:
            active_field.type(pygame.scrap.get_text() or "")
        elif screen == "name":
            if event.key == pygame.K_RETURN:
                self.connect(self.host, self.port)
            elif event.key == pygame.K_ESCAPE:
                self.running = False
        elif screen == "lobby":
            if event.key == pygame.K_RETURN:
                self.join(self.join_field.text)
            elif event.key == pygame.K_ESCAPE:
                c.bye()
                self.client, self.saved_token = None, ""
        elif screen == "room":
            me = next(p for p in c.room["players"] if p["id"] == c.player_id)
            if event.key == pygame.K_SPACE:
                c.set_ready(not me["ready"])
            elif event.key == pygame.K_RETURN:
                c.start_match()
            elif event.key == pygame.K_ESCAPE:
                c.leave_room()
            elif ctrl and event.key == pygame.K_c:
                pygame.scrap.put_text(protocol.format_join_string(self.host, self.port, c.room["code"]))
                self.say("invite copied")
        elif screen == "race":
            if event.key == pygame.K_SPACE:
                c.press()
            elif event.key == pygame.K_ESCAPE:
                c.leave_room()
        elif screen == "result":
            if event.key in (pygame.K_RETURN, pygame.K_ESCAPE):
                c.round = c.match_result = None  # back to the room, which the server already put us in

    # --- one frame ------------------------------------------------------------------

    def frame(self):
        screen = self.screen()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self.on_key(event, screen)
            elif event.type == pygame.TEXTINPUT and screen in ("name", "lobby"):
                (self.name_field if screen == "name" else self.join_field).type(event.text)

        c = self.client
        if c is not None:
            c.update()
            if c.error is not None:  # show the server's reason, once
                self.say(c.error["detail"])
                c.error = None
            if c.lost and c.lost != "closed_by_us" and not self.lost_reported:
                # Also covers a server that never answered (UDP has no "refused").
                self.say(f"connection lost ({c.lost}); ENTER reconnects")
                self.lost_reported = True
            if c.player_id is not None:
                self.saved_token = c.token
                if self.join_after_login:
                    c.join_room(self.join_after_login)
                    self.join_after_login = None
                if self.screen() == "lobby" and now_ms() - self.last_list_ms > 2000:
                    self.last_list_ms = now_ms()
                    c.list_rooms()
            if c.round is not self.shown_round:
                self.shown_round = c.round
                self.canvas.forget_eased()  # a new round: cars start from the line, not from where they were

        self.canvas.clear()
        getattr(screens, "draw_" + self.screen())(self.canvas, self)
        self.canvas.present()

    def run(self):
        while self.running:
            next_frame = time.monotonic() + 1 / 60
            self.frame()
            # Between frames, keep serving the network about once a millisecond.
            # Reading the socket only once per frame would add up to 16 ms to
            # every RTT sample and to every input we send.
            while time.monotonic() < next_frame:
                if self.client is not None:
                    self.client.update()
                time.sleep(0.001)
        if self.client is not None:
            self.client.bye()
        pygame.quit()
