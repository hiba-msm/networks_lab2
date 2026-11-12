"""The look: palettes, type, sprites and the drawing surface.

There are two skins. "broadcast" is drawn on a canvas twice the window size and
scaled down once per frame, which anti-aliases every edge. "arcade" is pixel
art, where smoothing is the enemy: it draws straight onto the window at 1x and
enlarges sprites by a whole factor with nearest-neighbour scaling.
Coordinates everywhere are in window pixels (1280 x 720); the canvas scales them.
"""

import pathlib

import pygame

W, H = 1280, 720
S = 2  # supersampling factor


def mix(a, b, t):
    """Colour t of the way from a to b."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


BG = (13, 15, 18)        # #0D0F12
FG = (232, 234, 237)     # #E8EAED
DIM = mix(BG, FG, 0.55)  # secondary text
HAIR = mix(BG, FG, 0.12)  # hairlines
FAINT = mix(BG, FG, 0.05)  # far background layer
ACCENTS = [(76, 141, 217), (217, 138, 76), (95, 184, 143)]  # #4C8DD9 #D98A4C #5FB88F

# The arcade palette: only colours that occur in the sprite pack, so drawn
# shapes and sprites cannot clash. Anything else is a mix() of two of these.
BLACK, WHITE = (0, 0, 0), (255, 255, 255)
INK, SLATE, ICE = (44, 51, 58), (62, 71, 81), (200, 224, 255)   # #2C333A #3E4751 #C8E0FF
RED, RED_LIGHT = (192, 62, 65), (245, 92, 92)                   # #C03E41 #F55C5C
YELLOW, YELLOW_LIGHT = (220, 156, 36), (255, 216, 61)           # #DC9C24 #FFD83D
GREEN, GREEN_LIGHT = (68, 178, 93), (90, 225, 124)              # #44B25D #5AE17C
BLUE, BLUE_LIGHT = (55, 125, 190), (74, 156, 240)               # #377DBE #4A9CF0
PAGE = mix(INK, BLACK, 0.45)   # behind the panels
PANEL = mix(INK, BLACK, 0.15)  # inside a panel
PLAYER_COLORS = [RED_LIGHT, YELLOW_LIGHT, GREEN_LIGHT]          # by lane, matching the car
CAR_SPRITES = ["sports_red", "sports_yellow", "sports_green"]   # by lane

ASSETS = pathlib.Path(__file__).parent.parent / "assets"
FONT_DIR = ASSETS / "fonts"
FONTS = {
    "sans": "SpaceGrotesk-Medium.ttf",    # broadcast labels
    "mono": "JetBrainsMono-Regular.ttf",  # broadcast numerals: all digits are the same width
    # Arcade, everything. Every glyph is exactly one em wide (so it is tabular
    # too) and is drawn on an 8 x 8 grid: use sizes 8, 16, 24, 48 only, which
    # make whole font pixels of 1, 2, 3 and 6 screen pixels.
    "pixel": "PressStart2P-Regular.ttf",
}

PX = 3  # one sprite pixel is 3 x 3 window pixels, for every sprite and every drawn scenery shape
_sprites = {}


def sprite(name, ghost=False):
    """A sprite from assets/sprites, enlarged PX times. pygame.transform.scale
    is nearest-neighbour, which keeps the pixels square and hard; smoothscale
    would blur them.

    ghost=True is the predicted car: the same sprite at 40% opacity, with the
    pixels on its edge solid white. The white edge is what shows when the
    ghost is only a few pixels ahead of the solid car."""
    if (name, ghost) not in _sprites:
        image = pygame.image.load(str(ASSETS / "sprites" / f"{name}.png")).convert_alpha()
        if ghost:
            edge = pygame.mask.from_surface(image).outline()  # before the fade: it needs the opaque shape
            image.fill((255, 255, 255, round(255 * 0.4)), special_flags=pygame.BLEND_RGBA_MULT)
            for point in edge:
                image.set_at(point, WHITE)
        _sprites[name, ghost] = pygame.transform.scale(image, (image.get_width() * PX, image.get_height() * PX))
    return _sprites[name, ghost]

EASE = 0.2  # every animated value moves this share of the way to its target per frame


class Canvas:
    def __init__(self, window, scale=S):
        self.window = window
        self.s = scale  # 2: supersampled (broadcast). 1: straight onto the window (arcade).
        self.surface = window if scale == 1 else pygame.Surface((W * scale, H * scale)).convert()
        self._fonts = {}
        self._glyphs = {}
        self._eased = {}
        self.memo = {}  # what a screen remembers between frames (ladder rows in motion)

    def clear(self):
        self.surface.fill(BG)

    def present(self):
        if self.surface is not self.window:
            pygame.transform.smoothscale(self.surface, self.window.get_size(), self.window)
        pygame.display.flip()

    def ease(self, key, target):
        """The smoothed version of a changing value: current += (target - current) * 0.2."""
        current = self._eased.get(key, target)
        current += (target - current) * EASE
        self._eased[key] = current
        return current

    def forget_eased(self):
        self._eased.clear()
        self.memo.clear()

    # --- text --------------------------------------------------------------------

    def _glyph(self, font, size, char, color):
        key = (font, size, char, color)
        if key not in self._glyphs:
            if (font, size) not in self._fonts:
                self._fonts[font, size] = pygame.font.Font(str(FONT_DIR / FONTS[font]), round(size * self.s))
            # The pixel font is drawn without anti-aliasing: its edges must stay hard.
            self._glyphs[key] = self._fonts[font, size].render(char, font != "pixel", color)
        return self._glyphs[key]

    def text(self, string, x, y, size, color=FG, font="sans", tracking=0.0, align="left"):
        """Draw one line with its baseline at y. tracking is extra letter spacing
        in ems. Drawn glyph by glyph from a cache, so a readout that changes
        every frame costs nothing and never shifts sideways. Returns the width."""
        glyphs = [self._glyph(font, size, char, color) for char in string]
        gap = tracking * size * self.s
        width = sum(g.get_width() for g in glyphs) + gap * max(0, len(glyphs) - 1)
        left = x * self.s - {"left": 0, "center": width / 2, "right": width}[align]
        top = y * self.s - self._fonts[font, size].get_ascent() if glyphs else 0
        for glyph in glyphs:
            self.surface.blit(glyph, (round(left), round(top)))
            left += glyph.get_width() + gap
        return width / self.s

    def label(self, string, x, y, color=DIM, size=11, align="left"):
        """Small, upper-case, widely tracked: the caption above a readout."""
        return self.text(string.upper(), x, y, size, color, "sans", 0.14, align)

    def number(self, string, x, y, size, color=FG, align="left", dim_zeros=False):
        """A numeric readout. dim_zeros draws leading zeros faintly: the readout
        keeps a fixed width (so nothing shifts as it grows) without shouting 007."""
        if not dim_zeros:
            return self.text(string, x, y, size, color, "mono", 0.0, align)
        body = string.lstrip("0")
        if not body or not body[0].isdigit():
            body = "0" + body  # keep the units digit: "00.4" dims one zero, not two
        width = self.text(string[:len(string) - len(body)], x, y, size, mix(BG, FG, 0.2), "mono")
        return width + self.text(body, x + width, y, size, color, "mono")

    # --- shapes ------------------------------------------------------------------

    def rect(self, x, y, w, h, color):
        pygame.draw.rect(self.surface, color, (round(x * self.s), round(y * self.s), max(1, round(w * self.s)), max(1, round(h * self.s))))

    def hline(self, x, y, w, color=HAIR):
        self.rect(x, y, w, 1, color)

    def vline(self, x, y, h, color=HAIR):
        self.rect(x, y, 1, h, color)

    def polygon(self, points, color, width=0):
        pygame.draw.polygon(self.surface, color, [(round(x * self.s), round(y * self.s)) for x, y in points], round(width * self.s))

    def circle(self, x, y, radius, color):
        pygame.draw.circle(self.surface, color, (round(x * self.s), round(y * self.s)), round(radius * self.s))

    def blit(self, surface, x, y):
        self.surface.blit(surface, (round(x * self.s), round(y * self.s)))
