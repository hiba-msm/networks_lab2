"""Sound, all of it generated here at start-up: no audio files. Each sound is
a list of 16-bit samples built with the standard library's array module and
handed to pygame.mixer, which mixes and plays on its own thread. Every call in
this file returns at once, so sound can never stall the game loop.

Hear every sound in turn:  python -m client.sound
"""

import logging
from array import array

import pygame

log = logging.getLogger("client")

RATE = 22050       # samples per second, mono; plenty for square waves
ENGINE_STEPS = 16  # one engine loop per whole press/second, 0..15
FADE_MS = 40       # cross-fade between two engine loops


def wave(notes, shape="square", volume=0.3, ramp_ms=5):
    """Samples for a run of notes, each (start Hz, end Hz, milliseconds). The
    pitch slides from start to end; 0 Hz is a rest. Every note fades in and out
    over ramp_ms, because a wave cut off mid-swing is heard as a click."""
    samples = array("h")
    phase = 0.0  # position inside one cycle, 0..1, carried across notes
    ramp = max(1, RATE * ramp_ms // 1000)
    for start_hz, end_hz, ms in notes:
        count = RATE * ms // 1000
        for i in range(count):
            hz = start_hz + (end_hz - start_hz) * i / count
            phase = (phase + hz / RATE) % 1.0
            if hz == 0:
                level = 0.0
            elif shape == "square":
                level = 1.0 if phase < 0.5 else -1.0
            else:  # sawtooth: harsher, used for the engine and the stall
                level = 2.0 * phase - 1.0
            envelope = min(1.0, i / ramp, (count - i) / ramp)
            samples.append(int(32767 * volume * level * envelope))
    return samples


def engine_loop(hz):
    """A short loop that repeats without a seam: a whole number of samples per
    cycle, a whole number of cycles, and no fade. A sawtooth plus a square one
    octave down gives a rough engine note."""
    period = round(RATE / hz) * 2  # samples in one cycle of the lower octave
    samples = array("h")
    for i in range(period * 4):
        saw = 2.0 * (i % (period // 2)) / (period // 2) - 1.0
        sub = 1.0 if i % period < period // 2 else -1.0
        samples.append(int(32767 * 0.11 * (saw + 0.6 * sub)))
    return samples


# The bass line: sixteen eighth-notes in A minor, looped for the whole race.
BASS_HZ = [55, 55, 110, 55, 65.4, 65.4, 130.8, 65.4, 73.4, 73.4, 146.8, 73.4, 82.4, 82.4, 98, 110]

RECIPES = {
    "press": dict(notes=[(1200, 1200, 14)], volume=0.14, ramp_ms=2),         # a click per counted key press
    "beep": dict(notes=[(440, 440, 140)]),                                   # a start light goes out
    "go": dict(notes=[(880, 880, 450)]),                                     # the last light: one octave up
    "stall": dict(notes=[(320, 70, 500)], shape="saw"),                      # descending buzz
    "overtake": dict(notes=[(660, 660, 50), (990, 990, 80)], volume=0.25),   # two rising notes
    "finish": dict(notes=[(523, 523, 90), (659, 659, 90), (784, 784, 90), (1047, 1047, 260)]),  # C E G C
    "bass": dict(notes=[(hz, hz, 214) for hz in BASS_HZ], volume=0.12),
}


class Sound:
    def __init__(self, muted=False):
        self.muted = muted
        self.ready = False        # False on a machine with no audio device: every call does nothing
        self.engine_step = None   # which engine loop is playing, None for silence
        self.music_on = False
        try:
            # allowedchanges=0: if the device wants another format, SDL converts
            # for us instead of silently handing back a different sample rate.
            pygame.mixer.quit()
            pygame.mixer.init(RATE, -16, 1, 256, allowedchanges=0)
        except pygame.error as exc:
            # No sound card, or its driver is off (lab PCs, remote desktops). The
            # game is fully playable without sound, so say so once and carry on.
            log.warning("no audio device, running silent: %s", exc)
            return
        self.sounds = {name: pygame.mixer.Sound(buffer=wave(**recipe)) for name, recipe in RECIPES.items()}
        self.engines = [pygame.mixer.Sound(buffer=engine_loop(45 + 9 * step)) for step in range(ENGINE_STEPS)]
        # Channels 0 and 1 take turns playing the engine (so one loop can fade
        # out while the next fades in), channel 2 is the bass line. Reserving
        # them stops a one-shot effect from being given one of the three.
        pygame.mixer.set_reserved(3)
        self.engine_channels = [pygame.mixer.Channel(0), pygame.mixer.Channel(1)]
        self.music_channel = pygame.mixer.Channel(2)
        self.ready = True

    def play(self, name):
        if self.ready and not self.muted:
            self.sounds[name].play()

    def engine(self, press_rate):
        """Call every frame. The engine's pitch follows the player's own press
        rate, so the ear confirms each key press. None switches the engine off."""
        step = None if press_rate is None else min(ENGINE_STEPS - 1, round(press_rate))
        if not self.ready or self.muted or step == self.engine_step:
            return
        self.engine_step = step
        self.engine_channels.reverse()  # the one that was playing is now second
        self.engine_channels[1].fadeout(FADE_MS)
        if step is not None:
            self.engine_channels[0].play(self.engines[step], loops=-1, fade_ms=FADE_MS)

    def music(self, on):
        """Call every frame: the bass line plays while on is true."""
        if not self.ready or self.muted or on == self.music_on:
            return
        self.music_on = on
        if on:
            self.music_channel.play(self.sounds["bass"], loops=-1)
        else:
            self.music_channel.fadeout(300)

    def toggle_mute(self):
        self.muted = not self.muted
        if self.ready and self.muted:
            pygame.mixer.stop()
            # Forget what was playing, so engine() and music() start it again on unmute.
            self.engine_step, self.music_on = None, False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sound = Sound()
    assert sound.ready, "no audio device"
    for name in RECIPES:
        print(name)
        sound.play(name)
        pygame.time.wait(900)
    for rate in range(ENGINE_STEPS):  # the engine, revving up
        print("engine at", rate, "presses/s")
        sound.engine(rate)
        pygame.time.wait(200)
