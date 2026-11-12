"""The look: palette, type and the drawing surface.

Everything is drawn on a canvas twice the window size and scaled down once per
frame, which anti-aliases every edge. Colours are flat: the "transparent" tones
are pre-mixed with the background, since nothing ever sits behind them but it.
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

FONT_DIR = pathlib.Path(__file__).parent.parent / "assets" / "fonts"
FONTS = {
    "sans": "SpaceGrotesk-Medium.ttf",    # labels
    "mono": "JetBrainsMono-Regular.ttf",  # every numeral: all digits are the same width
}

EASE = 0.2  # every animated value moves this share of the way to its target per frame


class Canvas:
    def __init__(self, window):
        self.window = window
        self.surface = pygame.Surface((W * S, H * S)).convert()
        self._fonts = {}
        self._glyphs = {}
        self._eased = {}

    def clear(self):
        self.surface.fill(BG)

    def present(self):
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

    # --- text --------------------------------------------------------------------

    def _glyph(self, font, size, char, color):
        key = (font, size, char, color)
        if key not in self._glyphs:
            if (font, size) not in self._fonts:
                self._fonts[font, size] = pygame.font.Font(str(FONT_DIR / FONTS[font]), round(size * S))
            self._glyphs[key] = self._fonts[font, size].render(char, True, color)
        return self._glyphs[key]

    def text(self, string, x, y, size, color=FG, font="sans", tracking=0.0, align="left"):
        """Draw one line with its baseline at y. tracking is extra letter spacing
        in ems. Drawn glyph by glyph from a cache, so a readout that changes
        every frame costs nothing and never shifts sideways. Returns the width."""
        glyphs = [self._glyph(font, size, char, color) for char in string]
        gap = tracking * size * S
        width = sum(g.get_width() for g in glyphs) + gap * max(0, len(glyphs) - 1)
        left = x * S - {"left": 0, "center": width / 2, "right": width}[align]
        top = y * S - self._fonts[font, size].get_ascent() if glyphs else 0
        for glyph in glyphs:
            self.surface.blit(glyph, (round(left), round(top)))
            left += glyph.get_width() + gap
        return width / S

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
        pygame.draw.rect(self.surface, color, (round(x * S), round(y * S), max(1, round(w * S)), max(1, round(h * S))))

    def hline(self, x, y, w, color=HAIR):
        self.rect(x, y, w, 1, color)

    def vline(self, x, y, h, color=HAIR):
        self.rect(x, y, 1, h, color)

    def polygon(self, points, color, width=0):
        pygame.draw.polygon(self.surface, color, [(round(x * S), round(y * S)) for x, y in points], round(width * S))

    def circle(self, x, y, radius, color):
        pygame.draw.circle(self.surface, color, (round(x * S), round(y * S)), round(radius * S))

    def blit(self, surface, x, y):
        self.surface.blit(surface, (round(x * S), round(y * S)))
