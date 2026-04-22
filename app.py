import math
import sys
import numpy as np
import pygame
from pygame.locals import (
    DOUBLEBUF, OPENGL, FULLSCREEN, QUIT, KEYDOWN, K_ESCAPE, K_UP, K_DOWN,
)
import random
import os
import threading
import time as _time_mod

def _print_help_banner(title, lines):
    """Print a friendly boxed help message to stderr so missing-dependency
    advice stands out instead of blending into the Python startup noise."""
    width = max(len(title), max((len(l) for l in lines), default=0)) + 4
    bar = "+" + "-" * (width - 2) + "+"
    print(bar, file=sys.stderr)
    print(f"| {title.ljust(width - 4)} |", file=sys.stderr)
    print(bar, file=sys.stderr)
    for line in lines:
        print(f"| {line.ljust(width - 4)} |", file=sys.stderr)
    print(bar, file=sys.stderr)


try:
    import sounddevice as _sd
    _AUDIO_AVAILABLE = True
except Exception:
    _AUDIO_AVAILABLE = False
    _print_help_banner(
        "Ambient audio disabled (sounddevice missing)",
        [
            "The procedural wind / rain / thunder / brown-noise layer",
            "needs the 'sounddevice' Python package.",
            "",
            "Install with:    pip install sounddevice",
            "",
            "The simulation will run without audio.",
        ],
    )

# pyfluidsynth on Windows calls `os.add_dll_directory(r'C:\tools\fluidsynth\bin')`
# at module import time — that's the Chocolatey default install path for
# fluidsynth. If you didn't install via Chocolatey the directory doesn't
# exist and the import raises FileNotFoundError (WinError 3). Pre-create
# the directory (empty is fine) so the import always succeeds; pyfluidsynth
# then falls back to the system DLL loader for the actual library. If
# fluidsynth itself isn't installed, Synth() later raises and the piano
# is silently disabled, but the rest of the app still runs.
if sys.platform == "win32":
    try:
        os.makedirs(r"C:\tools\fluidsynth\bin", exist_ok=True)
    except Exception:
        pass

try:
    import fluidsynth as _fluidsynth
    _FLUIDSYNTH_AVAILABLE = True
except Exception as _exc:
    _FLUIDSYNTH_AVAILABLE = False
    # Split the advice based on what actually failed: the Python binding
    # vs. the underlying C library. Both render as the same exception
    # type from pyfluidsynth's point of view, so we key off the message.
    _msg = str(_exc).lower()
    if "fluidsynth" in _msg and ("library" in _msg or "dll" in _msg
                                  or "cannot load" in _msg
                                  or "no such file" in _msg):
        _lines = [
            "The 'fluidsynth' system library is not installed.",
            "Install it for your OS (then re-run this program):",
            "",
            "  macOS:          brew install fluidsynth",
            "  Debian/Ubuntu:  sudo apt install fluidsynth",
            "  Fedora:         sudo dnf install fluidsynth",
            "  Arch:           sudo pacman -S fluidsynth",
            "  Windows:        choco install fluidsynth",
            "                  (or download from fluidsynth.org and put",
            "                   its .dll files on PATH)",
            "",
            "The simulation will run without the piano ensemble.",
        ]
    else:
        _lines = [
            "The 'pyfluidsynth' Python package is not installed.",
            "",
            "Install with:    pip install pyfluidsynth",
            "",
            "You also need the fluidsynth system library:",
            "  macOS:          brew install fluidsynth",
            "  Debian/Ubuntu:  sudo apt install fluidsynth",
            "  Windows:        choco install fluidsynth",
            "",
            "The simulation will run without the piano ensemble.",
        ]
    _print_help_banner("Procedural music disabled (FluidSynth not ready)",
                       _lines)

from OpenGL.GL import *
from OpenGL.GLU import (
    gluPerspective, gluLookAt, gluNewQuadric, gluQuadricTexture,
    gluQuadricNormals, gluCylinder, gluProject, GLU_SMOOTH,
)


# --- Road ---
ROAD_WIDTH = 10.0
SEG_LEN = 2.2
N_SEG = 400            # visible road/terrain extent: ~880m ahead of camera
SPEED = 28.0           # default cruise speed (m/s); Up/Down adjust live
SPEED_ACCEL = 24.0     # m/s² applied while Up/Down is held
SPEED_MIN = 0.0
SPEED_MAX = 90.0
CAM_HEIGHT = 3.4
CAM_BACK = 5.0
LOOK_AHEAD = 20.0

# --- Lamps ---
LAMP_SPACING = 10.0
N_LAMPS = 40

# --- Terrain ---
K_BANDS = 14
D_STEP = 6.0
TERRAIN_EDGE_D = 0.0   # terrain begins flush with road edge — no seam

# --- Biomes ---
ZONE_LEN = 280.0
TRANS_LEN = 95.0       # long smoothstep between zones so biomes shift gently
(BIOME_PLAIN, BIOME_HILL, BIOME_MOUNTAIN, BIOME_RIVER,
 BIOME_FOREST, BIOME_FROST, BIOME_CITY) = 0, 1, 2, 3, 4, 5, 6
BIOME_COUNT = 7

BIOME_COLOR = np.array([
    [0.46, 0.70, 0.26],   # plain grass
    [0.32, 0.56, 0.20],   # hill grass
    [0.62, 0.54, 0.46],   # mountain rock
    [0.26, 0.48, 0.68],   # river water
    [0.20, 0.36, 0.14],   # forest floor (dark undergrowth)
    [0.88, 0.92, 1.00],   # frost (snow field, cold blue-white)
    [0.30, 0.30, 0.33],   # city (concrete/asphalt near-ground)
], dtype=np.float32)

# --- Forest / trees ---
TREE_SPACING = 3.2           # stride along road for potential tree slots
TREE_MAX_PERP = 46.0         # max perpendicular distance from road edge
N_TREE_VARIANTS = 6          # number of baked tree templates

# --- Snow road shoulders ---
SNOW_SHOULDER_W = 2.2

# --- Day/Night cycle ---
# One minute == midnight → noon, so full cycle = 120s
DAY_PERIOD = 120.0
SKY_DOME_R = 1000.0    # large enough to enclose the deeper visible terrain


# --- Path curves ---
def curve_x(s):
    return (18.0 * math.sin(s * 0.0055)
            + 9.0 * math.sin(s * 0.013 + 1.3)
            + 3.0 * math.sin(s * 0.031 + 0.7))


def curve_y(s):
    return (6.0 * math.sin(s * 0.006 + 0.5)
            + 2.5 * math.sin(s * 0.017 + 2.1)
            + 0.8 * math.sin(s * 0.043))


def curve_x_np(s):
    return (18.0 * np.sin(s * 0.0055)
            + 9.0 * np.sin(s * 0.013 + 1.3)
            + 3.0 * np.sin(s * 0.031 + 0.7))


def curve_y_np(s):
    return (6.0 * np.sin(s * 0.006 + 0.5)
            + 2.5 * np.sin(s * 0.017 + 2.1)
            + 0.8 * np.sin(s * 0.043))


# --- Biomes ---
def biome_at(zone_idx, side):
    """Per-zone biome, with one coherence rule: frost zones are always
    symmetric across the road. If either side would roll frost, both do,
    so snow-covered ground never butts up against green grass on the
    opposite side (which looks wrong — weather is atmospheric). Other
    biomes still vary per side so you can get mountain on the left and
    plain on the right for scenic variety.
    """
    key_l = (zone_idx * 2654435761) & 0xFFFFFFFF
    key_r = (zone_idx * 2654435761 + 9277) & 0xFFFFFFFF
    bl = key_l % BIOME_COUNT
    br = key_r % BIOME_COUNT
    if bl == BIOME_FROST or br == BIOME_FROST:
        return BIOME_FROST
    return bl if side < 0 else br


def biome_weights_vec(s_arr, side):
    NS = len(s_arr)
    out = np.zeros((NS, BIOME_COUNT), dtype=np.float32)
    for i, s in enumerate(s_arr):
        idx = int(s // ZONE_LEN)
        pos = s - idx * ZONE_LEN
        cur = biome_at(idx, side)
        if pos < TRANS_LEN:
            prev = biome_at(idx - 1, side)
            t = pos / TRANS_LEN
            t = t * t * (3 - 2 * t)
            out[i, prev] += 1.0 - t
            out[i, cur] += t
        elif pos > ZONE_LEN - TRANS_LEN:
            nxt = biome_at(idx + 1, side)
            t = (ZONE_LEN - pos) / TRANS_LEN
            t = t * t * (3 - 2 * t)
            out[i, cur] += t
            out[i, nxt] += 1.0 - t
        else:
            out[i, cur] = 1.0
    return out


def is_plain(s, side):
    return biome_weights_vec(np.array([s], dtype=np.float32), side)[0, BIOME_PLAIN] > 0.65


def forest_weight_at(s, side):
    return biome_weights_vec(np.array([s], dtype=np.float32), side)[0, BIOME_FOREST]


def frost_weight_at(s, side):
    return biome_weights_vec(np.array([s], dtype=np.float32), side)[0, BIOME_FROST]


def frost_intensity_at(s):
    """Max frost weight across sides — drives snowfall visibility."""
    w = biome_weights_vec(np.array([s], dtype=np.float32), -1)[0, BIOME_FROST]
    w2 = biome_weights_vec(np.array([s], dtype=np.float32), +1)[0, BIOME_FROST]
    return float(max(w, w2))


# --- Terrain heights ---
def terrain_heights(s, d, t_time):
    plain = -0.25 + 0.18 * np.sin(s * 0.25 + d * 0.18)
    hill = (-0.30
            + 2.2 * np.sin(s * 0.035) * (d / 40.0)
            + 1.4 * np.sin(s * 0.09 + d * 0.13)
            + 0.6 * np.sin(s * 0.22 + d * 0.5))
    rise = np.clip(d / 45.0, 0.0, 1.0) ** 1.6
    mountain = (-0.25
                + 32.0 * rise * (0.55 + 0.40 * np.sin(s * 0.022))
                + 4.0 * rise * np.abs(np.sin(s * 0.057 + d * 0.05))
                + 3.0 * rise * np.sin(s * 0.14 + d * 0.24))
    bank = 1.2
    far_bank = 30.0
    water = -3.0 + 0.08 * np.sin(s * 0.6 + d * 0.4 + t_time * 2.0)
    river = np.where(
        d < bank,
        -0.25 - (d / bank) * 2.6,
        np.where(d < far_bank, water,
                 -3.0 + np.clip((d - far_bank) / 10.0, 0.0, 1.0) * 3.3),
    )
    # forest: near-flat forest floor with subtle rise for undergrowth humps
    forest = (-0.28 + 0.25 * np.sin(s * 0.11 + d * 0.18)
              + 0.15 * np.sin(s * 0.04))
    # frost: snowy drifts — slightly higher near road (plow banks), gentle dunes
    frost = (-0.20
             + 0.35 * np.exp(-d / 6.0)              # snow pile near road edge
             + 0.45 * np.sin(s * 0.07 + d * 0.09)   # rolling drifts
             + 0.20 * np.sin(s * 0.19))
    # city: flat paved ground with tiny variation (sidewalks + pavement)
    city = -0.22 + 0.05 * np.sin(s * 0.35 + d * 0.25)
    return plain, hill, mountain, river, forest, frost, city


# --- Day/Night model ---
# t_day in [0, 1): 0=midnight, 0.25=sunrise, 0.5=noon, 0.75=sunset
def sun_dir_at(t_day):
    angle = 2 * math.pi * (t_day - 0.25)  # 0 rad at sunrise
    # Base arc in the X-Y plane; slight tilt on Z so sun traces a real arc
    x = math.cos(angle)
    y = math.sin(angle)
    z = -0.18 * math.cos(angle)
    v = np.array([x, y, z], dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


# Keyframes: (t_day, zenith_rgb, horizon_rgb)
SKY_KEYS = [
    (0.00, (0.015, 0.018, 0.055), (0.035, 0.045, 0.10)),
    (0.20, (0.10, 0.09, 0.22),   (0.50, 0.30, 0.38)),
    (0.26, (0.32, 0.42, 0.64),   (1.00, 0.64, 0.32)),
    (0.38, (0.28, 0.52, 0.86),   (0.82, 0.88, 0.96)),
    (0.50, (0.22, 0.48, 0.85),   (0.78, 0.88, 0.96)),
    (0.62, (0.28, 0.52, 0.86),   (0.85, 0.84, 0.92)),
    (0.74, (0.34, 0.30, 0.52),   (1.00, 0.55, 0.24)),
    (0.80, (0.12, 0.10, 0.22),   (0.40, 0.20, 0.28)),
    (1.00, (0.015, 0.018, 0.055),(0.035, 0.045, 0.10)),
]


def _lerp3(a, b, t):
    return (a[0] * (1 - t) + b[0] * t,
            a[1] * (1 - t) + b[1] * t,
            a[2] * (1 - t) + b[2] * t)


def sky_colors_at(t_day, storm=0.0, flash=0.0):
    t = t_day % 1.0
    zen = horz = None
    for i in range(len(SKY_KEYS) - 1):
        t0, z0, h0 = SKY_KEYS[i]
        t1, z1, h1 = SKY_KEYS[i + 1]
        if t0 <= t <= t1:
            a = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            zen = _lerp3(z0, z1, a)
            horz = _lerp3(h0, h1, a)
            break
    if zen is None:
        zen, horz = SKY_KEYS[0][1], SKY_KEYS[0][2]
    # storm darkens the whole dome toward neutral gray
    if storm > 0.001:
        sz = (0.12, 0.13, 0.17)
        sh = (0.22, 0.22, 0.26)
        zen = _lerp3(zen, sz, storm * 0.75)
        horz = _lerp3(horz, sh, storm * 0.75)
    # lightning flash briefly whites out the sky
    if flash > 0.001:
        zen = (min(1.0, zen[0] + flash * 0.45),
               min(1.0, zen[1] + flash * 0.45),
               min(1.0, zen[2] + flash * 0.50))
        horz = (min(1.0, horz[0] + flash * 0.55),
                min(1.0, horz[1] + flash * 0.55),
                min(1.0, horz[2] + flash * 0.60))
    return zen, horz


def ambient_at(t_day, storm=0.0, flash=0.0):
    """Returns (brightness scalar, RGB tint) affecting terrain/road."""
    el = sun_dir_at(t_day)[1]
    day = _smooth((el + 0.15) / 0.50)
    bright = 0.22 + 0.78 * day
    warm = _smooth(1.0 - abs(el) / 0.25) * (1.0 if el > -0.25 else 0.0)
    cold = _smooth((-el - 0.10) / 0.60)
    r = 1.0 + 0.20 * warm - 0.25 * cold
    g = 1.0 + 0.05 * warm - 0.18 * cold
    b = 1.0 - 0.15 * warm + 0.15 * cold
    # storm dims the scene
    bright *= (1.0 - 0.55 * storm)
    # lightning flash briefly overpowers everything
    bright = min(1.6, bright + flash * 0.85)
    return bright, (r, g, b)


def night_factor_at(t_day):
    """1 at deep night, 0 during day — gates lamps and stars."""
    el = sun_dir_at(t_day)[1]
    return _smooth((-el + 0.05) / 0.35)


def cloud_tint_at(t_day, storm=0.0):
    el = sun_dir_at(t_day)[1]
    day = _smooth((el + 0.15) / 0.50)
    warm = _smooth(1.0 - abs(el) / 0.25) * (1.0 if el > -0.25 else 0.0)
    base_r = 0.35 + 0.65 * day
    base_g = 0.38 + 0.60 * day
    base_b = 0.45 + 0.55 * day
    r = base_r + 0.30 * warm
    g = base_g + 0.10 * warm
    b = base_b - 0.05 * warm
    if storm > 0.001:
        dark = (0.22, 0.22, 0.26)
        r = r * (1 - storm) + dark[0] * storm
        g = g * (1 - storm) + dark[1] * storm
        b = b * (1 - storm) + dark[2] * storm
    return (min(1.0, r), min(1.0, g), min(1.0, b))


# --- Storm / rain / lightning ---
# Storm cycles on a double-sine so they don't feel metronomic. Peaks push
# intensity into [0, 1] via a soft threshold. Ambient, sky, and fog all
# consume the intensity to darken the scene and thicken the atmosphere.

STORM_PERIOD = 90.0  # seconds per primary cycle

# Rain particle system
RAIN_N = 1400
RAIN_BOX_X = 32.0
RAIN_BOX_Z = 55.0
RAIN_Y_TOP = 22.0
RAIN_Y_BOTTOM = -2.0
RAIN_STREAK_DT = 0.08  # streak length in seconds of motion

# Lightning
BOLT_LIFE = 0.22  # seconds each bolt is visible


def storm_intensity_at(t_time):
    """0 in clear weather, 1 in the middle of a storm. Three detuned sines
    multiplied together produce rare tall peaks — storms only fire when
    all three happen to align, so most of the time the sky stays clear.

    Tuning: period ~4 minutes, threshold 0.55 on the normalised product,
    roughly 8-12% of wall-clock time sits in a visible storm, ~3% at peak.
    """
    # Longer primary period (~7 min) so storms build and clear slowly.
    a = 0.5 + 0.5 * math.sin(t_time * 2 * math.pi / 420.0)
    b = 0.5 + 0.5 * math.sin(t_time * 2 * math.pi / 115.0 + 1.3)
    c = 0.5 + 0.5 * math.sin(t_time * 2 * math.pi / 210.0 + 0.7)
    raw = a * b * c
    x = max(0.0, (raw - 0.36) / 0.64)
    x = min(1.0, x)
    return x * x * (3.0 - 2.0 * x)


def init_rain(seed=91):
    rng = np.random.default_rng(seed)
    pos = np.zeros((RAIN_N, 3), dtype=np.float32)
    pos[:, 0] = rng.uniform(-RAIN_BOX_X, RAIN_BOX_X, RAIN_N)
    pos[:, 1] = rng.uniform(RAIN_Y_BOTTOM, RAIN_Y_TOP, RAIN_N)
    pos[:, 2] = rng.uniform(-RAIN_BOX_Z, RAIN_BOX_Z * 0.25, RAIN_N)
    vel = np.zeros((RAIN_N, 3), dtype=np.float32)
    vel[:, 0] = 2.2 + rng.uniform(-0.8, 0.8, RAIN_N)   # wind drift on X
    vel[:, 1] = rng.uniform(-25.0, -17.0, RAIN_N)       # hard fall
    vel[:, 2] = rng.uniform(-0.6, 0.6, RAIN_N)
    return pos, vel


def update_rain(state, dt):
    pos, vel = state
    pos += vel * dt
    below = pos[:, 1] < RAIN_Y_BOTTOM
    outX = np.abs(pos[:, 0]) > RAIN_BOX_X
    outZ = np.abs(pos[:, 2]) > RAIN_BOX_Z
    respawn = below | outX | outZ
    n = int(respawn.sum())
    if n > 0:
        rng = np.random.default_rng()
        pos[respawn, 0] = rng.uniform(-RAIN_BOX_X, RAIN_BOX_X, n).astype(np.float32)
        pos[respawn, 1] = RAIN_Y_TOP
        pos[respawn, 2] = rng.uniform(-RAIN_BOX_Z, RAIN_BOX_Z * 0.3, n).astype(np.float32)


def draw_rain(state, intensity, cam_x, cam_y, cam_z):
    """Render rain as short GL_LINES streaks. Each drop draws a line from
    its current position to pos - velocity * streak_dt — direction of the
    streak matches the motion, length approximates motion blur."""
    if intensity < 0.04:
        return
    pos, vel = state
    # streak ends: tail trails behind the head by streak_dt of motion
    tails = pos - vel * RAIN_STREAK_DT
    lines = np.empty((RAIN_N * 2, 3), dtype=np.float32)
    lines[0::2] = pos
    lines[1::2] = tails

    glDisable(GL_TEXTURE_2D)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDepthMask(GL_FALSE)
    glLineWidth(1.3)
    glPushMatrix()
    glTranslatef(cam_x, cam_y, cam_z)
    # cool-blue rain with alpha tied to storm intensity
    glColor4f(0.60, 0.72, 0.90, min(1.0, intensity * 0.75))
    glEnableClientState(GL_VERTEX_ARRAY)
    glVertexPointer(3, GL_FLOAT, 0, lines)
    glDrawArrays(GL_LINES, 0, RAIN_N * 2)
    glDisableClientState(GL_VERTEX_ARRAY)
    glPopMatrix()
    glLineWidth(1.0)
    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)


# --- Brown noise ambient (Brownian / 1/f² spectrum, evokes road rush) ---
# Generated by synthesising a white noise spectrum, multiplying magnitudes
# by 1/f so the power spectrum is 1/f² (by definition brown noise), then
# inverse-FFTing back to the time domain. We loop a single long buffer
# and resample it on the fly in a sounddevice callback: each output sample
# reads the buffer at a floating-point index advanced by `speed_factor`
# per output frame, so playing back faster shifts the rumble higher in
# pitch, matching the car's wind-rush character when accelerating.

_AUDIO_SAMPLE_RATE = 44100
_AUDIO_BLOCK_SIZE = 1024
_AUDIO_VOLUME = 0.09              # quiet ambient (user asked "not aloud")
_AUDIO_BUFFER_SEC = 30.0


def generate_brown_noise_buffer(duration_s=_AUDIO_BUFFER_SEC,
                                sample_rate=_AUDIO_SAMPLE_RATE,
                                seed=42):
    """Brown noise via FFT-shaped Gaussian spectrum. Power spectrum is
    1/f² (brown = 2nd-order 1/f^α family). Much faster than iterative
    integration and avoids any DC drift because the DC bin is zeroed.
    """
    n = int(duration_s * sample_rate)
    rng = np.random.default_rng(seed)
    # Gaussian-distributed frequency-domain samples (real+imag)
    k = n // 2 + 1
    re = rng.standard_normal(k).astype(np.float32)
    im = rng.standard_normal(k).astype(np.float32)
    spectrum = re + 1j * im
    freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
    scale = np.zeros_like(freqs)
    # 1/f magnitude → 1/f² power. Skip DC (f=0) to avoid blow-up.
    scale[1:] = 1.0 / freqs[1:]
    brown = np.fft.irfft(spectrum * scale, n).astype(np.float32)
    # Normalise peak, then a gentle tanh to even out rare spikes
    brown /= (np.max(np.abs(brown)) + 1e-9)
    brown = np.tanh(brown * 1.15).astype(np.float32)
    return brown


def generate_rain_noise_buffer(duration_s=15.0,
                               sample_rate=_AUDIO_SAMPLE_RATE, seed=101):
    """Rain hiss: white noise FFT-shaped to emphasise the 2-5 kHz band,
    with slow amplitude modulation so density varies over time."""
    n = int(duration_s * sample_rate)
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft(rng.standard_normal(n).astype(np.float32))
    freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
    curve = np.exp(-((freqs - 4000.0) / 2500.0) ** 2)
    curve += 0.35 * np.exp(-((freqs - 1500.0) / 700.0) ** 2)
    rain = np.fft.irfft(spectrum * curve.astype(np.complex64), n).astype(np.float32)
    rain /= (np.max(np.abs(rain)) + 1e-9)
    # density variation — slow block-level modulation
    block = 512
    mod = 1.0 + 0.22 * rng.standard_normal(n // block + 1).astype(np.float32)
    mod = np.clip(np.repeat(mod, block)[:n], 0.55, 1.55)
    rain *= mod
    return np.tanh(rain * 1.1).astype(np.float32)


def generate_wind_noise_buffer(duration_s=25.0,
                               sample_rate=_AUDIO_SAMPLE_RATE, seed=103):
    """Wind: pink-tilted low-pass noise modulated by several slow LFOs
    for gusts. Strong on the low-mids, nothing above ~900 Hz."""
    n = int(duration_s * sample_rate)
    rng = np.random.default_rng(seed)
    re = rng.standard_normal(n // 2 + 1).astype(np.float32)
    im = rng.standard_normal(n // 2 + 1).astype(np.float32)
    spectrum = (re + 1j * im).astype(np.complex64)
    freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
    scale = np.zeros_like(freqs, dtype=np.float32)
    scale[1:] = 1.0 / (freqs[1:] ** 0.6)
    scale *= np.exp(-(freqs / 900.0) ** 1.8)
    wind = np.fft.irfft(spectrum * scale.astype(np.complex64), n).astype(np.float32)
    wind /= (np.max(np.abs(wind)) + 1e-9)
    # gust envelope — three detuned LFOs so gusts don't repeat exactly
    t = np.arange(n, dtype=np.float32) / sample_rate
    gust = (1.0
            + 0.40 * np.sin(t * 2 * math.pi / 5.3)
            + 0.30 * np.sin(t * 2 * math.pi / 7.8 + 1.2)
            + 0.20 * np.sin(t * 2 * math.pi / 3.1 + 0.5))
    wind *= np.clip(gust, 0.15, 2.1)
    wind /= (np.max(np.abs(wind)) + 1e-9)
    return np.tanh(wind * 1.1).astype(np.float32)


def generate_thunder_clip(duration_s=3.8,
                          sample_rate=_AUDIO_SAMPLE_RATE, seed=107):
    """Thunder one-shot: heavy sub-bass rumble with a sharp attack, a
    long exponential decay, and two secondary rolls so it sounds like a
    distant boom with after-rumbles rather than a single pop."""
    n = int(duration_s * sample_rate)
    rng = np.random.default_rng(seed)
    re = rng.standard_normal(n // 2 + 1).astype(np.float32)
    im = rng.standard_normal(n // 2 + 1).astype(np.float32)
    spectrum = (re + 1j * im).astype(np.complex64)
    freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
    scale = np.zeros_like(freqs, dtype=np.float32)
    scale[1:] = np.exp(-(freqs[1:] / 130.0) ** 1.3)
    rumble = np.fft.irfft(spectrum * scale.astype(np.complex64), n).astype(np.float32)
    t = np.arange(n, dtype=np.float32) / sample_rate
    env_main = (1.0 - np.exp(-t * 35.0)) * np.exp(-t * 1.2)
    env_sec1 = 0.45 * np.exp(-np.maximum(t - 0.7, 0.0) * 0.9) * (t > 0.3)
    env_sec2 = 0.28 * np.exp(-np.maximum(t - 1.6, 0.0) * 0.7) * (t > 1.2)
    env = np.clip(env_main + env_sec1 + env_sec2, 0.0, None).astype(np.float32)
    rumble *= env
    rumble /= (np.max(np.abs(rumble)) + 1e-9)
    return np.tanh(rumble * 0.9).astype(np.float32)


class AmbientAudioMixer:
    """Single sounddevice output stream mixing four layers:

        * brown-noise engine rumble at variable playback speed
        * rain hiss (looped), volume tracks storm × (1 - frost)
        * wind gusts (looped), volume tracks camera speed + open-terrain
          exposure + storm
        * thunder one-shots, triggered by lightning strikes

    All target volumes and the brown-noise speed are set from the main
    thread (atomic float writes) and low-passed inside the callback so
    parameter changes don't produce clicks. Thunder events are queued in
    a lock-protected list."""

    def __init__(self, brown_duration_s=_AUDIO_BUFFER_SEC):
        self.brown = generate_brown_noise_buffer(brown_duration_s)
        self.rain = generate_rain_noise_buffer()
        self.wind = generate_wind_noise_buffer()
        self.thunder = generate_thunder_clip()

        # target volumes written from main thread
        self.brown_vol_target = 0.085
        self.rain_vol_target = 0.0
        self.wind_vol_target = 0.04
        # smoothed inside callback
        self.brown_vol = self.brown_vol_target
        self.rain_vol = self.rain_vol_target
        self.wind_vol = self.wind_vol_target

        # brown noise playback speed
        self.speed_target = 1.0
        self.speed_current = 1.0

        # loop read phases
        self.brown_phase = 0.0
        self.rain_phase = 0
        self.wind_phase = 0

        # thunder events: each entry = [clip_sample_index, volume]
        self.thunder_lock = threading.Lock()
        self.thunder_events = []

        self.stream = None

    def start(self):
        self.stream = _sd.OutputStream(
            channels=1,
            samplerate=_AUDIO_SAMPLE_RATE,
            blocksize=_AUDIO_BLOCK_SIZE,
            callback=self._callback,
            dtype='float32',
        )
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def set_speed(self, speed_factor):
        self.speed_target = float(max(0.2, min(1.8, speed_factor)))

    def set_volumes(self, rain=None, wind=None, brown=None):
        if rain is not None:
            self.rain_vol_target = float(max(0.0, min(0.55, rain)))
        if wind is not None:
            self.wind_vol_target = float(max(0.0, min(0.40, wind)))
        if brown is not None:
            self.brown_vol_target = float(max(0.0, min(0.30, brown)))

    def trigger_thunder(self, volume=0.55):
        with self.thunder_lock:
            # cap at 3 concurrent thunder events so repeated lightning
            # doesn't snowball volume
            if len(self.thunder_events) < 3:
                self.thunder_events.append([0, float(volume)])

    @staticmethod
    def _read_loop(buf, phase, frames):
        n = len(buf)
        if phase + frames <= n:
            return buf[phase:phase + frames]
        head = buf[phase:]
        rem = frames - len(head)
        return np.concatenate([head, buf[:rem]])

    def _callback(self, outdata, frames, time_info, status):
        # Two smoothing rates:
        #   fast (α ≈ 0.035, τ ≈ 0.7 s) for brown-noise speed + volume
        #     so the engine rumble responds snappily to Up/Down presses
        #   slow (α ≈ 0.010, τ ≈ 2.5 s) for rain and wind so weather
        #     fades in and out gradually, matching real atmospheric
        #     transitions rather than snapping on/off
        a_fast = 0.035
        a_slow = 0.010
        self.speed_current += (self.speed_target - self.speed_current) * a_fast
        self.brown_vol += (self.brown_vol_target - self.brown_vol) * a_fast
        self.rain_vol += (self.rain_vol_target - self.rain_vol) * a_slow
        self.wind_vol += (self.wind_vol_target - self.wind_vol) * a_slow

        # brown noise with variable speed + linear interpolation
        sp = self.speed_current
        bn = len(self.brown)
        idxs = (self.brown_phase
                + np.arange(frames, dtype=np.float64) * sp) % bn
        i0 = idxs.astype(np.int64)
        i1 = (i0 + 1) % bn
        frac = (idxs - i0).astype(np.float32)
        brown_s = self.brown[i0] * (1.0 - frac) + self.brown[i1] * frac
        self.brown_phase = float((self.brown_phase + frames * sp) % bn)

        rain_s = self._read_loop(self.rain, self.rain_phase, frames)
        self.rain_phase = (self.rain_phase + frames) % len(self.rain)
        wind_s = self._read_loop(self.wind, self.wind_phase, frames)
        self.wind_phase = (self.wind_phase + frames) % len(self.wind)

        thunder_s = np.zeros(frames, dtype=np.float32)
        with self.thunder_lock:
            still_running = []
            for ev in self.thunder_events:
                start_idx, vol = ev
                clip_len = len(self.thunder)
                if start_idx < clip_len:
                    end_idx = min(start_idx + frames, clip_len)
                    chunk = self.thunder[start_idx:end_idx]
                    thunder_s[:len(chunk)] += chunk * vol
                    ev[0] = start_idx + frames
                    if ev[0] < clip_len:
                        still_running.append(ev)
            self.thunder_events = still_running

        outdata[:, 0] = (brown_s * self.brown_vol
                         + rain_s * self.rain_vol
                         + wind_s * self.wind_vol
                         + thunder_s)


# Back-compat alias so older code paths referencing BrownNoisePlayer
# keep working (the mixer subsumes its functionality).
BrownNoisePlayer = AmbientAudioMixer


# --- Minimalist procedural piano ---------------------------------------
# Generative strategy drawn from the minimalism research lineage:
#   * Scale-constrained random walk (Markov-style transitions biased by
#     pitch proximity — neighbouring notes are far more likely than
#     leaps). This is the simplest generative grammar that yields
#     melodically coherent lines (Kiesow & Fadiga / Jacob 2021 Markov
#     chains for computer music).
#   * Arvo Pärt's *tintinnabuli* overlay: a second voice that plays only
#     notes of the tonic triad at a lower octave, synchronised with the
#     melody. Creates the signature spare, sacred sound.
#   * Slow tempo (~45 BPM) with a generous rest probability (Erik Satie
#     / Brian Eno generative ambient approach).
#   * Dynamics (velocity) kept soft (40-70 of 127) so the piano sits as
#     an ambient layer under the brown-noise rush.
#
# Rendering goes through FluidSynth (C library) playing a CC0 Upright
# Piano KW SoundFont. The synth runs its own real-time audio thread via
# CoreAudio; the scheduler thread just issues noteon/noteoff events.

SOUNDFONT_PATH = "soundfonts/GeneralUser-GS.sf2"


class MinimalEnsemblePlayer:
    """Minimalist ensemble: Rhodes electric piano for the melody voice,
    strings ensemble for counterpoint and bass pedal.

    Upgrades over the previous piano-only version:

    * Two timbres from GeneralUser GS (SoundFont bank 0, programs 4 +
      48). Rhodes carries the sparse foreground; strings provide the
      sustained atmospheric pad.
    * String notes are always shaped by a **CC 11 expression envelope**
      (0 → target over ~1.5s attack, hold, target → 0 over ~2s release)
      so the strings *never* hard-clip on attack or release regardless
      of the SoundFont's own envelope. Rendered as a discretised ramp
      of MIDI CC events fired by threading.Timer.
    * Melody uses **motif development** (a short scale-degree gesture
      generated at startup and transposed / varied through the piece,
      Reich-style) in addition to Markov-style chord-conditioned walks.
    * Two-voice counterpoint still follows Fux first-species rules:
      consonant intervals with the melody, contrary-motion preference.
    * Bass pedal holds the chord root on strings for the full 8-beat
      chord with a very long attack and release so chord changes blend
      seamlessly rather than cross-fade with an audible edge.
    """

    # A-minor natural scale (A B C D E F G) across ~3 octaves. Voices
    # pick from this; voice 1 weights chord tones higher during each
    # chord so the melody follows the harmony.
    SCALE = [48, 50, 52, 53, 55, 57, 59, 60, 62, 64, 65, 67, 69, 71,
             72, 74, 76, 77, 79, 81, 83, 84]

    # Diatonic minor progression i - VI - III - VII = Am F C G, each
    # chord held for 8 beats so the full cycle is 32 beats (~48 s at
    # 40 BPM). Each entry: (name, chord-tone pool, bass-pedal pitch).
    CHORDS = [
        ("Am", [57, 60, 64, 69, 72, 76, 81], 45),   # A C E  / A2 pedal
        ("F",  [57, 60, 65, 69, 72, 77, 81], 41),   # F A C  / F2 pedal
        ("C",  [60, 64, 67, 72, 76, 79, 84], 48),   # C E G  / C3 pedal
        ("G",  [59, 62, 67, 71, 74, 79, 83], 43),   # G B D  / G2 pedal
    ]

    # Consonant intervals reduced modulo octave (Fux species-1):
    # unison/octave, m3, M3, P5, m6, M6. P4 is technically dissonant in
    # two-voice counterpoint but sounds fine modally, so we include it.
    CONSONANT_INTERVALS_MOD12 = {0, 3, 4, 5, 7, 8, 9}

    BPM = 40.0
    BEATS_PER_CHORD = 8

    # GeneralUser GS — programs + channels
    PROG_RHODES = 4           # Electric Piano 1 ("Tine" Rhodes)
    PROG_STRINGS = 48         # String Ensemble 1
    CH_MELODY = 0             # Rhodes
    CH_CP = 1                 # Strings — counterpoint
    CH_BASS = 2               # Strings — bass pedal

    # Rhodes echo delays (seconds) and relative velocity each echo plays at
    ECHOES = ((0.40, 0.55), (0.85, 0.28))

    # String expression envelope
    STR_EXPR_MAX = 108        # CC 11 target value at the top of the swell
    STR_RAMP_STEPS = 16       # discretisation of attack / release
    STR_ATTACK_DEFAULT = 1.5  # seconds — attack ramp for counterpoint
    STR_RELEASE_DEFAULT = 2.0 # seconds — release ramp for counterpoint
    STR_BASS_ATTACK = 2.5     # longer, very gentle attack for bass pad
    STR_BASS_RELEASE = 3.0

    def __init__(self, sf2_path=SOUNDFONT_PATH, gain=0.32):
        self.beat_sec = 60.0 / self.BPM
        self.fs = _fluidsynth.Synth(samplerate=44100, gain=gain)
        try:
            self.fs.start(driver="coreaudio")
        except Exception:
            self.fs.start()
        sfid = self.fs.sfload(sf2_path)
        if sfid == -1:
            raise RuntimeError(f"failed to load SoundFont at {sf2_path}")
        # Rhodes on the melody channel, strings on CP + bass
        self.fs.program_select(self.CH_MELODY, sfid, 0, self.PROG_RHODES)
        self.fs.program_select(self.CH_CP, sfid, 0, self.PROG_STRINGS)
        self.fs.program_select(self.CH_BASS, sfid, 0, self.PROG_STRINGS)

        # Long atmospheric reverb tail
        try:
            self.fs.set_reverb(roomsize=0.92, damping=0.35,
                               width=0.9, level=0.95)
        except Exception:
            pass

        self.rng = random.Random(2024)
        # Reich-style motif: 4-5 scale-degree steps that get transposed
        # and rotated through the piece for thematic continuity.
        self.motif = self._make_motif()
        self.motif_pos = 0
        self.motif_cycles = 0
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        try:
            for ch in (self.CH_MELODY, self.CH_CP, self.CH_BASS):
                self.fs.all_notes_off(ch)
                self._safe_cc(ch, 11, 0)
            self.fs.delete()
        except Exception:
            pass

    # ----- low-level MIDI helpers -----------------------------------
    def _safe_noteoff(self, channel, pitch):
        if self.stop_event.is_set():
            return
        try:
            self.fs.noteoff(channel, pitch)
        except Exception:
            pass

    def _safe_cc(self, channel, cc_num, value):
        if self.stop_event.is_set():
            return
        try:
            self.fs.cc(channel, cc_num, int(max(0, min(127, value))))
        except Exception:
            pass

    # ----- Rhodes melody (with echoes) ------------------------------
    def _fire_rhodes(self, pitch, velocity, hold_sec):
        if self.stop_event.is_set() or pitch < 21 or pitch > 108:
            return
        try:
            self.fs.noteon(self.CH_MELODY, pitch, velocity)
        except Exception:
            return
        threading.Timer(hold_sec, self._safe_noteoff,
                        args=(self.CH_MELODY, pitch)).start()
        for delay, vel_factor in self.ECHOES:
            echo_vel = max(8, int(velocity * vel_factor))
            threading.Timer(delay, self._echo_rhodes,
                            args=(pitch, echo_vel, hold_sec)).start()

    def _echo_rhodes(self, pitch, velocity, hold_sec):
        if self.stop_event.is_set():
            return
        try:
            self.fs.noteon(self.CH_MELODY, pitch, velocity)
        except Exception:
            return
        threading.Timer(hold_sec, self._safe_noteoff,
                        args=(self.CH_MELODY, pitch)).start()

    # ----- Strings with smooth envelope (no hard-clip attack) -------
    def _fire_strings(self, channel, pitch, velocity,
                      sustain_sec, attack_sec=None, release_sec=None):
        """Strings swell: CC 11 ramps 0→target over attack_sec, holds
        through sustain, then ramps back to 0 over release_sec. noteon
        is issued before the ramp (so the SF2 sample starts with its
        real attack transient, but CC 11 scales it to zero, masking the
        hard transient); noteoff is scheduled once the whole envelope
        has completed.
        """
        if self.stop_event.is_set() or pitch < 21 or pitch > 108:
            return
        if attack_sec is None:
            attack_sec = self.STR_ATTACK_DEFAULT
        if release_sec is None:
            release_sec = self.STR_RELEASE_DEFAULT
        target = self.STR_EXPR_MAX
        steps = self.STR_RAMP_STEPS

        # Start at silent expression, fire noteon, then ramp up
        self._safe_cc(channel, 11, 0)
        try:
            self.fs.noteon(channel, pitch, velocity)
        except Exception:
            return
        # Attack ramp
        for i in range(1, steps + 1):
            t = i / steps
            threading.Timer(
                attack_sec * t, self._safe_cc,
                args=(channel, 11, int(target * t)),
            ).start()
        # Release ramp — starts after attack + sustain
        rel_start = attack_sec + sustain_sec
        for i in range(1, steps + 1):
            t = i / steps
            threading.Timer(
                rel_start + release_sec * t, self._safe_cc,
                args=(channel, 11, int(target * (1.0 - t))),
            ).start()
        # noteoff after the entire envelope has finished
        threading.Timer(
            attack_sec + sustain_sec + release_sec + 0.15,
            self._safe_noteoff, args=(channel, pitch),
        ).start()

    # ----- motif system ---------------------------------------------
    def _make_motif(self):
        """A short scale-degree gesture (3-5 steps) generated at start
        and evolved throughout the piece — gives thematic continuity
        (Reich-style motif development)."""
        n = self.rng.choice([3, 4, 5])
        steps = []
        for _ in range(n):
            # Mostly stepwise, occasional leap; rarely repeat
            steps.append(self.rng.choice([-2, -1, -1, 1, 1, 2]))
        return steps

    def _evolve_motif(self):
        """Every few chord cycles the motif evolves: transpose, reverse,
        or regenerate. Keeps the piece thematically connected while
        still moving forward (Reich/Glass-style development)."""
        roll = self.rng.random()
        if roll < 0.35:
            # retrograde
            self.motif = list(reversed(self.motif))
        elif roll < 0.65:
            # small transposition (shift by 1 scale step)
            shift = self.rng.choice([-1, 1])
            self.motif = [s + shift for s in self.motif]
            # clamp individual steps so we don't snowball
            self.motif = [max(-3, min(3, s)) for s in self.motif]
        else:
            # fresh motif
            self.motif = self._make_motif()
        self.motif_pos = 0

    # ----- voice selection ------------------------------------------
    def _pick_voice1_markov(self, chord_tones, last_pitch):
        """Markov-style: 70% chord tones + 30% scale passing notes,
        weighted toward proximity to the previous note."""
        pool = chord_tones if self.rng.random() < 0.70 else self.SCALE
        weights = [1.0 / (1.0 + abs(p - last_pitch) * 0.40) for p in pool]
        return self.rng.choices(pool, weights=weights, k=1)[0]

    def _pick_voice1_motif(self, chord_tones, last_pitch):
        """Advance by the current motif step through the scale, snapping
        to a nearby chord tone when close."""
        step = self.motif[self.motif_pos]
        self.motif_pos = (self.motif_pos + 1) % len(self.motif)
        if self.motif_pos == 0:
            self.motif_cycles += 1
            if self.motif_cycles >= 3:   # evolve every 3 cycles
                self._evolve_motif()
                self.motif_cycles = 0
        # Find last_pitch's nearest position in the scale
        scale = self.SCALE
        idx = min(range(len(scale)),
                  key=lambda i: abs(scale[i] - last_pitch))
        new_idx = max(0, min(len(scale) - 1, idx + step))
        cand = scale[new_idx]
        # Snap to a chord tone if one is within a tone of the candidate
        chord_near = min(chord_tones, key=lambda p: abs(p - cand))
        if abs(chord_near - cand) <= 2:
            return chord_near
        return cand

    def _pick_voice1(self, chord_tones, last_pitch):
        """Blend motif + Markov: 60% motif, 40% Markov. Produces a line
        that feels thematically coherent but never fully deterministic."""
        if self.rng.random() < 0.60:
            return self._pick_voice1_motif(chord_tones, last_pitch)
        return self._pick_voice1_markov(chord_tones, last_pitch)

    def _pick_voice2(self, chord_tones, voice1_pitch, voice2_last,
                     voice1_motion):
        """Counterpoint: among chord tones forming a consonant interval
        with voice 1, pick one that gives contrary motion relative to
        voice 1's last step and stays close to voice 2's last note.
        Returns None if no feasible note exists (voice 2 rests).
        """
        feasible = []
        for p in chord_tones:
            if p == voice1_pitch:
                continue
            interval = abs(p - voice1_pitch) % 12
            if interval in self.CONSONANT_INTERVALS_MOD12:
                # keep voices in separate registers
                if 48 <= p <= 74:
                    feasible.append(p)
        if not feasible:
            return None
        weights = []
        for p in feasible:
            v2_motion = 0 if p == voice2_last else (
                1 if p > voice2_last else -1)
            w = 1.0
            # Preference for contrary motion vs voice 1 (Fux)
            if voice1_motion != 0 and v2_motion == -voice1_motion:
                w *= 2.4
            elif voice1_motion != 0 and v2_motion == voice1_motion:
                w *= 0.7   # parallel motion discouraged
            # Voice leading — closeness
            w *= 1.0 / (1.0 + abs(p - voice2_last) * 0.30)
            weights.append(w)
        return self.rng.choices(feasible, weights=weights, k=1)[0]

    # ----- main scheduler -------------------------------------------
    def _loop(self):
        beat = 0
        v1_last = 69   # A4
        v2_last = 60   # C4
        v1_motion = 0
        rng = self.rng
        wait = self.stop_event.wait
        bsec = self.beat_sec

        while not self.stop_event.is_set():
            chord_idx = (beat // self.BEATS_PER_CHORD) % len(self.CHORDS)
            _name, chord_tones, bass_pitch = self.CHORDS[chord_idx]
            beat_in_chord = beat % self.BEATS_PER_CHORD

            # Bass pedal (strings): at chord start, fire a very smooth
            # swell that holds through most of the chord and releases
            # just before the next one. The 2.5s attack / 3s release
            # means adjacent bass pedals crossfade softly.
            if beat_in_chord == 0:
                bass_sustain = bsec * (self.BEATS_PER_CHORD - 2) - \
                               self.STR_BASS_ATTACK
                if bass_sustain < 0.3:
                    bass_sustain = 0.3
                self._fire_strings(
                    self.CH_BASS, bass_pitch, velocity=68,
                    sustain_sec=bass_sustain,
                    attack_sec=self.STR_BASS_ATTACK,
                    release_sec=self.STR_BASS_RELEASE,
                )

            # Counterpoint (strings): fire at beats 0 and 4 of each chord
            # with probability — gives a slow harmonic-support voice.
            if beat_in_chord in (0, 4) and rng.random() < 0.70:
                v2_pitch = self._pick_voice2(
                    chord_tones, v1_last, v2_last, v1_motion,
                )
                if v2_pitch is not None:
                    v2_last = v2_pitch
                    self._fire_strings(
                        self.CH_CP, v2_pitch, velocity=64,
                        sustain_sec=bsec * 2.0,
                        attack_sec=self.STR_ATTACK_DEFAULT,
                        release_sec=self.STR_RELEASE_DEFAULT,
                    )

            # Melody (Rhodes): fires on ~55% of beats, driven by the
            # motif+Markov blend. Rhodes keeps its natural envelope —
            # that's what gives the piece its attack character.
            if rng.random() < 0.55:
                v1_pitch = self._pick_voice1(chord_tones, v1_last)
                v1_motion = (0 if v1_pitch == v1_last
                             else (1 if v1_pitch > v1_last else -1))
                velocity = 46 + rng.randint(0, 22)
                hold_beats = rng.choice([1, 2, 2, 3])
                self._fire_rhodes(v1_pitch, velocity,
                                  hold_sec=bsec * hold_beats)
                v1_last = v1_pitch

            # Phrase breath at chord boundaries
            if beat_in_chord == self.BEATS_PER_CHORD - 1 and rng.random() < 0.30:
                if wait(bsec * 1.5):
                    break

            if wait(bsec):
                break
            beat += 1

        # Shutdown: release anything still held and mute all channels
        for ch in (self.CH_MELODY, self.CH_CP, self.CH_BASS):
            try:
                self.fs.all_notes_off(ch)
                self._safe_cc(ch, 11, 0)
            except Exception:
                pass


# Back-compat alias for the older name used before the Rhodes + strings
# upgrade.
MinimalPianoPlayer = MinimalEnsemblePlayer


# --- Lens flare (sprite-based, canonical approach: sun→center ghost chain
# + anamorphic horizontal streak + main burst). Only fires when the view
# is roughly pointing at the sun and the sun sits well above the horizon;
# a 0.5s EMA on the combined intensity keeps the flare from flickering as
# the road curves and the view direction oscillates past the threshold.
# Storm dampens it (clouds block the beam). Rare by construction: only
# shows when road heading aligns with the sun within ~35° and the sun is
# > ~0.25 elevation.
def make_flare_disc_texture(size=128):
    """Soft radial disc in RGBA — one sprite reused for every flare element,
    scaled non-uniformly to produce the anamorphic streak."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    d = np.sqrt((xs - c) ** 2 + (ys - c) ** 2) / c
    alpha = np.clip(1.0 - d, 0.0, 1.0) ** 1.7
    rgba = np.zeros((size, size, 4), dtype=np.float32)
    rgba[..., 0] = 1.0
    rgba[..., 1] = 1.0
    rgba[..., 2] = 1.0
    rgba[..., 3] = alpha
    return (rgba * 255).astype(np.uint8)


def _flare_sprite(cx, cy, w, h):
    glBegin(GL_QUADS)
    glTexCoord2f(0.0, 0.0); glVertex2f(cx - w / 2, cy - h / 2)
    glTexCoord2f(1.0, 0.0); glVertex2f(cx + w / 2, cy - h / 2)
    glTexCoord2f(1.0, 1.0); glVertex2f(cx + w / 2, cy + h / 2)
    glTexCoord2f(0.0, 1.0); glVertex2f(cx - w / 2, cy + h / 2)
    glEnd()


# Ghost table: (t along sun→center axis, radius, RGBA). t=1 is sun, 0 is
# screen centre, negative values land past centre on the far side.
_FLARE_GHOSTS = (
    (0.78, 55.0, (1.00, 0.80, 0.55, 0.30)),
    (0.45, 36.0, (0.75, 1.00, 0.80, 0.25)),
    (0.18, 28.0, (0.85, 0.70, 1.00, 0.22)),
    (-0.12, 42.0, (1.00, 0.85, 0.60, 0.26)),
    (-0.42, 28.0, (0.65, 0.90, 1.00, 0.20)),
    (-0.80, 52.0, (1.00, 0.95, 0.70, 0.28)),
    (-1.15, 34.0, (0.90, 0.70, 0.90, 0.18)),
)


def draw_lens_flare(disc_tex, sun_dir, cam_x, cam_y, cam_z,
                    look_x, look_y, look_z, W, H, intensity):
    if intensity < 0.02:
        return
    # Project sun world position (pushed way out along sun_dir) to screen
    sun_world = (cam_x + sun_dir[0] * 600.0,
                 cam_y + sun_dir[1] * 600.0,
                 cam_z + sun_dir[2] * 600.0)
    modelview = glGetDoublev(GL_MODELVIEW_MATRIX)
    projection = glGetDoublev(GL_PROJECTION_MATRIX)
    viewport = glGetIntegerv(GL_VIEWPORT)
    try:
        sx, sy, sz = gluProject(
            sun_world[0], sun_world[1], sun_world[2],
            modelview, projection, viewport,
        )
    except Exception:
        return
    if sz <= 0.0 or sz >= 1.0:
        return  # sun is behind the camera or past the far plane

    # Switch to 2D ortho overlay
    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    glOrtho(0, W, 0, H, -1, 1)
    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()

    glDisable(GL_DEPTH_TEST)
    glDisable(GL_FOG)
    glDisable(GL_LIGHTING)
    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, disc_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)  # additive — flares only brighten
    glDepthMask(GL_FALSE)

    # Anamorphic horizontal streak through the sun
    glColor4f(1.0, 0.97, 0.85, 0.55 * intensity)
    _flare_sprite(sx, sy, W * 0.55, 4.0)

    # Main sun burst glow (two layers for softer falloff)
    glColor4f(1.0, 0.95, 0.78, 0.45 * intensity)
    _flare_sprite(sx, sy, 180.0, 180.0)
    glColor4f(1.0, 0.98, 0.85, 0.65 * intensity)
    _flare_sprite(sx, sy, 80.0, 80.0)

    # Ghosts along the sun→centre axis
    cx_s = W * 0.5
    cy_s = H * 0.5
    for t, radius, col in _FLARE_GHOSTS:
        px = cx_s + (sx - cx_s) * t
        py = cy_s + (sy - cy_s) * t
        glColor4f(col[0], col[1], col[2], col[3] * intensity)
        _flare_sprite(px, py, radius * 2.0, radius * 2.0)

    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)
    glEnable(GL_DEPTH_TEST)
    glEnable(GL_FOG)

    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)
    glPopMatrix()


# --- Rain puddles ---
# Fixed-function GL can't run a real reflection shader, so we fake sky
# reflection with a multi-scale noise "water" texture modulated at draw
# time by the current sky-horizon color, drifted via the texture matrix
# for a ripple animation. A second additive sparkle pass reads the same
# texture at a different offset to fake shifting specular glints.
POND_SPACING = 12.0
POND_MIN_D = 2.0
POND_MAX_D = 24.0


def make_pond_texture(size=256):
    """Seamless ripple/caustic pattern. RGB carries brightness bias for the
    reflection; alpha carries wetness coverage so edges fade off."""
    rng = np.random.default_rng(131)
    # multi-octave seamless noise
    tex = np.zeros((size, size), dtype=np.float32)
    amp_sum = 0.0
    for fx, fy, amp in [(4, 4, 0.5), (8, 8, 0.3), (16, 16, 0.18),
                        (32, 32, 0.1)]:
        tex += _wrap_noise(size, size, fx, fy, rng) * amp
        amp_sum += amp
    tex /= amp_sum
    tex = (tex - tex.min()) / (tex.max() - tex.min() + 1e-9)

    # warp to emphasise bright reflective highlights
    rippled = tex ** 1.5
    # radial alpha falloff so the pond shape reads as a round puddle
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    r = np.sqrt((xs - c) ** 2 + (ys - c) ** 2) / c
    alpha = np.clip(1.0 - r, 0.0, 1.0) ** 1.3

    rgba = np.zeros((size, size, 4), dtype=np.float32)
    rgba[..., 0] = 0.45 + 0.55 * rippled
    rgba[..., 1] = 0.55 + 0.45 * rippled
    rgba[..., 2] = 0.70 + 0.30 * rippled
    rgba[..., 3] = alpha * (0.85 + 0.15 * rippled)
    return (rgba * 255).clip(0, 255).astype(np.uint8)


def draw_ponds(pond_tex, s_car, storm_i, horizon_rgb, amb_rgb, t_time):
    """Scatter reflective water patches on the ground where rain falls.
    Fades in with storm intensity; fades out shortly after the storm does.
    Skips river/frost biomes (already water or snow)."""
    if storm_i < 0.08:
        return
    s_start = math.floor(s_car / POND_SPACING) * POND_SPACING
    max_s = s_car + N_SEG * SEG_LEN
    n_steps = int((max_s - s_start) / POND_SPACING) + 1

    # Precompute biome weights for pond eligibility and density modulation
    s_arr = np.arange(n_steps, dtype=np.float32) * POND_SPACING + s_start
    wL = biome_weights_vec(s_arr, -1)
    wR = biome_weights_vec(s_arr, +1)

    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, pond_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDepthMask(GL_FALSE)
    glEnable(GL_POLYGON_OFFSET_FILL)
    glPolygonOffset(-1.4, -1.4)

    # Reflection color: sky horizon tinted toward water, then gently shaded
    # down by ambient brightness so night ponds look like wet asphalt and
    # daytime ponds look like bright sky.
    refl_r = (horizon_rgb[0] * 0.55 + 0.15) * amb_rgb[0]
    refl_g = (horizon_rgb[1] * 0.60 + 0.15) * amb_rgb[1]
    refl_b = (horizon_rgb[2] * 0.70 + 0.20) * amb_rgb[2]
    base_a = min(1.0, storm_i * 1.4)

    # Animated ripple via texture matrix drift
    glMatrixMode(GL_TEXTURE)
    glPushMatrix()
    glLoadIdentity()
    glTranslatef(t_time * 0.02, t_time * 0.015, 0.0)
    glMatrixMode(GL_MODELVIEW)

    glColor4f(min(1.0, refl_r), min(1.0, refl_g), min(1.0, refl_b), base_a)

    for i in range(n_steps):
        s = float(s_arr[i])
        if s < s_car - 1.0:
            continue
        for side, warr in ((-1, wL), (+1, wR)):
            w = warr[i]
            # skip river (already water) and frost (already snow)
            if w[BIOME_RIVER] > 0.25 or w[BIOME_FROST] > 0.35:
                continue
            key = (int(s * 73) * 2654435761
                   + (0 if side < 0 else 8191)
                   + 13) & 0xFFFFFFFF
            if (key & 0xFF) / 255.0 > 0.30:  # only ~30% slots produce ponds
                continue
            d_off = POND_MIN_D + ((key >> 8) & 0xFF) / 255.0 * (POND_MAX_D - POND_MIN_D)
            radius = 1.5 + ((key >> 16) & 0x0F) / 15.0 * 2.3
            aspect = 1.0 + ((key >> 20) & 0x07) / 7.0 * 0.7
            yaw = ((key >> 24) & 0x3F) / 63.0 * 360.0

            px = curve_x(s) + side * (ROAD_WIDTH / 2 + d_off)
            py = curve_y(s) + 0.02
            pz = -(s - s_car)

            glPushMatrix()
            glTranslatef(px, py, pz)
            glRotatef(yaw, 0, 1, 0)
            # draw as triangle fan ellipse with world-space UVs so the
            # ripple texture tiles consistently across pond sizes
            glBegin(GL_TRIANGLE_FAN)
            glNormal3f(0.0, 1.0, 0.0)
            glTexCoord2f(0.5, 0.5); glVertex3f(0.0, 0.0, 0.0)
            steps = 16
            for k in range(steps + 1):
                t = 2 * math.pi * k / steps
                x = math.cos(t) * radius
                z = math.sin(t) * radius * aspect
                u = 0.5 + (x / (radius * 2.2))
                v = 0.5 + (z / (radius * 2.2))
                glTexCoord2f(u, v); glVertex3f(x, 0.0, z)
            glEnd()
            glPopMatrix()

    glMatrixMode(GL_TEXTURE)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)
    glDisable(GL_POLYGON_OFFSET_FILL)
    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)


def generate_bolt(rng, cam_x, cam_y, cam_z):
    """Fractal lightning via recursive midpoint displacement (Reed & Wyvill
    1986; Kim & Lin 2007 for branches). Starts high in the clouds, drops
    steeply to ground. Horizontal displacement per subdivision decays
    geometrically so the bolt keeps a recognisably vertical trunk with
    smaller zigzags layered on. Vertical displacement is kept small so
    segments don't double back or stall.
    """
    angle = rng.uniform(0, 2 * math.pi)
    dist = rng.uniform(80.0, 200.0)
    end = np.array([cam_x + math.cos(angle) * dist,
                    cam_y - 4.0,
                    cam_z + math.sin(angle) * dist], dtype=np.float32)
    # Start almost directly above the strike point (small cloud wander only)
    start = np.array([end[0] + rng.uniform(-12, 12),
                      cam_y + 130.0,
                      end[2] + rng.uniform(-12, 12)], dtype=np.float32)

    pts = [start, end]
    for it in range(7):  # 7 subdivisions → 129 segments
        new_pts = [pts[0]]
        for i in range(len(pts) - 1):
            a = pts[i]
            b = pts[i + 1]
            mid = (a + b) * 0.5
            seg_len = float(np.linalg.norm(b - a))
            # horizontal displacement shrinks fast → crisp mostly-vertical bolt
            dh = seg_len * 0.22 * (0.55 ** it)
            dv = seg_len * 0.05 * (0.55 ** it)
            disp = np.array([rng.uniform(-1, 1) * dh,
                             rng.uniform(-1, 1) * dv,
                             rng.uniform(-1, 1) * dh], dtype=np.float32)
            new_pts.append(mid + disp)
            new_pts.append(b)
        pts = new_pts

    main_line = np.stack(pts).astype(np.float32)

    # Branch forks — 1 or 2, starting high and peeling off horizontally.
    # Real bolts rarely fork at the bottom, so pick origins from the top
    # half of the trunk.
    n_forks = int(rng.integers(1, 3))
    forks = []
    for _ in range(n_forks):
        i = int(rng.integers(len(pts) // 5, len(pts) // 2))
        origin = pts[i]
        tgt = origin + np.array([rng.uniform(-40, 40),
                                 rng.uniform(-35, -10),
                                 rng.uniform(-40, 40)], dtype=np.float32)
        fpts = [origin, tgt]
        for it in range(4):
            n_new = [fpts[0]]
            for j in range(len(fpts) - 1):
                a = fpts[j]; b = fpts[j + 1]
                mid = (a + b) * 0.5
                sl = float(np.linalg.norm(b - a))
                dh = sl * 0.25 * (0.55 ** it)
                dv = sl * 0.05 * (0.55 ** it)
                d = np.array([rng.uniform(-1, 1) * dh,
                              rng.uniform(-1, 1) * dv,
                              rng.uniform(-1, 1) * dh], dtype=np.float32)
                n_new.append(mid + d)
                n_new.append(b)
            fpts = n_new
        forks.append(np.stack(fpts).astype(np.float32))

    return main_line, forks


def draw_bolt(bolt, age):
    """Two-pass line rendering: thick dim glow beneath, thin bright core on
    top. Real strikes consist of several return strokes flickering in
    quick succession — we approximate that with a 35 Hz sinusoidal
    flicker modulating the overall brightness during the life."""
    if bolt is None or age > BOLT_LIFE:
        return
    main_line, forks = bolt
    life_t = 1.0 - age / BOLT_LIFE
    flicker = 0.55 + 0.45 * math.sin(age * 2 * math.pi * 35.0)
    # crisp early, softer later, with ongoing flicker through the whole life
    vis = max(0.0, (life_t ** 0.5) * flicker)

    glDisable(GL_TEXTURE_2D)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)  # additive
    glDepthMask(GL_FALSE)

    for line_pts in (main_line, *forks):
        # glow pass
        glLineWidth(6.0)
        glColor4f(0.60, 0.78, 1.0, vis * 0.35)
        glBegin(GL_LINE_STRIP)
        for p in line_pts:
            glVertex3f(float(p[0]), float(p[1]), float(p[2]))
        glEnd()
        # core pass
        glLineWidth(2.0)
        glColor4f(1.0, 1.0, 1.0, vis)
        glBegin(GL_LINE_STRIP)
        for p in line_pts:
            glVertex3f(float(p[0]), float(p[1]), float(p[2]))
        glEnd()

    glLineWidth(1.0)
    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)


# --- Textures ---
def make_road_texture(size=512):
    rng = np.random.default_rng(7)
    base = rng.integers(72, 108, (size, size)).astype(np.uint8)
    for _ in range(240):
        cy, cx = rng.integers(0, size, 2)
        r = rng.integers(4, 18)
        shade = int(rng.integers(-15, 15))
        y0, y1 = max(0, cy - r), min(size, cy + r)
        x0, x1 = max(0, cx - r), min(size, cx + r)
        base[y0:y1, x0:x1] = np.clip(
            base[y0:y1, x0:x1].astype(int) + shade, 55, 130
        ).astype(np.uint8)
    tex = np.stack([base, base, base], axis=-1)
    tex[:, 10:20] = 225
    tex[:, size - 20:size - 10] = 225
    dash = size // 10
    for i in range(0, size, dash * 2):
        tex[i:i + dash, size // 2 - 6:size // 2 + 6] = 235
    return tex


def _bilinear_noise(size, freq, rng):
    grid = rng.random((freq + 1, freq + 1))
    ys = np.linspace(0, freq, size)
    xs = np.linspace(0, freq, size)
    yi = ys.astype(int).clip(0, freq - 1)
    xi = xs.astype(int).clip(0, freq - 1)
    fy = (ys - yi)[:, None]
    fx = (xs - xi)[None, :]
    g00 = grid[yi][:, xi]
    g10 = grid[yi + 1][:, xi]
    g01 = grid[yi][:, xi + 1]
    g11 = grid[yi + 1][:, xi + 1]
    return g00 * (1 - fx) * (1 - fy) + g01 * fx * (1 - fy) + g10 * (1 - fx) * fy + g11 * fx * fy


def _wrap_noise(h, w, fx, fy, rng):
    grid = rng.random((fy + 1, fx)).astype(np.float32)
    grid = np.concatenate([grid, grid[:, :1]], axis=1)
    ys = np.linspace(0, fy, h)
    xs = np.linspace(0, fx, w, endpoint=False)
    yi = np.clip(ys.astype(int), 0, fy - 1)
    xi = np.clip(xs.astype(int), 0, fx - 1)
    fyf = (ys - yi)[:, None]
    fxf = (xs - xi)[None, :]
    g00 = grid[yi][:, xi]
    g10 = grid[yi + 1][:, xi]
    g01 = grid[yi][:, xi + 1]
    g11 = grid[yi + 1][:, xi + 1]
    fxs = fxf * fxf * (3 - 2 * fxf)
    fys = fyf * fyf * (3 - 2 * fyf)
    return g00 * (1 - fxs) * (1 - fys) + g01 * fxs * (1 - fys) + g10 * (1 - fxs) * fys + g11 * fxs * fys


def make_cloud_texture(w=1024, h=512):
    """RGBA: white RGB with self-shadow, alpha = cloud density * altitude mask.
    Tinted at draw-time by sky color for dawn/dusk glow.
    """
    rng = np.random.default_rng(3)
    noise = np.zeros((h, w), dtype=np.float32)
    amp_sum = 0.0
    for fx, fy, amp in [(4, 2, 0.55), (8, 4, 0.30), (16, 8, 0.18),
                        (32, 16, 0.10), (64, 32, 0.05)]:
        noise += _wrap_noise(h, w, fx, fy, rng) * amp
        amp_sum += amp
    noise /= amp_sum
    noise = (noise - noise.min()) / (noise.max() - noise.min() + 1e-9)

    v = np.linspace(0.0, 1.0, h, dtype=np.float32)
    mask = np.clip((v - 0.08) * 2.4, 0, 1) * np.clip((1.05 - v) * 1.8, 0, 1)
    mask = mask ** 0.85

    lo, hi = 0.50, 0.74
    t = np.clip((noise - lo) / (hi - lo), 0, 1)
    alpha = t * t * (3 - 2 * t) * mask[:, None]

    shadow = np.roll(noise, 4, axis=0) - noise
    shade = 1.0 - np.clip(shadow * 2.5, 0, 0.35)

    rgba = np.zeros((h, w, 4), dtype=np.float32)
    rgba[..., 0] = 1.0 * shade
    rgba[..., 1] = 1.0 * shade
    rgba[..., 2] = 1.02 * shade
    rgba[..., 3] = alpha
    rgba = np.clip(rgba, 0.0, 1.0)
    return (rgba * 255).astype(np.uint8)


def make_stars_texture(w=1024, h=512):
    """Sparse star field with varying brightness. u wraps horizontally.

    Sharp pin-prick stars, not blurry Gaussian blobs: most stars occupy
    exactly one pixel, a minority a 2x2 sharp cluster, and a rare few a
    2x2 core with a faint 1-pixel cross halo (diffraction-spike feel).
    Uploaded without mipmaps so single-pixel peaks survive rendering.
    """
    rng = np.random.default_rng(19)
    tex = np.zeros((h, w), dtype=np.float32)

    def _peak(y, x, b):
        if 0 <= y < h and 0 <= x < w and tex[y, x] < b:
            tex[y, x] = b

    for _ in range(3200):
        cy = int(rng.integers(2, h - 2))
        cx = int(rng.integers(2, w - 2))
        brightness = float(rng.random() ** 2.5)
        if rng.random() < 0.12:
            brightness *= 2.2

        roll = rng.random()
        if roll < 0.88:
            # pinprick — single pixel, crisp
            _peak(cy, cx, brightness)
        elif roll < 0.975:
            # 2x2 sharp cluster, slightly dimmer per-pixel so the cluster
            # totals a bit brighter than a pinprick without the edge fade
            for dy in (0, 1):
                for dx in (0, 1):
                    _peak(cy + dy, cx + dx, brightness * 0.92)
        else:
            # prominent star: bright 2x2 core with a faint 1-pixel-wide
            # cross halo so the edges read crisp
            for dy in (0, 1):
                for dx in (0, 1):
                    _peak(cy + dy, cx + dx, brightness)
            halo = brightness * 0.18
            for dy in (0, 1):
                _peak(cy + dy, cx - 1, halo)
                _peak(cy + dy, cx + 2, halo)
            for dx in (0, 1):
                _peak(cy - 1, cx + dx, halo)
                _peak(cy + 2, cx + dx, halo)

    tex = np.clip(tex, 0, 1)
    # fewer stars near horizon (atmospheric extinction)
    v = np.linspace(0, 1, h)
    mask = np.clip((v - 0.04) * 1.8, 0, 1)
    tex *= mask[:, None]
    # slight blueish tint
    rgb = np.stack([0.92 * tex, 0.95 * tex, 1.00 * tex], axis=-1)
    return (rgb * 255).clip(0, 255).astype(np.uint8)


def make_terrain_texture(size=512):
    rng = np.random.default_rng(11)
    tex = np.zeros((size, size), dtype=np.float32)
    for freq, amp in [(6, 0.5), (14, 0.3), (30, 0.15), (64, 0.08)]:
        tex += _bilinear_noise(size, freq, rng) * amp
    tex = (tex - tex.min()) / (tex.max() - tex.min())
    val = 0.82 + 0.55 * (tex - 0.5)
    val = np.clip(val, 0.45, 1.25)
    rgb = np.stack([val, val, val], axis=-1)
    return (rgb * 255).clip(0, 255).astype(np.uint8)


def upload_texture(img, internal=GL_RGB, src=GL_RGB, mipmaps=True):
    tex_id = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, tex_id)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,
                    GL_LINEAR_MIPMAP_LINEAR if mipmaps else GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    h, w = img.shape[:2]
    glTexImage2D(GL_TEXTURE_2D, 0, internal, w, h, 0, src, GL_UNSIGNED_BYTE, img.tobytes())
    if mipmaps:
        glGenerateMipmap(GL_TEXTURE_2D)
    return tex_id


# --- Sky dome ---
def build_sky_dome(radius=SKY_DOME_R, stacks=24, slices=48):
    W = slices + 1
    n = (stacks + 1) * W
    verts = np.zeros((n, 3), dtype=np.float32)
    tcs = np.zeros((n, 2), dtype=np.float32)
    vfrac = np.zeros(n, dtype=np.float32)  # 0 at horizon, 1 at zenith
    for i in range(stacks + 1):
        lat = (i / stacks) * (math.pi / 2) - 0.05
        y = radius * math.sin(lat)
        r = radius * math.cos(lat)
        v = i / stacks
        for j in range(W):
            lon = (j / slices) * 2 * math.pi
            k = i * W + j
            verts[k] = (r * math.cos(lon), y, r * math.sin(lon))
            tcs[k] = (j / slices, v)
            vfrac[k] = v
    idx = np.zeros((stacks * slices * 6,), dtype=np.uint32)
    p = 0
    for i in range(stacks):
        for j in range(slices):
            a = i * W + j
            b = i * W + (j + 1)
            c = (i + 1) * W + j
            d2 = (i + 1) * W + (j + 1)
            idx[p:p + 6] = [a, b, c, b, d2, c]
            p += 6
    return verts, tcs, vfrac, idx


def compute_dome_colors(vfrac, zenith, horizon):
    """Per-vertex color from v-fraction, biased so gradient feels natural."""
    ev = vfrac ** 0.7
    z = np.array(zenith, dtype=np.float32)
    h = np.array(horizon, dtype=np.float32)
    return (h[None, :] * (1.0 - ev[:, None]) + z[None, :] * ev[:, None]).astype(np.float32)


def draw_sky(sky_state, cam_x, cam_y, cam_z, t_time, t_day,
             storm=0.0, flash=0.0):
    verts, tcs, vfrac, idx, cloud_tex, stars_tex = sky_state
    zenith, horizon = sky_colors_at(t_day, storm, flash)
    colors = compute_dome_colors(vfrac, zenith, horizon)
    night_a = night_factor_at(t_day)
    cloud_tint = cloud_tint_at(t_day, storm)

    glDepthMask(GL_FALSE)
    glDisable(GL_DEPTH_TEST)
    glDisable(GL_FOG)
    glDisable(GL_CULL_FACE)
    glDisable(GL_TEXTURE_2D)

    glPushMatrix()
    glTranslatef(cam_x, cam_y, cam_z)

    # Pass 1: gradient (per-vertex colors, no texture)
    glEnableClientState(GL_VERTEX_ARRAY)
    glEnableClientState(GL_COLOR_ARRAY)
    glDisableClientState(GL_TEXTURE_COORD_ARRAY)
    glVertexPointer(3, GL_FLOAT, 0, verts)
    glColorPointer(3, GL_FLOAT, 0, colors)
    glDrawElements(GL_TRIANGLES, len(idx), GL_UNSIGNED_INT, idx)
    glDisableClientState(GL_COLOR_ARRAY)

    # Pass 2: stars (additive, only at night)
    if night_a > 0.01:
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, stars_tex)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)
        glColor4f(night_a, night_a, night_a, night_a)
        glEnableClientState(GL_TEXTURE_COORD_ARRAY)
        glTexCoordPointer(2, GL_FLOAT, 0, tcs)
        # slow rotation for celestial drift
        glMatrixMode(GL_TEXTURE)
        glLoadIdentity()
        glTranslatef(t_day * 0.5, 0.0, 0.0)
        glMatrixMode(GL_MODELVIEW)
        glDrawElements(GL_TRIANGLES, len(idx), GL_UNSIGNED_INT, idx)
        glMatrixMode(GL_TEXTURE); glLoadIdentity(); glMatrixMode(GL_MODELVIEW)
        glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        glDisable(GL_BLEND)

    # Pass 3: clouds (alpha blended, tinted by sky/sun)
    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, cloud_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glColor4f(cloud_tint[0], cloud_tint[1], cloud_tint[2], 1.0)
    glEnableClientState(GL_TEXTURE_COORD_ARRAY)
    glTexCoordPointer(2, GL_FLOAT, 0, tcs)
    glMatrixMode(GL_TEXTURE)
    glLoadIdentity()
    glTranslatef(t_time * 0.004, 0.0, 0.0)
    glMatrixMode(GL_MODELVIEW)
    glDrawElements(GL_TRIANGLES, len(idx), GL_UNSIGNED_INT, idx)
    glMatrixMode(GL_TEXTURE); glLoadIdentity(); glMatrixMode(GL_MODELVIEW)
    glDisableClientState(GL_TEXTURE_COORD_ARRAY)
    glDisable(GL_BLEND)

    glDisableClientState(GL_VERTEX_ARRAY)
    glPopMatrix()

    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_DEPTH_TEST)
    glDepthMask(GL_TRUE)
    glEnable(GL_FOG)


# --- Sun / Moon billboards ---
def draw_celestial(cam_x, cam_y, cam_z, direction, radius,
                   core_color, glow_color, core_alpha=1.0):
    """Billboard disc with soft glow, placed on the dome along `direction`."""
    if direction[1] < -0.25:
        return  # well below horizon
    up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    right = np.cross(direction, up)
    rn = np.linalg.norm(right)
    if rn < 1e-3:
        right = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    else:
        right /= rn
    up2 = np.cross(right, direction)
    up2 /= (np.linalg.norm(up2) + 1e-9)

    center = np.array([cam_x, cam_y, cam_z], dtype=np.float32) + direction * (SKY_DOME_R * 0.92)

    # fade when near/below horizon
    alt_fade = max(0.0, min(1.0, (direction[1] + 0.15) / 0.25))

    glDisable(GL_TEXTURE_2D)
    glDisable(GL_FOG)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    glDepthMask(GL_FALSE)

    # Halo layers: large dim, medium, tight core
    layers = [
        (4.0, 0.08, glow_color),
        (2.2, 0.22, glow_color),
        (1.1, 0.55, core_color),
    ]
    for r_mul, a_mul, col in layers:
        r = radius * r_mul
        glBegin(GL_TRIANGLE_FAN)
        glColor4f(col[0], col[1], col[2], a_mul * alt_fade * core_alpha)
        glVertex3f(*center)
        steps = 36
        for i in range(steps + 1):
            t = 2 * math.pi * i / steps
            p = center + right * (r * math.cos(t)) + up2 * (r * math.sin(t))
            glColor4f(col[0], col[1], col[2], 0.0)
            glVertex3f(*p)
        glEnd()

    # bright solid core (opaque-ish)
    r = radius * 0.6
    glBegin(GL_TRIANGLE_FAN)
    glColor4f(core_color[0], core_color[1], core_color[2], alt_fade * core_alpha)
    glVertex3f(*center)
    for i in range(33):
        t = 2 * math.pi * i / 32
        p = center + right * (r * math.cos(t)) + up2 * (r * math.sin(t))
        glColor4f(core_color[0], core_color[1], core_color[2], alt_fade * core_alpha)
        glVertex3f(*p)
    glEnd()

    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)
    glEnable(GL_FOG)


def sun_color(t_day):
    el = sun_dir_at(t_day)[1]
    warm = _smooth(1.0 - abs(el) / 0.30) * (1.0 if el > -0.25 else 0.0)
    noon = _smooth((el - 0.1) / 0.5)
    # sunset/sunrise hot orange, noon white-yellow
    r = 1.0
    g = 0.75 + 0.20 * noon - 0.15 * warm
    b = 0.55 + 0.40 * noon - 0.45 * warm
    return (min(1.0, r), max(0.0, min(1.0, g)), max(0.0, min(1.0, b)))


# --- Terrain rendering ---
_TERRAIN_INDICES = {}


def grid_indices(NS, K):
    key = (NS, K)
    if key in _TERRAIN_INDICES:
        return _TERRAIN_INDICES[key]
    idx = np.zeros(((NS - 1) * (K - 1) * 6,), dtype=np.uint32)
    p = 0
    for i in range(NS - 1):
        for k in range(K - 1):
            a = i * K + k
            b = i * K + (k + 1)
            c = (i + 1) * K + k
            d2 = (i + 1) * K + (k + 1)
            idx[p:p + 6] = [a, c, b, b, c, d2]
            p += 6
    _TERRAIN_INDICES[key] = idx
    return idx


def build_side_arrays(s_arr, s_car, side, t_time, amb_rgb):
    """Returns (verts, col_rgb, tcs, snow_rgba, snow_tcs).

    * col_rgb is the biome-tinted base color for pass 1 (terrain noise tex).
    * snow_rgba carries ambient RGB + per-vertex snow alpha for pass 2 — the
      alpha is the max of the frost biome weight at that s (full cover in
      frost zones) and an altitude-based snowcap factor (mountains get caps
      everywhere). The snow pass uses a coarser tex-coord scale so the snow
      photo tiles at a larger world frequency than the ground noise.
    """
    NS = len(s_arr)
    K = K_BANDS
    d = TERRAIN_EDGE_D + np.arange(K, dtype=np.float32) * D_STEP
    rx = curve_x_np(s_arr)
    ry = curve_y_np(s_arr)
    rz = -(s_arr - s_car)
    weights = biome_weights_vec(s_arr, side)
    s2 = s_arr[:, None]
    d2 = d[None, :]
    plain, hill, mnt, river, forest, frost, city = terrain_heights(s2, d2, t_time)
    off = (weights[:, 0:1] * plain + weights[:, 1:2] * hill
           + weights[:, 2:3] * mnt + weights[:, 3:4] * river
           + weights[:, 4:5] * forest + weights[:, 5:6] * frost
           + weights[:, 6:7] * city)
    # Seamless shoulder: ramp the biome height offset from 0 at the road
    # edge up to full strength over the first ~3m perpendicular. Eliminates
    # the curb drop at the pavement (each biome profile naturally sits a bit
    # below road level at d=0) while keeping the biome geometry beyond.
    edge_fac = np.clip(d / 3.0, 0.0, 1.0)
    edge_fac = edge_fac * edge_fac * (3 - 2 * edge_fac)  # smoothstep
    off = off * edge_fac[None, :]
    x = rx[:, None] + side * (ROAD_WIDTH / 2 + d[None, :])
    y = ry[:, None] + off
    z = np.broadcast_to(rz[:, None], (NS, K))
    verts = np.stack([x, y, z], axis=-1).reshape(-1, 3).astype(np.float32)

    col_s = weights @ BIOME_COLOR
    amb = np.array(amb_rgb, dtype=np.float32)
    col_s = np.clip(col_s * amb[None, :], 0.0, 1.0)
    col = np.broadcast_to(col_s[:, None, :], (NS, K, 3)).reshape(-1, 3).astype(np.float32)

    u = (d[None, :] * 0.08).repeat(NS, axis=0)
    v = np.broadcast_to((s_arr * 0.08)[:, None], (NS, K))
    tcs = np.stack([u, v], axis=-1).reshape(-1, 2).astype(np.float32)

    # Snow overlay alpha: full cover in frost biome, altitude cap on mountains
    frost_cov = np.broadcast_to(weights[:, BIOME_FROST:BIOME_FROST + 1], (NS, K))
    alt_cap = np.clip((y - 11.0) / 11.0, 0.0, 1.0) ** 1.4 * 0.90
    snow_a = np.maximum(frost_cov, alt_cap).reshape(-1).astype(np.float32)

    snow_rgba = np.zeros((NS * K, 4), dtype=np.float32)
    snow_rgba[:, 0] = min(1.0, amb_rgb[0])
    snow_rgba[:, 1] = min(1.0, amb_rgb[1])
    snow_rgba[:, 2] = min(1.0, amb_rgb[2])
    snow_rgba[:, 3] = snow_a

    u_s = (d[None, :] * 0.05).repeat(NS, axis=0)
    v_s = np.broadcast_to((s_arr * 0.05)[:, None], (NS, K))
    snow_tcs = np.stack([u_s, v_s], axis=-1).reshape(-1, 2).astype(np.float32)

    return verts, col, tcs, snow_rgba, snow_tcs


def draw_terrain(terrain_tex, snow_tex, s_car, t_time, amb_rgb):
    NS = N_SEG + 1
    s_arr = (np.arange(NS, dtype=np.float32) * SEG_LEN) + s_car
    idx = grid_indices(NS, K_BANDS)

    glEnable(GL_TEXTURE_2D)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnableClientState(GL_VERTEX_ARRAY)
    glEnableClientState(GL_COLOR_ARRAY)
    glEnableClientState(GL_TEXTURE_COORD_ARRAY)

    # Build once per side, reuse buffers across both passes
    sides = []
    for side in (-1, +1):
        sides.append(build_side_arrays(s_arr, s_car, side, t_time, amb_rgb))

    # Pass 1: biome-tinted ground
    glBindTexture(GL_TEXTURE_2D, terrain_tex)
    for verts, cols, tcs, _snow_rgba, _snow_tcs in sides:
        glVertexPointer(3, GL_FLOAT, 0, verts)
        glColorPointer(3, GL_FLOAT, 0, cols)
        glTexCoordPointer(2, GL_FLOAT, 0, tcs)
        glDrawElements(GL_TRIANGLES, len(idx), GL_UNSIGNED_INT, idx)

    # Pass 2: real snow photo overlaid where frost weight > 0 or altitude is high
    glBindTexture(GL_TEXTURE_2D, snow_tex)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glEnable(GL_POLYGON_OFFSET_FILL)
    glPolygonOffset(-1.0, -1.0)  # coplanar with pass 1 — bias forward slightly
    for verts, _cols, _tcs, snow_rgba, snow_tcs in sides:
        glVertexPointer(3, GL_FLOAT, 0, verts)
        glColorPointer(4, GL_FLOAT, 0, snow_rgba)
        glTexCoordPointer(2, GL_FLOAT, 0, snow_tcs)
        glDrawElements(GL_TRIANGLES, len(idx), GL_UNSIGNED_INT, idx)
    glDisable(GL_POLYGON_OFFSET_FILL)
    glDisable(GL_BLEND)

    glDisableClientState(GL_VERTEX_ARRAY)
    glDisableClientState(GL_COLOR_ARRAY)
    glDisableClientState(GL_TEXTURE_COORD_ARRAY)


# --- Civil structures: bridges, tunnels ---
# Placement strategy is biome-driven rather than random:
#   * Bridge: placed where the road passes a river biome on either side —
#     the road always crosses water there, so rails fit naturally.
#   * Tunnel: placed where mountain biome appears on BOTH sides — the
#     road is in a mountain pass; the tunnel walls occlude the mountain
#     rise and give the illusion of cutting through rock.
# Collision avoidance is automatic because the biomes are disjoint:
# trees (forest / frost) and buildings (city) live in different zones
# from rivers and mountains, so structures never overlap them. Tunnel
# walls sit just outside the road edge and cover the mountain's
# steep rise behind.
#
# Weather reaction is per-vertex: the top edge of rails mixes toward
# snow-white as frost biome weight rises at the camera, and the whole
# concrete colour darkens while it's raining (wet surface absorbs more
# light). Tunnel interiors are shielded from weather — they stay a
# darker concrete regardless of the sky.

CONCRETE_COLOR_BASE = (0.78, 0.78, 0.82)


def make_concrete_texture(size=512, seed=211):
    """Grey concrete with per-pixel noise, dark stains, and horizontal
    panel seams every 80 px so it reads as cast-in-place construction."""
    rng = np.random.default_rng(seed)
    base = rng.integers(140, 180, (size, size)).astype(np.int16)
    # dark stains
    for _ in range(80):
        cy, cx = rng.integers(0, size, 2)
        r = int(rng.integers(5, 22))
        y0, y1 = max(0, cy - r), min(size, cy + r)
        x0, x1 = max(0, cx - r), min(size, cx + r)
        shade = int(rng.integers(-22, 22))
        base[y0:y1, x0:x1] = np.clip(base[y0:y1, x0:x1] + shade, 90, 220)
    # panel seams
    for y in range(0, size, 80):
        base[y:y + 2] = 100
    base = base.astype(np.uint8)
    rgb = np.stack([base, base, np.clip(base.astype(int) + 3, 0, 255).astype(np.uint8)], axis=-1)
    return rgb


def _structure_tint(amb_rgb, storm_i, base_scale=0.82):
    """Wet concrete darkens with storm; dry stays close to ambient tint."""
    wet = 1.0 - 0.28 * storm_i
    return (amb_rgb[0] * base_scale * wet,
            amb_rgb[1] * base_scale * wet,
            amb_rgb[2] * base_scale * wet)


def _snow_mix(base_col, frost_i, amount=0.70):
    """Mix base colour toward snow white by frost × amount."""
    snow = (0.92, 0.94, 0.98)
    t = frost_i * amount
    return (base_col[0] * (1 - t) + snow[0] * t,
            base_col[1] * (1 - t) + snow[1] * t,
            base_col[2] * (1 - t) + snow[2] * t)


def draw_civil_structures(s_car, concrete_tex, amb_rgb, storm_i, frost_i):
    NS = N_SEG + 1
    s_arr = np.arange(NS, dtype=np.float32) * SEG_LEN + s_car
    wL = biome_weights_vec(s_arr, -1)
    wR = biome_weights_vec(s_arr, +1)
    bridge_w = np.maximum(wL[:, BIOME_RIVER], wR[:, BIOME_RIVER])
    # Tunnels need a mountain presence on average — strictly requiring
    # BOTH sides is too restrictive given the biome distribution (frost
    # is the only forced-symmetric biome), so we use the mean weight
    # instead. Practically: a tunnel fires when the road sits in a
    # mountain pass on at least one side with the other side close.
    tunnel_w = 0.5 * (wL[:, BIOME_MOUNTAIN] + wR[:, BIOME_MOUNTAIN])

    if bridge_w.max() < 0.25 and tunnel_w.max() < 0.30:
        return  # nothing to draw in the visible window

    base = _structure_tint(amb_rgb, storm_i)

    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, concrete_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDepthMask(GL_TRUE)  # solid structures participate in depth writes

    if bridge_w.max() >= 0.25:
        _draw_bridges(s_arr, bridge_w, base, frost_i, s_car)
    if tunnel_w.max() >= 0.30:
        _draw_tunnels(s_arr, tunnel_w, base, s_car)

    glDisable(GL_BLEND)


def _emit_strip_segments(s_arr, weight_arr, threshold, emit_pair):
    """Walk the s_arr array and, for each run of consecutive samples where
    weight > threshold, call emit_pair(i, alpha) twice per vertex pair.
    Uses separate GL_QUAD_STRIP invocations per run so discontinuities
    don't smear across large gaps."""
    in_strip = False
    for i in range(len(s_arr)):
        w = float(weight_arr[i])
        if w < threshold:
            if in_strip:
                glEnd()
                in_strip = False
        else:
            alpha = float(min(1.0, (w - threshold) / 0.30))
            if not in_strip:
                glBegin(GL_QUAD_STRIP)
                in_strip = True
            emit_pair(i, alpha)
    if in_strip:
        glEnd()


def _draw_bridges(s_arr, bridge_w, base_col, frost_i, s_car):
    RAIL_H = 1.05
    base_top = _snow_mix(base_col, frost_i, amount=0.80)  # snow caps the top
    base_bot = base_col

    for side in (-1, +1):
        def emit(i, alpha, side=side):
            s = float(s_arr[i])
            x = curve_x(s) + side * (ROAD_WIDTH / 2 + 0.10)
            y_base = curve_y(s) + 0.05
            z = -(s - s_car)
            u = s * 0.18
            glColor4f(base_bot[0], base_bot[1], base_bot[2], alpha)
            glTexCoord2f(u, 0.0); glVertex3f(x, y_base, z)
            glColor4f(base_top[0], base_top[1], base_top[2], alpha)
            glTexCoord2f(u, 1.0); glVertex3f(x, y_base + RAIL_H, z)
        _emit_strip_segments(s_arr, bridge_w, 0.25, emit)


def _draw_tunnels(s_arr, tunnel_w, base_col, s_car):
    WALL_H = 5.6
    CEIL_Y = 5.4
    inner = (base_col[0] * 0.55, base_col[1] * 0.55, base_col[2] * 0.58)
    ceiling = (base_col[0] * 0.40, base_col[1] * 0.40, base_col[2] * 0.44)

    for side in (-1, +1):
        def emit(i, alpha, side=side):
            s = float(s_arr[i])
            x = curve_x(s) + side * (ROAD_WIDTH / 2 + 0.05)
            y_base = curve_y(s) + 0.03
            z = -(s - s_car)
            u = s * 0.18
            glColor4f(inner[0], inner[1], inner[2], alpha)
            glTexCoord2f(u, 0.0); glVertex3f(x, y_base, z)
            glTexCoord2f(u, 1.5); glVertex3f(x, y_base + WALL_H, z)
        _emit_strip_segments(s_arr, tunnel_w, 0.30, emit)

    def emit_ceil(i, alpha):
        s = float(s_arr[i])
        x = curve_x(s)
        y = curve_y(s) + CEIL_Y
        z = -(s - s_car)
        u = s * 0.18
        glColor4f(ceiling[0], ceiling[1], ceiling[2], alpha)
        glTexCoord2f(0.0, u); glVertex3f(x - ROAD_WIDTH / 2, y, z)
        glTexCoord2f(1.0, u); glVertex3f(x + ROAD_WIDTH / 2, y, z)
    _emit_strip_segments(s_arr, tunnel_w, 0.30, emit_ceil)


def draw_road(tex_id, s_car, amb_rgb):
    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, tex_id)
    glColor3f(min(1.0, amb_rgb[0]), min(1.0, amb_rgb[1]), min(1.0, amb_rgb[2]))
    glBegin(GL_QUAD_STRIP)
    for i in range(N_SEG + 1):
        s = s_car + i * SEG_LEN
        x = curve_x(s)
        y = curve_y(s)
        z = -(s - s_car)
        v = s * 0.12
        glTexCoord2f(0.0, v); glVertex3f(x - ROAD_WIDTH / 2, y + 0.03, z)
        glTexCoord2f(1.0, v); glVertex3f(x + ROAD_WIDTH / 2, y + 0.03, z)
    glEnd()
    glDisable(GL_TEXTURE_2D)


def draw_lamps(s_car, night_a):
    if night_a < 0.02:
        return  # lamps off during the day
    s_start = math.ceil(s_car / LAMP_SPACING) * LAMP_SPACING

    # posts — slightly brighter at night
    post_shade = 0.22 + 0.18 * night_a
    glDisable(GL_TEXTURE_2D)
    glColor3f(post_shade, post_shade, post_shade + 0.02)
    glLineWidth(2.0)
    glBegin(GL_LINES)
    for i in range(N_LAMPS):
        s = s_start + i * LAMP_SPACING
        x = curve_x(s); y = curve_y(s); z = -(s - s_car)
        for side in (-1, 1):
            if not is_plain(s, side):
                continue
            px = x + side * (ROAD_WIDTH / 2 + 0.6)
            glVertex3f(px, y, z); glVertex3f(px, y + 3.2, z)
            glVertex3f(px, y + 3.2, z); glVertex3f(px - side * 1.0, y + 3.2, z)
    glEnd()

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    glDepthMask(GL_FALSE)
    for i in range(N_LAMPS):
        s = s_start + i * LAMP_SPACING
        d = s - s_car
        if d < 0:
            continue
        fade = max(0.0, 1.0 - d / (N_LAMPS * LAMP_SPACING * 0.9)) * night_a
        x = curve_x(s); y = curve_y(s); z = -d
        for side in (-1, 1):
            if not is_plain(s, side):
                continue
            px = x + side * (ROAD_WIDTH / 2 + 0.6) - side * 1.0
            py = y + 3.15
            for (r, a) in ((1.4, 0.15), (0.7, 0.35), (0.28, 0.9)):
                rr = r * (0.6 + 0.6 * (1.0 - min(1.0, d / 300.0)))
                glColor4f(1.0, 0.92, 0.55, a * fade)
                glBegin(GL_TRIANGLE_FAN)
                glVertex3f(px, py, z)
                for k in range(13):
                    t = 2 * math.pi * k / 12
                    glVertex3f(px + math.cos(t) * rr, py + math.sin(t) * rr, z)
                glEnd()
            glColor4f(1.0, 0.85, 0.4, 0.22 * fade)
            glBegin(GL_QUADS)
            rx, rz = 2.2, 2.2
            glVertex3f(px - rx, y + 0.04, z - rz)
            glVertex3f(px + rx, y + 0.04, z - rz)
            glVertex3f(px + rx, y + 0.04, z + rz)
            glVertex3f(px - rx, y + 0.04, z + rz)
            glEnd()

    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)


# --- Trees (procedural recursive branching with L-system-style randomness) ---
# Design: each tree is a recursive fractal. A trunk cylinder tapers up, then
# splits into 2-3 children with random yaw+pitch. Terminal nodes emit a
# cross-billboard leaf cluster with an alpha-cutout texture. Each variant is
# baked once into a display list, then instanced across forest zones with
# glTranslate/glRotate/glScale and glCallList.
#
# Bark texture is a real CC0 photo from ambientCG (Bark001). Leaf texture is
# procedurally generated with random ovoid blobs on a transparent background;
# multiple crossed quads read the same texture to imply volumetric foliage.


def load_texture_file(path):
    surf = pygame.image.load(path)
    arr = pygame.surfarray.array3d(surf)       # (w, h, 3)
    arr = np.transpose(arr, (1, 0, 2))         # (h, w, 3) row-major
    arr = arr[::-1]                             # GL origin is lower-left
    return np.ascontiguousarray(arr, dtype=np.uint8)


def make_leaf_texture(size=256):
    """RGBA foliage cluster — ovoid leaves with soft alpha, transparent BG."""
    rng = np.random.default_rng(23)
    rgba = np.zeros((size, size, 4), dtype=np.float32)
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)

    for _ in range(22):
        cx = float(rng.integers(28, size - 28))
        cy = float(rng.integers(28, size - 28))
        rx = float(rng.integers(8, 15))
        ry = float(rng.integers(14, 26))
        angle = float(rng.uniform(0.0, math.pi))
        g = 0.45 + float(rng.uniform(-0.08, 0.18))
        r = 0.18 + float(rng.uniform(-0.05, 0.10))
        b = 0.12 + float(rng.uniform(-0.03, 0.10))
        cosA, sinA = math.cos(angle), math.sin(angle)
        dx = xs - cx
        dy = ys - cy
        xr = dx * cosA + dy * sinA
        yr = -dx * sinA + dy * cosA
        dist = (xr / rx) ** 2 + (yr / ry) ** 2
        m = np.clip(1.0 - dist, 0.0, 1.0) ** 1.2
        # darken a faint central vein line
        vein = 1.0 - 0.18 * np.exp(-(xr ** 2) / max(0.3, (rx * 0.18) ** 2))
        sa = m * 0.95
        rgba[..., 0] = rgba[..., 0] * (1 - sa) + r * vein * sa
        rgba[..., 1] = rgba[..., 1] * (1 - sa) + g * vein * sa
        rgba[..., 2] = rgba[..., 2] * (1 - sa) + b * vein * sa
        rgba[..., 3] = np.maximum(rgba[..., 3], m)

    return (rgba * 255).clip(0, 255).astype(np.uint8)


def make_snow_bark_texture(bark_rgb):
    """Brighten bark toward snow-covered wood; keep bark detail showing through."""
    rng = np.random.default_rng(57)
    snow = np.array([232.0, 238.0, 248.0], dtype=np.float32)
    t = 0.60  # 0 = raw bark, 1 = pure snow
    out = bark_rgb.astype(np.float32) * (1.0 - t) + snow * t
    # per-pixel noise so snow doesn't look flat
    noise = rng.integers(-14, 14, bark_rgb.shape[:2], dtype=np.int16).astype(np.float32)
    out[..., 0] = np.clip(out[..., 0] + noise, 0, 255)
    out[..., 1] = np.clip(out[..., 1] + noise, 0, 255)
    out[..., 2] = np.clip(out[..., 2] + noise * 0.6 + 6.0, 0, 255)  # tilt toward blue
    return out.astype(np.uint8)


def make_snow_leaf_texture(size=256):
    """Leaf-cluster texture where most leaves carry a snow cap — mostly white
    with occasional green peek-through and pale veins."""
    rng = np.random.default_rng(47)
    rgba = np.zeros((size, size, 4), dtype=np.float32)
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    for _ in range(24):
        cx = float(rng.integers(28, size - 28))
        cy = float(rng.integers(28, size - 28))
        rx = float(rng.integers(9, 16))
        ry = float(rng.integers(15, 26))
        angle = float(rng.uniform(0.0, math.pi))
        if rng.random() < 0.25:
            # unsnowed leaf — still green for contrast
            r = 0.32 + float(rng.uniform(-0.05, 0.08))
            g = 0.52 + float(rng.uniform(-0.05, 0.15))
            b = 0.28 + float(rng.uniform(-0.05, 0.10))
        else:
            w = 0.90 + float(rng.uniform(-0.08, 0.06))
            r = w
            g = w * 0.99
            b = min(1.0, w * 1.03)
        cosA, sinA = math.cos(angle), math.sin(angle)
        dx = xs - cx
        dy = ys - cy
        xr = dx * cosA + dy * sinA
        yr = -dx * sinA + dy * cosA
        dist = (xr / rx) ** 2 + (yr / ry) ** 2
        m = np.clip(1.0 - dist, 0.0, 1.0) ** 1.2
        vein = 1.0 - 0.12 * np.exp(-(xr ** 2) / max(0.3, (rx * 0.18) ** 2))
        sa = m * 0.95
        rgba[..., 0] = rgba[..., 0] * (1 - sa) + r * vein * sa
        rgba[..., 1] = rgba[..., 1] * (1 - sa) + g * vein * sa
        rgba[..., 2] = rgba[..., 2] * (1 - sa) + b * vein * sa
        rgba[..., 3] = np.maximum(rgba[..., 3], m)
    return (rgba * 255).clip(0, 255).astype(np.uint8)


def make_snowflake_texture(size=32):
    """Soft white disc for point-sprite snowflakes (RGBA, alpha falloff)."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    d = np.sqrt((xs - c) ** 2 + (ys - c) ** 2) / c
    alpha = np.clip(1.0 - d, 0.0, 1.0) ** 2.2
    rgba = np.zeros((size, size, 4), dtype=np.float32)
    rgba[..., 0] = 1.0
    rgba[..., 1] = 1.0
    rgba[..., 2] = 1.0
    rgba[..., 3] = alpha
    return (rgba * 255).astype(np.uint8)


_TREE_QUADRIC = None


def _tree_quadric():
    global _TREE_QUADRIC
    if _TREE_QUADRIC is None:
        _TREE_QUADRIC = gluNewQuadric()
        gluQuadricTexture(_TREE_QUADRIC, GL_TRUE)
        gluQuadricNormals(_TREE_QUADRIC, GLU_SMOOTH)
    return _TREE_QUADRIC


def _draw_leaf_cluster(rng, size):
    """3–5 crossed textured quads at current transform, facing varied directions."""
    n = rng.randint(3, 5)
    for _ in range(n):
        yaw = rng.uniform(0.0, 360.0)
        pitch = rng.uniform(-35.0, 35.0)
        roll = rng.uniform(-20.0, 20.0)
        glPushMatrix()
        glRotatef(yaw, 0, 1, 0)
        glRotatef(pitch, 1, 0, 0)
        glRotatef(roll, 0, 0, 1)
        s = size
        glBegin(GL_QUADS)
        glTexCoord2f(0.0, 0.0); glVertex3f(-s, -s * 0.3, 0.0)
        glTexCoord2f(1.0, 0.0); glVertex3f(+s, -s * 0.3, 0.0)
        glTexCoord2f(1.0, 1.0); glVertex3f(+s, s * 1.7, 0.0)
        glTexCoord2f(0.0, 1.0); glVertex3f(-s, s * 1.7, 0.0)
        glEnd()
        glPopMatrix()


def _emit_branch(rng, length, radius, depth, max_depth, bark_tex, leaf_tex):
    """Recursive branch emitter. Embeds texture binds + alpha-test state into
    the currently compiling display list."""
    # Bark segment (tapered cylinder aligned along +Y)
    glBindTexture(GL_TEXTURE_2D, bark_tex)
    glDisable(GL_ALPHA_TEST)
    glPushMatrix()
    glRotatef(-90.0, 1, 0, 0)  # gluCylinder lies along +Z → rotate into +Y
    tip = radius * 0.72
    gluCylinder(_tree_quadric(), radius, tip, length, 8, 1)
    glPopMatrix()

    terminal = depth >= max_depth or length < 0.35
    if terminal:
        # Leaf cluster at branch tip
        glBindTexture(GL_TEXTURE_2D, leaf_tex)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.4)
        glPushMatrix()
        glTranslatef(0.0, length, 0.0)
        leaf_size = max(0.35, radius * 7.5 + 0.4)
        _draw_leaf_cluster(rng, leaf_size)
        glPopMatrix()
        return

    glPushMatrix()
    glTranslatef(0.0, length, 0.0)
    n_children = rng.choice([2, 2, 3]) if depth < max_depth - 1 else rng.choice([1, 2])
    base_yaw = rng.uniform(0.0, 360.0)
    for i in range(n_children):
        yaw = base_yaw + (360.0 / n_children) * i + rng.uniform(-22.0, 22.0)
        pitch = rng.uniform(22.0, 46.0) * (1.0 - depth * 0.06)
        glPushMatrix()
        glRotatef(yaw, 0, 1, 0)
        glRotatef(pitch, 1, 0, 0)
        child_len = length * rng.uniform(0.62, 0.80)
        child_rad = tip * rng.uniform(0.82, 0.98)
        _emit_branch(rng, child_len, child_rad, depth + 1, max_depth, bark_tex, leaf_tex)
        glPopMatrix()
    glPopMatrix()


def build_tree_variant(seed, bark_tex, leaf_tex):
    """Compile one tree into a display list. Returns the list id."""
    rng = random.Random(seed)
    trunk_len = rng.uniform(2.8, 4.4)
    trunk_rad = rng.uniform(0.18, 0.32)
    max_depth = rng.choice([4, 5, 5])
    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    _emit_branch(rng, trunk_len, trunk_rad, 0, max_depth, bark_tex, leaf_tex)
    glDisable(GL_ALPHA_TEST)  # leave state clean for the caller
    glEndList()
    return list_id


def build_tree_variants(bark_tex, leaf_tex, n=N_TREE_VARIANTS):
    return [build_tree_variant(101 + i * 37, bark_tex, leaf_tex) for i in range(n)]


def draw_forest(s_car, tree_lists, frost_tree_lists, amb_rgb,
                t_time=0.0, wind_strength=0.0):
    """Instance baked tree lists across positions in forest/frost biome zones.
    Picks the frost (snow-covered) variant set where the slot is in a frost
    zone; forest zones get the normal green variants.

    Wind sway (NVIDIA GPU Gems 3 Ch. 6 / Crytek Chapter 16 approach,
    adapted for fixed-function / baked display lists):

      * `wind_strength` (0-1) drives a small rotation applied to every
        tree before its display list is called. The rotation happens
        around the tree's base (local origin of the display list) so
        the canopy sways while the trunk's base stays planted.
      * Two axes (pitch + roll) at different frequencies and phases so
        the motion doesn't look like a 2D pendulum.
      * A global gust LFO multiplies the amplitude — all trees sync
        subtly to the same gust cycle.
      * Per-tree phase from the same deterministic slot hash so
        neighbouring trees don't move in unison.
    """
    s_start = math.floor(s_car / TREE_SPACING) * TREE_SPACING
    max_s = s_car + N_SEG * SEG_LEN
    n_steps = int((max_s - s_start) / TREE_SPACING) + 1
    nvar = len(tree_lists)

    # Wind terms computed once per frame
    sway_active = wind_strength > 0.02
    if sway_active:
        gust = 0.55 + 0.45 * math.sin(t_time * 0.32)
        amp_deg = wind_strength * gust * 5.0   # max ~3.5° per axis
        omega = 1.2 + wind_strength * 1.8      # sway rate grows with wind
        # "Base lean" — wind pushes trees consistently, oscillation rides
        # on top so trees lean into the wind then wobble around it
        lean_deg = wind_strength * 1.8
    else:
        amp_deg = 0.0
        omega = 0.0
        lean_deg = 0.0

    glEnable(GL_TEXTURE_2D)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glColor3f(min(1.0, amb_rgb[0]),
              min(1.0, amb_rgb[1]),
              min(1.0, amb_rgb[2]))

    for i in range(n_steps):
        s = s_start + i * TREE_SPACING
        if s < s_car - 1.0:
            continue
        for side in (-1, +1):
            w = biome_weights_vec(np.array([s], dtype=np.float32), side)[0]
            fw = float(w[BIOME_FOREST])
            frw = float(w[BIOME_FROST])
            tree_density = fw + frw  # trees grow in both
            if tree_density < 0.28:
                continue
            key = (int(s * 97) * 2654435761
                   + (0 if side < 0 else 7919)
                   + 1013904223) & 0xFFFFFFFF
            if ((key & 0xFFFF) / 65535.0) > tree_density * 0.95:
                continue
            d_edge = 1.2 + ((key >> 16) & 0xFF) / 255.0 * TREE_MAX_PERP
            variant = ((key >> 24) & 0x07) % nvar
            yaw = ((key >> 8) & 0xFF) / 255.0 * 360.0
            scale = 0.78 + ((key >> 4) & 0x0F) / 15.0 * 0.55

            tx = curve_x(s) + side * (ROAD_WIDTH / 2 + d_edge)
            ty = curve_y(s) - 0.15
            tz = -(s - s_car)

            use_frost = frw > fw
            lst = frost_tree_lists[variant] if use_frost else tree_lists[variant]

            glPushMatrix()
            glTranslatef(tx, ty, tz)
            glRotatef(yaw, 0, 1, 0)

            # Wind sway — rotate around the tree's base (local origin)
            if sway_active:
                # Per-tree phase from the same hash, spread across [0, 2π)
                phase = ((key >> 12) & 0x3FFF) * (2.0 * math.pi / 0x3FFF)
                sx = lean_deg + amp_deg * math.sin(t_time * omega + phase)
                sz = amp_deg * 0.6 * math.sin(
                    t_time * omega * 1.25 + phase + 0.7
                )
                glRotatef(sx, 1.0, 0.0, 0.0)
                glRotatef(sz, 0.0, 0.0, 1.0)

            glScalef(scale, scale, scale)
            glCallList(lst)
            glPopMatrix()

    glDisable(GL_ALPHA_TEST)


# --- Snow road shoulders ---
def draw_snow_shoulders(snow_tex, s_car, amb_rgb):
    """Two QUAD_STRIPs hugging the road edges; per-vertex alpha tracks frost
    weight so snow tapers cleanly into biome transitions."""
    NS = N_SEG + 1
    s_arr = np.arange(NS, dtype=np.float32) * SEG_LEN + s_car
    wL = biome_weights_vec(s_arr, -1)[:, BIOME_FROST]
    wR = biome_weights_vec(s_arr, +1)[:, BIOME_FROST]
    if wL.max() < 0.02 and wR.max() < 0.02:
        return

    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, snow_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDepthMask(GL_FALSE)

    for side, warr in ((-1, wL), (+1, wR)):
        glBegin(GL_QUAD_STRIP)
        for i in range(NS):
            s = s_arr[i]
            a = float(min(1.0, warr[i] * 1.3))
            x = curve_x(s); y = curve_y(s); z = -(s - s_car)
            inner = x + side * (ROAD_WIDTH / 2 + 0.02)
            outer = x + side * (ROAD_WIDTH / 2 + SNOW_SHOULDER_W)
            v = s * 0.25
            glColor4f(amb_rgb[0], amb_rgb[1], amb_rgb[2], a)
            glTexCoord2f(0.0, v); glVertex3f(inner, y + 0.05, z)
            glTexCoord2f(0.35, v); glVertex3f(outer, y + 0.05, z)
        glEnd()

    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)


# --- Snowfall particle system ---
# Camera-local box of point-sprite flakes. Each flake falls with gravity + slow
# horizontal swirl; when it leaves the box, it respawns at the top edge with
# a new random X/Z. Rendering is gated and alpha-scaled by the frost weight at
# the camera, so flakes only appear in frost biomes and fade at transitions.
SNOW_N = 700
SNOW_BOX_X = 34.0
SNOW_BOX_Z = 60.0
SNOW_Y_TOP = 22.0
SNOW_Y_BOTTOM = -2.0


def init_snow(seed=77):
    rng = np.random.default_rng(seed)
    pos = np.zeros((SNOW_N, 3), dtype=np.float32)
    pos[:, 0] = rng.uniform(-SNOW_BOX_X, SNOW_BOX_X, SNOW_N)
    pos[:, 1] = rng.uniform(SNOW_Y_BOTTOM, SNOW_Y_TOP, SNOW_N)
    pos[:, 2] = rng.uniform(-SNOW_BOX_Z, SNOW_BOX_Z * 0.2, SNOW_N)
    vel = np.zeros((SNOW_N, 3), dtype=np.float32)
    vel[:, 0] = rng.uniform(-0.35, 0.35, SNOW_N)
    vel[:, 1] = rng.uniform(-5.6, -3.8, SNOW_N)
    vel[:, 2] = rng.uniform(-0.25, 0.25, SNOW_N)
    seeds = rng.uniform(0.0, 6.2831855, SNOW_N).astype(np.float32)
    return pos, vel, seeds


def update_snow(state, dt, t_time):
    pos, vel, seeds = state
    # vertical fall + gentle horizontal swirl
    swirl_x = 0.55 * np.sin(t_time * 1.25 + seeds)
    swirl_z = 0.45 * np.cos(t_time * 0.85 + seeds * 1.3)
    pos[:, 0] += (vel[:, 0] + swirl_x) * dt
    pos[:, 1] += vel[:, 1] * dt
    pos[:, 2] += (vel[:, 2] + swirl_z) * dt

    # respawn when below bottom or outside horizontal box
    below = pos[:, 1] < SNOW_Y_BOTTOM
    outX = np.abs(pos[:, 0]) > SNOW_BOX_X
    outZ = np.abs(pos[:, 2]) > SNOW_BOX_Z
    respawn = below | outX | outZ
    n = int(respawn.sum())
    if n > 0:
        rng = np.random.default_rng()
        pos[respawn, 0] = rng.uniform(-SNOW_BOX_X, SNOW_BOX_X, n).astype(np.float32)
        pos[respawn, 1] = SNOW_Y_TOP
        pos[respawn, 2] = rng.uniform(-SNOW_BOX_Z, SNOW_BOX_Z * 0.3, n).astype(np.float32)


def draw_snow(state, snow_tex, wL, wR, cam_x, cam_y, cam_z):
    """Per-side snowfall. Flakes with local x<0 belong to the left side and
    use the left frost weight for alpha; flakes with x>=0 use the right
    weight. That way if only one side of the road is in frost biome, snow
    falls only on that side and cleanly tapers across transitions."""
    if wL < 0.02 and wR < 0.02:
        return
    pos, _vel, _seed = state

    # per-flake alpha — bumped 20% for visibility, clamped to 1
    alpha = np.where(pos[:, 0] < 0.0, wL, wR).astype(np.float32)
    np.clip(alpha * 1.2, 0.0, 1.0, out=alpha)
    rgba = np.empty((len(pos), 4), dtype=np.float32)
    rgba[:, 0:3] = 1.0
    rgba[:, 3] = alpha

    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, snow_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_POINT_SPRITE)
    glTexEnvi(GL_POINT_SPRITE, GL_COORD_REPLACE, GL_TRUE)
    glPointSize(14.0)
    glPointParameterfv(GL_POINT_DISTANCE_ATTENUATION, (0.0, 0.06, 0.0015))
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDepthMask(GL_FALSE)

    glPushMatrix()
    glTranslatef(cam_x, cam_y, cam_z)
    glEnableClientState(GL_VERTEX_ARRAY)
    glEnableClientState(GL_COLOR_ARRAY)
    glVertexPointer(3, GL_FLOAT, 0, pos)
    glColorPointer(4, GL_FLOAT, 0, rgba)
    glDrawArrays(GL_POINTS, 0, len(pos))
    glDisableClientState(GL_COLOR_ARRAY)
    glDisableClientState(GL_VERTEX_ARRAY)
    glPopMatrix()

    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)
    glDisable(GL_POINT_SPRITE)


# --- Cityscape biome ---
# Approach (drawing on Wonka 2003 / Müller 2006 split-grammar ideas but far
# simpler): each building is a rectangular prism with one tiled "facade"
# texture on its four sides. UVs are baked at variant-compile time so the
# window grid keeps a consistent real-world window size across buildings of
# different sizes. At night, a second additive pass samples a second
# emission texture at the same UVs — only a random subset of windows are
# lit, producing a warm urban skyline glow that fades in with night_factor.
#
# Buildings are placed far from the road (~90-170m perpendicular) so that
# detail imperfections and the seam where terrain ends both disappear into
# atmospheric fog. Placement is deterministic via a per-slot hash, identical
# between the day and emission passes, so lights line up with windows.

CITY_SLOT_SPACING = 8.0      # stride along road for candidate building slots
CITY_MIN_D = 92.0            # min perpendicular distance from road edge
CITY_MAX_D = 175.0
N_BUILDING_VARIANTS = 12

WINDOW_H_M = 1.7             # world-space size of one window
FLOOR_H_M = 3.2              # world-space height of one floor


def city_weight_at(s, side):
    return biome_weights_vec(np.array([s], dtype=np.float32), side)[0, BIOME_CITY]


def make_facade_texture(size=512, cols=8, rows=16, seed=51):
    """Base facade: concrete walls with a regular window grid, windows
    rendered as darker-reflective panels with a lighter frame highlight."""
    rng = np.random.default_rng(seed)
    rgb = np.full((size, size, 3), [62, 63, 72], dtype=np.int16)
    noise = rng.integers(-10, 10, (size, size, 1), dtype=np.int16)
    rgb = np.clip(rgb + noise, 28, 95).astype(np.uint8)

    cw = size / cols
    rh = size / rows
    for c in range(cols):
        for r in range(rows):
            x0 = int(c * cw + cw * 0.18)
            x1 = int(c * cw + cw * 0.82)
            y0 = int(r * rh + rh * 0.20)
            y1 = int(r * rh + rh * 0.78)
            # window pane (darker slightly-blue glass)
            rgb[y0:y1, x0:x1] = (28, 32, 44)
            # sill/lintel highlights
            rgb[y0:y0 + 1, x0:x1] = (112, 112, 120)
            rgb[y1 - 1:y1, x0:x1] = (22, 22, 28)
    return rgb


def make_facade_emission_texture(size=512, cols=8, rows=16, seed=73):
    """Sparse lit windows on fully-transparent background. Same UV layout
    as the base facade so lights land exactly on window panes.
    ~45% of windows lit, each with slight warm-color jitter."""
    rng = np.random.default_rng(seed)
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    cw = size / cols
    rh = size / rows
    for c in range(cols):
        for r in range(rows):
            if rng.random() > 0.45:
                continue
            x0 = int(c * cw + cw * 0.18)
            x1 = int(c * cw + cw * 0.82)
            y0 = int(r * rh + rh * 0.20)
            y1 = int(r * rh + rh * 0.78)
            warm = float(rng.uniform(0.80, 1.0))
            hue_shift = float(rng.uniform(-0.10, 0.15))  # small cool/warm mix
            rr = int(np.clip(255 * warm, 40, 255))
            gg = int(np.clip(225 * warm * (1.0 - hue_shift * 0.4), 40, 255))
            bb = int(np.clip(150 * warm * (1.0 - hue_shift), 20, 255))
            rgba[y0:y1, x0:x1, 0] = rr
            rgba[y0:y1, x0:x1, 1] = gg
            rgba[y0:y1, x0:x1, 2] = bb
            rgba[y0:y1, x0:x1, 3] = 255
    return rgba


def build_building_variant(seed):
    """Compile one building (prism, 4 facade quads) into a display list.

    UV repetition is baked so windows tile consistently with real-world
    dimensions: one window per WINDOW_H_M horizontally, one floor per
    FLOOR_H_M vertically. The base matches the number of window *columns*
    in the facade texture (8) so a complete grid fits every `cols` windows.
    """
    rng = random.Random(seed)
    w = rng.uniform(7.0, 14.0)
    d = rng.uniform(7.0, 14.0)
    h = rng.uniform(22.0, 78.0)

    # one texture tile == 8 windows wide × 16 floors tall; build's UV repeats
    # per world distance chosen so window proportions stay constant.
    u_front = (w / WINDOW_H_M) / 8.0
    u_side = (d / WINDOW_H_M) / 8.0
    v_up = (h / FLOOR_H_M) / 16.0

    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    glBegin(GL_QUADS)
    # -Z face (front) — outward normal -Z
    glNormal3f(0.0, 0.0, -1.0)
    glTexCoord2f(0, 0); glVertex3f(-w / 2, 0, -d / 2)
    glTexCoord2f(u_front, 0); glVertex3f(w / 2, 0, -d / 2)
    glTexCoord2f(u_front, v_up); glVertex3f(w / 2, h, -d / 2)
    glTexCoord2f(0, v_up); glVertex3f(-w / 2, h, -d / 2)
    # +Z face (back) — outward normal +Z
    glNormal3f(0.0, 0.0, 1.0)
    glTexCoord2f(0, 0); glVertex3f(w / 2, 0, d / 2)
    glTexCoord2f(u_front, 0); glVertex3f(-w / 2, 0, d / 2)
    glTexCoord2f(u_front, v_up); glVertex3f(-w / 2, h, d / 2)
    glTexCoord2f(0, v_up); glVertex3f(w / 2, h, d / 2)
    # -X face (left) — outward normal -X
    glNormal3f(-1.0, 0.0, 0.0)
    glTexCoord2f(0, 0); glVertex3f(-w / 2, 0, d / 2)
    glTexCoord2f(u_side, 0); glVertex3f(-w / 2, 0, -d / 2)
    glTexCoord2f(u_side, v_up); glVertex3f(-w / 2, h, -d / 2)
    glTexCoord2f(0, v_up); glVertex3f(-w / 2, h, d / 2)
    # +X face (right) — outward normal +X
    glNormal3f(1.0, 0.0, 0.0)
    glTexCoord2f(0, 0); glVertex3f(w / 2, 0, -d / 2)
    glTexCoord2f(u_side, 0); glVertex3f(w / 2, 0, d / 2)
    glTexCoord2f(u_side, v_up); glVertex3f(w / 2, h, d / 2)
    glTexCoord2f(0, v_up); glVertex3f(w / 2, h, -d / 2)
    # +Y face (roof) — dark flat cap so the building reads as solid from
    # above; UVs point at a single neutral texel so no windows appear.
    glNormal3f(0.0, 1.0, 0.0)
    glTexCoord2f(0.02, 0.02); glVertex3f(-w / 2, h, -d / 2)
    glTexCoord2f(0.03, 0.02); glVertex3f(w / 2, h, -d / 2)
    glTexCoord2f(0.03, 0.03); glVertex3f(w / 2, h, d / 2)
    glTexCoord2f(0.02, 0.03); glVertex3f(-w / 2, h, d / 2)
    glEnd()
    glEndList()
    return list_id, (w, h, d)


def build_building_variants(n=N_BUILDING_VARIANTS):
    return [build_building_variant(701 + i * 41) for i in range(n)]


def _iter_city_slots(s_car):
    """Yield (s, side, key, tx, ty, tz, yaw, variant) for each building slot
    in visible city zones. Used identically by day and emission passes so
    geometry lines up perfectly between them."""
    s_start = math.floor(s_car / CITY_SLOT_SPACING) * CITY_SLOT_SPACING
    max_s = s_car + N_SEG * SEG_LEN
    n_steps = int((max_s - s_start) / CITY_SLOT_SPACING) + 1
    s_arr = np.arange(n_steps, dtype=np.float32) * CITY_SLOT_SPACING + s_start
    wL = biome_weights_vec(s_arr, -1)[:, BIOME_CITY]
    wR = biome_weights_vec(s_arr, +1)[:, BIOME_CITY]
    for i in range(n_steps):
        s = float(s_arr[i])
        if s < s_car - 2.0:
            continue
        for side, weights in ((-1, wL), (+1, wR)):
            w = float(weights[i])
            if w < 0.30:
                continue
            key = (int(s * 53) * 2654435761
                   + (0 if side < 0 else 5099)
                   + 1013904223) & 0xFFFFFFFF
            # density gate: more buildings the deeper you're inside the zone
            if ((key & 0xFFFF) / 65535.0) > w * 0.9:
                continue
            d_off = CITY_MIN_D + ((key >> 16) & 0xFF) / 255.0 * (CITY_MAX_D - CITY_MIN_D)
            variant = ((key >> 24) & 0x0F) % N_BUILDING_VARIANTS
            yaw = ((key >> 4) & 0x3F) / 63.0 * 360.0
            tx = curve_x(s) + side * (ROAD_WIDTH / 2 + d_off)
            ty = curve_y(s) - 0.3
            tz = -(s - s_car)
            yield s, side, key, tx, ty, tz, yaw, variant


def draw_city(s_car, building_lists, facade_tex, emission_tex,
              amb_rgb, night_a):
    """Two-pass render: tinted facade, then additive emission at night."""
    slots = list(_iter_city_slots(s_car))
    if not slots:
        return

    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, facade_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)

    # Enable fixed-function lighting for the facade pass so each face
    # picks up different brightness from the baked normals. Without this
    # all four walls shade identically and buildings look flat/paper-like.
    tint = (min(1.0, amb_rgb[0] * 0.85),
            min(1.0, amb_rgb[1] * 0.85),
            min(1.0, amb_rgb[2] * 0.90))
    glEnable(GL_LIGHTING)
    glEnable(GL_LIGHT0)
    glEnable(GL_COLOR_MATERIAL)
    glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
    # Directional key light (w=0). Fixed upper-right-front direction so
    # face shading stays consistent regardless of camera and time of day —
    # the sun drives scene ambient tint separately.
    glLightfv(GL_LIGHT0, GL_POSITION, (0.6, 1.0, 0.45, 0.0))
    # Ambient ~55% of tint so shadowed sides aren't black; diffuse drives
    # face-to-face contrast.
    glLightfv(GL_LIGHT0, GL_AMBIENT,
              (tint[0] * 0.60, tint[1] * 0.60, tint[2] * 0.62, 1.0))
    glLightfv(GL_LIGHT0, GL_DIFFUSE,
              (tint[0] * 0.70, tint[1] * 0.70, tint[2] * 0.72, 1.0))
    glLightfv(GL_LIGHT0, GL_SPECULAR, (0.0, 0.0, 0.0, 1.0))
    glColor3f(*tint)

    for s, side, key, tx, ty, tz, yaw, variant in slots:
        list_id, _dims = building_lists[variant]
        glPushMatrix()
        glTranslatef(tx, ty, tz)
        glRotatef(yaw, 0, 1, 0)
        glCallList(list_id)
        glPopMatrix()

    glDisable(GL_LIGHTING)
    glDisable(GL_COLOR_MATERIAL)

    if night_a < 0.03:
        return

    # Emission pass: additive blend, warm tint scaled by night factor.
    glBindTexture(GL_TEXTURE_2D, emission_tex)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    glDepthMask(GL_FALSE)
    glColor4f(min(1.0, night_a * 1.1),
              min(1.0, night_a * 1.02),
              min(1.0, night_a * 0.85),
              1.0)

    for s, side, key, tx, ty, tz, yaw, variant in slots:
        list_id, _dims = building_lists[variant]
        glPushMatrix()
        glTranslatef(tx, ty, tz)
        glRotatef(yaw, 0, 1, 0)
        glCallList(list_id)
        glPopMatrix()

    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)


# --- Main ---
def main():
    pygame.init()
    info = pygame.display.Info()
    W, H = info.current_w, info.current_h
    pygame.display.set_mode((W, H), DOUBLEBUF | OPENGL | FULLSCREEN)
    pygame.display.set_caption("Roads")
    pygame.mouse.set_visible(False)

    glEnable(GL_DEPTH_TEST)
    glEnable(GL_LINE_SMOOTH)
    glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)

    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    # Far plane pushed to 1800m so the dome and distant terrain stay inside
    # the frustum without clipping. Near plane kept tight for foreground
    # detail.
    gluPerspective(68.0, W / float(H), 0.1, 1800.0)
    glMatrixMode(GL_MODELVIEW)

    glEnable(GL_FOG)
    # GL_LINEAR fog gives smooth aerial-perspective fade across a specified
    # range. Starts at 180m, fully opaque at 920m — mountains 800m away are
    # barely-visible silhouettes that gradually resolve as you approach,
    # instead of popping at the mesh edge.
    glFogi(GL_FOG_MODE, GL_LINEAR)
    glFogf(GL_FOG_START, 180.0)
    glFogf(GL_FOG_END, 920.0)

    road_tex = upload_texture(make_road_texture())
    terrain_tex = upload_texture(make_terrain_texture())
    cloud_tex = upload_texture(make_cloud_texture(), internal=GL_RGBA, src=GL_RGBA)
    # No mipmaps on the stars texture — mipmap averaging blurs the
    # single-pixel star peaks into soft patches at distance.
    stars_tex = upload_texture(make_stars_texture(), mipmaps=False)
    bark_rgb = load_texture_file("textures/Bark001_1K-JPG_Color.jpg")
    bark_tex = upload_texture(bark_rgb)
    snow_bark_tex = upload_texture(make_snow_bark_texture(bark_rgb))
    leaf_tex = upload_texture(make_leaf_texture(), internal=GL_RGBA, src=GL_RGBA)
    snow_leaf_tex = upload_texture(make_snow_leaf_texture(), internal=GL_RGBA, src=GL_RGBA)
    tree_lists = build_tree_variants(bark_tex, leaf_tex)
    frost_tree_lists = build_tree_variants(snow_bark_tex, snow_leaf_tex)
    snow_ground_tex = upload_texture(load_texture_file("textures/Snow001_1K-JPG_Color.jpg"))
    snowflake_tex = upload_texture(make_snowflake_texture(), internal=GL_RGBA, src=GL_RGBA)
    snow_state = init_snow()
    facade_tex = upload_texture(make_facade_texture())
    emission_tex = upload_texture(make_facade_emission_texture(), internal=GL_RGBA, src=GL_RGBA)
    building_lists = build_building_variants()
    concrete_tex = upload_texture(make_concrete_texture())
    rain_state = init_rain()
    pond_tex = upload_texture(make_pond_texture(), internal=GL_RGBA, src=GL_RGBA)
    flare_tex = upload_texture(make_flare_disc_texture(), internal=GL_RGBA, src=GL_RGBA)
    flare_smoothed = 0.0

    # Ambient audio mixer: brown-noise engine rumble + rain + wind +
    # thunder one-shots. Disabled silently if the platform doesn't have a
    # usable audio output (e.g. headless runs).
    audio_player = None
    if _AUDIO_AVAILABLE:
        try:
            audio_player = AmbientAudioMixer()
            audio_player.start()
        except Exception as exc:
            _print_help_banner(
                "Ambient audio failed to start",
                [
                    f"Error: {exc}",
                    "",
                    "Check that an audio output device is available.",
                    "On macOS you may need to grant microphone / audio",
                    "permission the first time.",
                    "",
                    "The simulation will run without ambient audio.",
                ],
            )
            audio_player = None

    # Procedural minimalist ensemble over the ambient bed. Needs
    # fluidsynth (system library) + pyfluidsynth + the CC0 SoundFont.
    # Skipped gracefully with a friendly message if any part is missing.
    piano_player = None
    if _FLUIDSYNTH_AVAILABLE and os.path.exists(SOUNDFONT_PATH):
        try:
            piano_player = MinimalEnsemblePlayer()
            piano_player.start()
        except Exception as exc:
            _print_help_banner(
                "Procedural ensemble failed to start",
                [
                    f"Error: {exc}",
                    "",
                    "Check that fluidsynth is installed and that the",
                    f"SoundFont at {SOUNDFONT_PATH}",
                    "is readable. Re-run setup_soundfonts.py if unsure.",
                    "",
                    "The simulation will run without the piano ensemble.",
                ],
            )
            piano_player = None
    elif _FLUIDSYNTH_AVAILABLE and not os.path.exists(SOUNDFONT_PATH):
        _print_help_banner(
            "SoundFont missing — procedural ensemble disabled",
            [
                f"Expected file: {SOUNDFONT_PATH}",
                "",
                "Download the CC0 GeneralUser GS SoundFont with:",
                "",
                f"  {sys.executable} setup_soundfonts.py",
                "",
                "This fetches a ~32 MB .sf2 from GitHub and places it",
                "under ./soundfonts/. The simulation will run without",
                "the piano ensemble until it's in place.",
            ],
        )
    bolt_rng = np.random.default_rng(613)
    active_bolt = None
    bolt_age = 0.0
    time_to_strike = 3.0
    # Seed the EMA with the starting storm value so we don't ramp in from 0
    storm_smoothed = storm_intensity_at(0.0)
    sv, stc, sfr, sidx = build_sky_dome()
    sky_state = (sv, stc, sfr, sidx, cloud_tex, stars_tex)

    clock = pygame.time.Clock()
    s_car = 0.0
    t_time = 0.0
    # start near dawn so first sight is pretty
    t_day = 0.18
    speed = SPEED
    running = True
    while running:
        dt = clock.tick(60) / 1000.0
        for e in pygame.event.get():
            if e.type == QUIT:
                running = False
            elif e.type == KEYDOWN and e.key == K_ESCAPE:
                running = False

        # Up/Down: live accel/decel. Held-key polling so the speed change is
        # smooth and proportional to how long the key is held.
        keys = pygame.key.get_pressed()
        if keys[K_UP]:
            speed += SPEED_ACCEL * dt
        if keys[K_DOWN]:
            speed -= SPEED_ACCEL * dt
        if speed < SPEED_MIN:
            speed = SPEED_MIN
        elif speed > SPEED_MAX:
            speed = SPEED_MAX

        s_car += speed * dt
        t_time += dt
        t_day = (t_day + dt / DAY_PERIOD) % 1.0

        # Audio mix: brown noise speed tracks camera speed; rain and wind
        # volumes track the weather/biome state. Set once per frame —
        # the callback smooths the targets internally.
        if audio_player is not None:
            audio_player.set_speed(0.25 + (speed / SPEED) * 0.75)

        # weather tick: storm intensity and lightning lifecycle.
        # EMA on the raw storm signal so intensity climbs and falls over
        # ~30 seconds rather than whatever the sine product happens to do —
        # looks like weather building gradually, not flicking on and off.
        storm_raw = storm_intensity_at(t_time)
        storm_tau = 60.0   # weather blends over ~1 min
        storm_smoothed += (storm_raw - storm_smoothed) * min(1.0, dt / storm_tau)
        storm_i = storm_smoothed
        if active_bolt is not None:
            bolt_age += dt
            if bolt_age > BOLT_LIFE:
                active_bolt = None
                bolt_age = 0.0

        flash = 0.0
        if active_bolt is not None:
            # Same ~35 Hz flicker as the bolt itself — sky and bolt strobe
            # together, reading as multiple return strokes.
            life_t = 1.0 - bolt_age / BOLT_LIFE
            fl = 0.55 + 0.45 * math.sin(bolt_age * 2 * math.pi * 35.0)
            flash = max(0.0, (life_t ** 0.6) * fl)

        # day/night + weather derived values
        zenith, horizon = sky_colors_at(t_day, storm_i, flash)
        bright, tint = ambient_at(t_day, storm_i, flash)
        amb = (tint[0] * bright, tint[1] * bright, tint[2] * bright)
        night_a = night_factor_at(t_day)
        sun_d = sun_dir_at(t_day)
        moon_d = -sun_d  # opposite

        # fog fades into the horizon color of the moment
        fog_c = (horizon[0] * bright * 0.9 + 0.05,
                 horizon[1] * bright * 0.9 + 0.05,
                 horizon[2] * bright * 0.9 + 0.05, 1.0)
        glFogfv(GL_FOG_COLOR, fog_c)
        # Gradually thicken fog up to +10% when the camera is in frost biome.
        # frost_intensity_at returns the smoothstep-blended biome weight so
        # density eases in/out at zone transitions rather than snapping.
        frost_i = frost_intensity_at(s_car)
        # Frost biome and heavy storm both reduce visibility: shrink the
        # fog-end distance so the linear fog ramp terminates sooner.
        vis_end = 920.0 / (1.0 + 0.10 * frost_i + 0.30 * storm_i)
        glFogf(GL_FOG_END, vis_end)
        glClearColor(horizon[0], horizon[1], horizon[2], 1.0)

        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()

        s_cam = s_car - CAM_BACK
        cx = curve_x(s_cam)
        cy = curve_y(s_cam) + CAM_HEIGHT
        cz = CAM_BACK
        s_look = s_car + LOOK_AHEAD
        lx = curve_x(s_look)
        ly = curve_y(s_look) + 0.8
        lz = -(s_look - s_car)
        gluLookAt(cx, cy, cz, lx, ly, lz, 0.0, 1.0, 0.0)

        # Try to trigger a new bolt now that we have the camera position.
        # Strikes are deliberately rare: poll once per second, low success
        # probability even at peak storm, and a mandatory gap of several
        # seconds between consecutive strikes so they feel like events.
        time_to_strike -= dt
        if active_bolt is None and time_to_strike <= 0.0:
            if storm_i > 0.45 and bolt_rng.random() < storm_i * 0.12:
                active_bolt = generate_bolt(bolt_rng, cx, cy, cz)
                bolt_age = 0.0
                time_to_strike = float(bolt_rng.uniform(5.0, 12.0))
                # Thunder clap: louder when the storm is heavier, with a
                # little random variation per strike so they don't sound
                # identical.
                if audio_player is not None:
                    audio_player.trigger_thunder(
                        volume=0.40 + storm_i * 0.40
                               + float(bolt_rng.uniform(-0.05, 0.15)),
                    )
            else:
                time_to_strike = 1.0

        # Weather-driven ambient mix. Volumes here are the *targets* —
        # the mixer's callback low-passes them with a ~2.5 s time
        # constant so rain and wind fade in and out gradually rather
        # than snapping. Levels are ~50% of the previous scale so the
        # weather sits under the music, not on top of it.
        if audio_player is not None:
            rain_vol = storm_i * (1.0 - frost_i) * 0.17
            wL_now = biome_weights_vec(
                np.array([s_car], dtype=np.float32), -1)[0]
            wR_now = biome_weights_vec(
                np.array([s_car], dtype=np.float32), +1)[0]
            open_exp = 0.5 * (
                wL_now[BIOME_PLAIN] + wL_now[BIOME_MOUNTAIN] + wL_now[BIOME_FROST]
                + wR_now[BIOME_PLAIN] + wR_now[BIOME_MOUNTAIN] + wR_now[BIOME_FROST]
            )
            speed_ratio = speed / SPEED
            wind_vol = (0.015
                        + speed_ratio * 0.025
                        + open_exp * 0.040
                        + storm_i * 0.035)
            audio_player.set_volumes(rain=rain_vol, wind=wind_vol)

        draw_sky(sky_state, cx, cy, cz, t_time, t_day, storm_i, flash)

        # sun + moon sit on top of the sky pass but behind terrain
        sc = sun_color(t_day)
        draw_celestial(cx, cy, cz, sun_d, radius=14.0,
                       core_color=sc,
                       glow_color=(sc[0], sc[1] * 0.8, sc[2] * 0.6))
        # moon — slightly smaller, cool silver. Fades out when sun is up.
        moon_alpha = _smooth((0.3 - bright) / 0.3)
        if moon_alpha > 0.02 and moon_d[1] > -0.25:
            draw_celestial(cx, cy, cz, moon_d, radius=9.0,
                           core_color=(0.92, 0.94, 1.0),
                           glow_color=(0.55, 0.65, 0.85),
                           core_alpha=moon_alpha)

        draw_terrain(terrain_tex, snow_ground_tex, s_car, t_time, amb)
        draw_city(s_car, building_lists, facade_tex, emission_tex, amb, night_a)
        # Wind strength drives tree sway. Built from the same ingredients as
        # wind audio: storm intensity dominates, open biomes add exposure,
        # camera speed a touch. Clamped so peak sway stays around 3-4°.
        wL_c = biome_weights_vec(
            np.array([s_car], dtype=np.float32), -1)[0]
        wR_c = biome_weights_vec(
            np.array([s_car], dtype=np.float32), +1)[0]
        open_exp_trees = 0.5 * (
            wL_c[BIOME_PLAIN] + wL_c[BIOME_MOUNTAIN] + wL_c[BIOME_FROST]
            + wR_c[BIOME_PLAIN] + wR_c[BIOME_MOUNTAIN] + wR_c[BIOME_FROST]
        )
        wind_strength = min(
            0.8,
            0.10 + 0.30 * storm_i + 0.18 * open_exp_trees
                 + 0.05 * (speed / SPEED),
        )
        draw_forest(s_car, tree_lists, frost_tree_lists, amb,
                    t_time, wind_strength)
        draw_ponds(pond_tex, s_car, storm_i, horizon, amb, t_time)
        draw_road(road_tex, s_car, amb)
        # Bridges + tunnels: placed where biomes dictate (river / mountain)
        # so they never collide with trees, buildings, or other structures.
        # Concrete tint reacts to storm (wet darkening) and frost (snow cap).
        draw_civil_structures(s_car, concrete_tex, amb, storm_i, frost_i)
        draw_snow_shoulders(snow_ground_tex, s_car, amb)
        draw_lamps(s_car, night_a)

        # snowfall: per-side gating. Flakes only fall where that side of
        # the road is in a frost biome; if left is frost and right isn't,
        # snow falls only to the left.
        wL_frost = frost_weight_at(s_car, -1)
        wR_frost = frost_weight_at(s_car, +1)
        update_snow(snow_state, dt, t_time)
        draw_snow(snow_state, snowflake_tex, wL_frost, wR_frost, cx, cy, cz)

        # rain: storm active AND not in a frost biome (otherwise it's snow
        # falling from the sibling snow system — don't draw both at once).
        update_rain(rain_state, dt)
        rain_i = storm_i * (1.0 - frost_i)
        draw_rain(rain_state, rain_i, cx, cy, cz)

        # lightning bolt (if one is currently active)
        draw_bolt(active_bolt, bolt_age)

        # Lens flare: last pass so it overlays everything like real lens
        # optics. Intensity = sun altitude × view alignment × (1 - storm),
        # smoothed with a 0.5 s EMA so the flare fades in and out cleanly
        # instead of flickering as the road sways across the sun direction.
        sun_alt_factor = _smooth((sun_d[1] - 0.18) / 0.40)
        vdx = lx - cx; vdy = ly - cy; vdz = lz - cz
        vL = math.sqrt(vdx * vdx + vdy * vdy + vdz * vdz) + 1e-9
        vdx /= vL; vdy /= vL; vdz /= vL
        align = (vdx * float(sun_d[0])
                 + vdy * float(sun_d[1])
                 + vdz * float(sun_d[2]))
        align_factor = _smooth((align - 0.72) / 0.28)
        storm_damp = max(0.0, 1.0 - 0.8 * storm_i)
        flare_target = sun_alt_factor * align_factor * storm_damp * 0.55
        flare_smoothed += (flare_target - flare_smoothed) * min(1.0, dt / 0.5)
        draw_lens_flare(flare_tex, sun_d, cx, cy, cz, lx, ly, lz,
                        W, H, flare_smoothed)

        pygame.display.flip()

    if piano_player is not None:
        piano_player.stop()
    if audio_player is not None:
        audio_player.stop()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
