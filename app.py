import math
import sys
import numpy as np
import pygame
from pygame.locals import (
    DOUBLEBUF, OPENGL, FULLSCREEN, QUIT, KEYDOWN, K_ESCAPE,
    K_UP, K_DOWN, K_LEFT, K_RIGHT, K_SPACE, K_t,
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
# at module import time. That's the Chocolatey install path, which the
# binding hard-codes even if the user installed fluidsynth by another
# means (MSYS2, the official Windows installer, manual DLL drop). If the
# hard-coded directory doesn't exist the import raises FileNotFoundError
# (WinError 3) before our try/except can even engage, crashing the app.
#
# Fix: pre-create that directory (empty is fine) so the import always
# succeeds, AND proactively add every common fluidsynth install location
# to the DLL search path so the actual library can be found regardless
# of which installer the user used.
if sys.platform == "win32":
    _WIN_FS_DLL_LOCATIONS = [
        r"C:\tools\fluidsynth\bin",                      # Chocolatey
        r"C:\Program Files\fluidsynth\bin",              # official installer
        r"C:\Program Files (x86)\fluidsynth\bin",
        r"C:\msys64\mingw64\bin",                        # MSYS2 mingw64
        r"C:\msys64\ucrt64\bin",                         # MSYS2 ucrt64
        os.path.expanduser(r"~\scoop\apps\fluidsynth\current\bin"),  # Scoop
    ]
    try:
        os.makedirs(r"C:\tools\fluidsynth\bin", exist_ok=True)
    except Exception:
        pass
    for _dll_dir in _WIN_FS_DLL_LOCATIONS:
        if os.path.isdir(_dll_dir):
            try:
                os.add_dll_directory(_dll_dir)
            except Exception:
                pass


def _fluidsynth_drivers_for_platform():
    """Preference-ordered list of fluidsynth audio driver names for the
    current OS. fluidsynth auto-selects a reasonable default when given
    an empty/unknown name, so we try known-good drivers in order and
    fall through to the library default if none work."""
    if sys.platform == "win32":
        return ("dsound", "wasapi", "waveout", "portaudio", "sdl2")
    if sys.platform == "darwin":
        return ("coreaudio", "portaudio", "sdl2")
    # Linux / BSD / others
    return ("pulseaudio", "alsa", "jack", "portaudio", "sdl2")

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
    gluQuadricNormals, gluCylinder, gluDisk, gluSphere, gluDeleteQuadric,
    gluProject, GLU_SMOOTH,
)


# --- Road ---
ROAD_WIDTH = 10.0
SEG_LEN = 2.2
N_SEG = 400            # visible road/terrain extent: ~880m ahead of camera
SPEED = 28.0           # default cruise speed (m/s); Up/Down adjust live
SPEED_ACCEL = 24.0     # m/s² applied while Up/Down is held
SPEED_MIN = 0.0
SPEED_MAX = 90.0

# Camera yaw: Left/Right rotate the view around the vertical axis while
# the car position and height stay the same. Limited to 180° total
# (±90° each side) so the player can never look backward — the driving
# view stays readable. Space re-centers to looking straight ahead.
CAMERA_YAW_RATE = 70.0   # degrees per second while Left/Right held
CAMERA_YAW_LIMIT = 90.0  # ±90° -> 180° total rotation range
CAM_HEIGHT = 3.4
CAM_BACK = 5.0
LOOK_AHEAD = 20.0
# Road/terrain sheets start this far *behind* s_car so they extend past the
# camera (which sits at s_car - CAM_BACK). Without this, the near rows would
# begin at the camera's s and leave a geometry gap below the view, exposing
# the glClearColor as a horizon-coloured stripe along the bottom of the frame.
NEAR_EXTEND = CAM_BACK + 2.0 * SEG_LEN

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


def is_plain_either_side(s):
    """Street lamps bracket the road, so a plain/urban zone on *either*
    side of the road should get lamps on *both* sides (lamps mirror
    across the carriageway). Previously we gated per-side — if the right
    side of a zone happened to be another biome, lamps only appeared on
    the left. Fixes that."""
    wL = biome_weights_vec(np.array([s], dtype=np.float32), -1)[0, BIOME_PLAIN]
    wR = biome_weights_vec(np.array([s], dtype=np.float32), +1)[0, BIOME_PLAIN]
    return max(float(wL), float(wR)) > 0.55


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
    # --- Storm-weather dome ---
    # Clear-sky Preetham-style: zenith far brighter & bluer than horizon.
    # Overcast (CIE Moon & Spencer 1942 / Preetham high-turbidity):
    #   * Luminance ratio collapses — zenith only ~1.15× horizon, not 10×
    #   * Rayleigh blue fades (Nishita 1996 multi-scatter): multi-bounce
    #     off water droplets desaturates sharply toward neutral gray with
    #     a faint green cast
    #   * Under heavy storm horizon can actually EXCEED zenith (the
    #     dome-light effect) — a narrow band of diffused sun still leaks
    #     around the overcast deck's edge
    if storm > 0.001:
        # Target overcast colours. Green-gray (matches bruised
        # stratocumulus), with horizon slightly brighter than zenith so
        # the gradient FLATTENS rather than reverses — the signature
        # "flat gray dome" look of a rainy afternoon.
        sz = (0.22, 0.23, 0.22)   # overcast zenith — gray-green
        sh = (0.28, 0.29, 0.28)   # overcast horizon — a touch brighter
        # Smoothstep so gradient reshaping kicks in strongly beyond ~0.3.
        s = storm * storm * (3 - 2 * storm) * 0.92
        zen = _lerp3(zen, sz, s)
        horz = _lerp3(horz, sh, s)
        # Extra desaturation pull: blend toward the average of zenith &
        # horizon luminance so colour contrast flattens further.
        if storm > 0.4:
            avg = ((zen[0] + horz[0]) * 0.5,
                   (zen[1] + horz[1]) * 0.5,
                   (zen[2] + horz[2]) * 0.5)
            k = (storm - 0.4) / 0.6 * 0.35
            zen = _lerp3(zen, avg, k)
            horz = _lerp3(horz, avg, k)
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
        # Storm cloud tint: darker, greener-gray than fair-weather tint,
        # following the multi-scatter aerosol story (less Rayleigh blue).
        # The same direction applied to `make_overcast_texture` so the
        # two cloud layers read as the same material under the same
        # illumination.
        dark = (0.26, 0.28, 0.27)
        r = r * (1 - storm) + dark[0] * storm
        g = g * (1 - storm) + dark[1] * storm
        b = b * (1 - storm) + dark[2] * storm
        # At very high storm intensity, pull the whole tint toward its
        # mean (desaturate further) so the cloud mass reads as grayer.
        if storm > 0.35:
            k = (storm - 0.35) / 0.65 * 0.40
            m = (r + g + b) / 3.0
            r = r * (1 - k) + m * k
            g = g * (1 - k) + m * k
            b = b * (1 - k) + m * k
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

        # Clear device settings that some builds seed with the literal
        # "default" (a string that isn't a real device on Windows, giving
        # the 'Device "default" does not exist' error). An empty value
        # tells the platform driver to use the OS's actual default.
        for _setting_key in (
            "audio.dsound.device", "audio.wasapi.device",
            "audio.waveout.device", "audio.coreaudio.device",
            "audio.pulseaudio.device", "audio.alsa.device",
        ):
            try:
                self.fs.setting(_setting_key, "")
            except Exception:
                pass
        # We never read MIDI input (we inject notes via API). Tell
        # fluidsynth not to try — avoids the "not enough MIDI in devices"
        # error on systems with no MIDI hardware.
        for _setting_key in ("midi.autoconnect",):
            try:
                self.fs.setting(_setting_key, 0)
            except Exception:
                pass

        # Try platform-appropriate audio drivers in order until one works.
        _started = False
        _last_err = None
        for _drv in _fluidsynth_drivers_for_platform():
            try:
                self.fs.start(driver=_drv)
                _started = True
                break
            except Exception as _err:
                _last_err = _err
                continue
        if not _started:
            # Absolute fallback: let fluidsynth pick its compiled default
            try:
                self.fs.start()
            except Exception as _err:
                raise RuntimeError(
                    f"fluidsynth could not open any audio driver. "
                    f"Last error: {_last_err or _err}"
                )

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


# --- Lens weather overlay: droplets + snowflakes on the "camera lens" ---
# Screen-space post-process overlay — a pool of alpha-blended sprites
# drawn in a 2D ortho layer *after* all 3D rendering, so they sit on the
# framebuffer as if stuck to the lens. Only active during rain or snow,
# and they're deliberately partially transparent and sparse — realism
# comes from seeing *through* them, not from them dominating the view.
#
# Physics model (simplified from Tatarchuk 2006 "Rain Rendering" + the
# Unity / Unreal "raindrop FX" body of work):
#   * Each drop has a random size, position, and life.
#   * Rain drops slide downward over their life (surface-tension pull
#     plus oblate-spheroid streaking), snowflakes drift slightly and
#     mostly sit.
#   * Alpha ramps up quickly at birth, plateaus, then fades out — so
#     drops don't pop in or out.
#   * Population is capped, spawn rate is modulated by weather intensity
#     so the effect tracks the storm envelope cleanly.

LENS_MAX_DROPS = 34
LENS_RAIN_SPAWN_RATE = 10.0        # drops/sec at full rain
LENS_SNOW_SPAWN_RATE = 5.5         # flakes/sec at full frost


def make_lens_drop_texture(size=128):
    """Soft water-drop blob with a refraction highlight. Alpha is
    *forced to zero outside the unit circle* (np.where mask), so the
    square quad's corners are guaranteed fully transparent — this is
    what makes the drop read as round rather than square regardless of
    the sprite's aspect ratio."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    d = np.sqrt((xs - c) ** 2 + (ys - c) ** 2) / c

    # Hard circular mask: alpha clipped to 0 past the unit circle and
    # softly ramping down to zero inside (computed on clipped values so
    # the power never sees a negative).
    inside_factor = np.clip(1.0 - d, 0.0, 1.0)
    alpha_shape = inside_factor ** 0.9

    # Specular highlight (refraction scatter) in the upper-left
    hx = c - size * 0.24
    hy = c - size * 0.24
    hd = np.sqrt((xs - hx) ** 2 + (ys - hy) ** 2) / (size * 0.18)
    highlight = np.clip(1.0 - hd, 0.0, 1.0) ** 2.5

    rgba = np.zeros((size, size, 4), dtype=np.float32)
    rgba[..., 0] = 0.60 + 0.35 * highlight
    rgba[..., 1] = 0.70 + 0.28 * highlight
    rgba[..., 2] = 0.85 + 0.14 * highlight
    # Translucent, so the background still shows through — real water
    # on glass doesn't fully occlude.
    rgba[..., 3] = alpha_shape * 0.62
    return (rgba * 255).clip(0, 255).astype(np.uint8)


def make_lens_flake_texture(size=128):
    """White snowflake: radial disc + six spokes (hexagonal symmetry).
    Alpha zeroed outside the unit circle so the sprite is truly round."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    dx = xs - c
    dy = ys - c
    r = np.sqrt(dx * dx + dy * dy) / c

    inside_factor = np.clip(1.0 - r, 0.0, 1.0)
    disc = inside_factor ** 1.3
    # Spokes: minimum distance to the nearest of 6 axis angles
    angle = np.arctan2(dy, dx)
    mod = np.mod(angle, np.pi / 3.0)
    spoke_dist = np.minimum(mod, np.pi / 3.0 - mod)
    spoke = np.exp(-(spoke_dist * 11.0) ** 2)
    spoke *= inside_factor

    total = np.maximum(disc * 0.55, spoke * 0.95)
    rgba = np.zeros((size, size, 4), dtype=np.float32)
    rgba[..., 0] = 0.96
    rgba[..., 1] = 0.98
    rgba[..., 2] = 1.00
    rgba[..., 3] = total * 0.85
    return (rgba * 255).clip(0, 255).astype(np.uint8)


class LensWeatherOverlay:
    """Fixed-pool of droplets/flakes with life management. Update happens
    once per frame with current rain/snow intensity and the viewport size;
    draw is a single 2D ortho pass called after everything else."""

    def __init__(self):
        self.drops = []
        self.rng = np.random.default_rng(911)

    def _spawn(self, kind, W, H):
        if kind == "rain":
            return {
                "kind": "rain",
                "x": float(self.rng.uniform(W * 0.05, W * 0.95)),
                "y": float(self.rng.uniform(H * 0.28, H * 0.92)),
                "vx": float(self.rng.uniform(-3.0, 3.0)),
                "vy": float(self.rng.uniform(8.0, 28.0)),  # slide down
                "size": float(self.rng.uniform(14.0, 30.0)),
                "age": 0.0,
                "life": float(self.rng.uniform(3.5, 6.5)),
            }
        return {
            "kind": "snow",
            "x": float(self.rng.uniform(W * 0.05, W * 0.95)),
            "y": float(self.rng.uniform(H * 0.20, H * 0.90)),
            "vx": float(self.rng.uniform(-1.5, 1.5)),
            "vy": float(self.rng.uniform(1.5, 5.0)),
            "size": float(self.rng.uniform(16.0, 32.0)),
            "age": 0.0,
            "life": float(self.rng.uniform(5.5, 9.5)),
        }

    def update(self, dt, rain_i, snow_i, W, H):
        # Spawn new drops/flakes, capped at pool size
        if rain_i > 0.04:
            expected = rain_i * LENS_RAIN_SPAWN_RATE * dt
            n = int(self.rng.poisson(max(0.0, expected)))
            for _ in range(n):
                if len(self.drops) >= LENS_MAX_DROPS:
                    break
                self.drops.append(self._spawn("rain", W, H))
        if snow_i > 0.04:
            expected = snow_i * LENS_SNOW_SPAWN_RATE * dt
            n = int(self.rng.poisson(max(0.0, expected)))
            for _ in range(n):
                if len(self.drops) >= LENS_MAX_DROPS:
                    break
                self.drops.append(self._spawn("snow", W, H))

        # Advance and cull
        alive = []
        for d in self.drops:
            d["age"] += dt
            if d["age"] >= d["life"]:
                continue
            # ortho maps y=0 to bottom, y=H to top. vy is positive when we
            # want screen-down motion, so subtract it from y.
            d["x"] += d["vx"] * dt
            d["y"] -= d["vy"] * dt
            if d["y"] < -40.0:
                continue  # slid off the bottom of the lens
            alive.append(d)
        self.drops = alive

    def draw(self, drop_tex, flake_tex, W, H):
        if not self.drops:
            return
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
        # Force alpha test off — any earlier pass that left it enabled
        # (trees, flowers with their cutout state) would otherwise make
        # these sprites hard-edged and look square.
        glDisable(GL_ALPHA_TEST)
        glEnable(GL_TEXTURE_2D)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDepthMask(GL_FALSE)

        last_tex = None
        for d in self.drops:
            # Alpha envelope — quick attack, plateau, longer release
            age_frac = d["age"] / d["life"]
            if age_frac < 0.12:
                a = age_frac / 0.12
            elif age_frac < 0.72:
                a = 1.0
            else:
                a = max(0.0, (1.0 - age_frac) / 0.28)
            tex = drop_tex if d["kind"] == "rain" else flake_tex
            if tex != last_tex:
                glBindTexture(GL_TEXTURE_2D, tex)
                last_tex = tex
            glColor4f(1.0, 1.0, 1.0, a * 0.75)
            x, y, s = d["x"], d["y"], d["size"]
            glBegin(GL_QUADS)
            glTexCoord2f(0.0, 0.0); glVertex2f(x - s / 2, y - s / 2)
            glTexCoord2f(1.0, 0.0); glVertex2f(x + s / 2, y - s / 2)
            glTexCoord2f(1.0, 1.0); glVertex2f(x + s / 2, y + s / 2)
            glTexCoord2f(0.0, 1.0); glVertex2f(x - s / 2, y + s / 2)
            glEnd()

        glDepthMask(GL_TRUE)
        glDisable(GL_BLEND)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_FOG)

        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()


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
            # Ellipse triangle fan. UVs span the *full* [0, 1] radially
            # so the pond texture's edge-alpha (zero past r=1) kills
            # every ring vertex to full transparency — this is what
            # hides the 28-gon outline and makes the puddle read as a
            # smooth natural shape instead of an obvious polygon.
            glBegin(GL_TRIANGLE_FAN)
            glNormal3f(0.0, 1.0, 0.0)
            glTexCoord2f(0.5, 0.5); glVertex3f(0.0, 0.0, 0.0)
            steps = 28
            for k in range(steps + 1):
                t = 2 * math.pi * k / steps
                ctx = math.cos(t)
                ctz = math.sin(t)
                x = ctx * radius
                z = ctz * radius * aspect
                u = 0.5 + 0.5 * ctx   # full [0, 1] range across ellipse
                v = 0.5 + 0.5 * ctz
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
    """Classic storm bolt via recursive midpoint displacement (Reed &
    Wyvill 1986). Horizontal displacement decays geometrically per
    subdivision so the trunk stays vertical with jagged zigzag layered
    on top. Life ~0.22 s, triggered by storm events or the T key.
    Separate from generate_ca_charge (see below), which is the CA-based
    1–4 frame electric-charge flash."""
    angle = float(rng.uniform(0, 2 * math.pi))
    dist = float(rng.uniform(80.0, 200.0))
    end = np.array([cam_x + math.cos(angle) * dist,
                    cam_y - 4.0,
                    cam_z + math.sin(angle) * dist], dtype=np.float32)
    start = np.array([end[0] + rng.uniform(-12, 12),
                      cam_y + 130.0,
                      end[2] + rng.uniform(-12, 12)], dtype=np.float32)
    pts = [start, end]
    for it in range(7):
        new_pts = [pts[0]]
        for i in range(len(pts) - 1):
            a = pts[i]; b = pts[i + 1]
            mid = (a + b) * 0.5
            seg_len = float(np.linalg.norm(b - a))
            dh = seg_len * 0.22 * (0.55 ** it)
            dv = seg_len * 0.05 * (0.55 ** it)
            disp = np.array([rng.uniform(-1, 1) * dh,
                             rng.uniform(-1, 1) * dv,
                             rng.uniform(-1, 1) * dh], dtype=np.float32)
            new_pts.append(mid + disp)
            new_pts.append(b)
        pts = new_pts
    main_line = np.stack(pts).astype(np.float32)

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


def generate_ca_charge(rng, cam_x, cam_y, cam_z, eta=1.6):
    """Cellular-automaton *electric charge* — a separate, very brief
    flash (1–4 frames) that complements the main bolt. Grown via a
    simplified Dielectric Breakdown Model (Niemeyer, Pietronero &
    Weismann 1984; Kim & Lin 2007 for the full Laplace-solve
    extension, which we skip for speed). Each iteration adds exactly
    one cell to a voxel grid; the next cell is chosen from boundary
    candidates with probability proportional to potential^η, where
    potential is approximated by grid depth toward ground. η>1 gives
    the sparse, dendritic structure characteristic of real lightning.
    """
    angle = float(rng.uniform(0, 2 * math.pi))
    dist = float(rng.uniform(80.0, 200.0))
    ground_x = cam_x + math.cos(angle) * dist
    ground_z = cam_z + math.sin(angle) * dist
    ground_y = cam_y - 4.0
    cloud_y = cam_y + 130.0

    GW_HALF = 9          # horizontal half-width in cells → grid 19 × 19
    GH = 26              # vertical cells
    X_PER_CELL = 2.2     # metres per horizontal cell
    y_per_cell = (cloud_y - ground_y) / GH

    def cell_to_world(ix, iy, iz):
        wx = ground_x + (ix - GW_HALF) * X_PER_CELL
        wz = ground_z + (iz - GW_HALF) * X_PER_CELL
        wy = cloud_y - iy * y_per_cell
        return (wx, wy, wz)

    # CA seed at top center, with a small cloud-wander offset baked in
    seed = (GW_HALF, 0, GW_HALF)
    filled = {seed: None}  # cell -> parent cell
    max_size_x = 2 * GW_HALF
    max_size_z = 2 * GW_HALF
    target_y = GH - 1
    max_cells = GH * 2 + 12

    # Growth loop — each iteration adds exactly one cell to `filled`
    while len(filled) < max_cells:
        boundary = {}  # cell -> (weight, parent)
        for fcell in filled:
            cx, cy, cz = fcell
            # 18 candidate neighbours: 3×2×3 Moore neighbourhood with
            # dy ∈ {0, 1} (no upward growth — bolts move toward ground)
            for dx in (-1, 0, 1):
                for dy in (0, 1):
                    for dz in (-1, 0, 1):
                        if dx == 0 and dy == 0 and dz == 0:
                            continue
                        nx, ny, nz = cx + dx, cy + dy, cz + dz
                        if not (0 <= nx <= max_size_x
                                and 0 <= ny <= target_y
                                and 0 <= nz <= max_size_z):
                            continue
                        ncell = (nx, ny, nz)
                        if ncell in filled:
                            continue
                        # DBM weight: potential^η, with potential
                        # approximated by depth (0..1 along y)
                        potential = (ny + 1) / (GH + 1)
                        w = potential ** eta
                        if dy == 1:      # vertical preference
                            w *= 1.9
                        # Keep the strongest parent claim on this cell
                        prev = boundary.get(ncell)
                        if prev is None or w > prev[0]:
                            boundary[ncell] = (w, fcell)

        if not boundary:
            break

        # Weighted random choice
        cells_list = list(boundary.items())
        weights = [entry[1][0] for entry in cells_list]
        total_w = sum(weights)
        if total_w <= 0.0:
            break
        r = float(rng.random()) * total_w
        acc = 0.0
        new_cell = cells_list[-1][0]
        parent = cells_list[-1][1][1]
        for ncell, (w, par) in cells_list:
            acc += w
            if r <= acc:
                new_cell = ncell
                parent = par
                break
        filled[new_cell] = parent

        # Stop early once we've made the ground AND produced some
        # branching — anything more is just embellishment.
        if new_cell[1] >= target_y and len(filled) > GH * 1.2:
            break

    # Tip = deepest cell reached
    tip = max(filled.keys(), key=lambda c: c[1])

    # Main channel: trace tip → seed via parent pointers
    main_pts = []
    cur = tip
    while cur is not None:
        main_pts.append(cell_to_world(*cur))
        cur = filled[cur]
    main_pts.reverse()
    main_line = np.array(main_pts, dtype=np.float32)

    # Children map for fork discovery
    children = {}
    for c, p in filled.items():
        if p is not None:
            children.setdefault(p, []).append(c)

    # Main path as a set so we can exclude it when hunting forks
    main_set = set()
    cur = tip
    while cur is not None:
        main_set.add(cur)
        cur = filled[cur]

    # Forks: any cell on the main path that has ≥ 2 children spawns a
    # fork tracing through the child that *isn't* on the main path.
    forks = []
    for c in main_set:
        ch_list = children.get(c, [])
        if len(ch_list) < 2:
            continue
        for child in ch_list:
            if child in main_set:
                continue
            fork_pts = [cell_to_world(*c)]
            cur = child
            for _ in range(12):
                fork_pts.append(cell_to_world(*cur))
                nxt = children.get(cur)
                if not nxt:
                    break
                cur = nxt[0]
            if len(fork_pts) >= 3:
                forks.append(np.array(fork_pts, dtype=np.float32))
            break
        if len(forks) >= 3:
            break

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


def draw_ca_charge(bolt):
    """CA electric-charge flash. Life is measured in frames (1–4), not
    seconds — every frame it's alive it flashes at full brightness.
    The glow is wider and core slightly thicker than the normal bolt
    so the flash reads instantly despite its very short lifetime."""
    if bolt is None:
        return
    main_line, forks = bolt
    glDisable(GL_TEXTURE_2D)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    glDepthMask(GL_FALSE)
    for line_pts in (main_line, *forks):
        glLineWidth(8.0)
        glColor4f(0.75, 0.90, 1.0, 0.55)
        glBegin(GL_LINE_STRIP)
        for p in line_pts:
            glVertex3f(float(p[0]), float(p[1]), float(p[2]))
        glEnd()
        glLineWidth(2.5)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        glBegin(GL_LINE_STRIP)
        for p in line_pts:
            glVertex3f(float(p[0]), float(p[1]), float(p[2]))
        glEnd()
    glLineWidth(1.0)
    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)


# --- Textures ---
def make_road_texture(size=512):
    """Procedural asphalt with realistic imperfections: base noise, oil
    stains, random-walk cracks, and edge dirt smudges. Lane markings are
    drawn last so imperfections never contaminate the stripes. Sides are
    masked so cracks stay on the drivable lane."""
    rng = np.random.default_rng(7)
    base = rng.integers(72, 108, (size, size)).astype(np.uint8)

    # 1. Coarse asphalt variation (granular/aggregate look)
    for _ in range(240):
        cy, cx = rng.integers(0, size, 2)
        r = rng.integers(4, 18)
        shade = int(rng.integers(-15, 15))
        y0, y1 = max(0, cy - r), min(size, cy + r)
        x0, x1 = max(0, cx - r), min(size, cx + r)
        base[y0:y1, x0:x1] = np.clip(
            base[y0:y1, x0:x1].astype(int) + shade, 55, 130
        ).astype(np.uint8)

    tex = np.stack([base, base, base], axis=-1).astype(np.int16)

    # 2. Oil stains — sparse dark elliptical patches
    ys_m, xs_m = np.mgrid[0:size, 0:size].astype(np.float32)
    for _ in range(5):
        cy = float(rng.integers(60, size - 60))
        cx = float(rng.integers(60, size - 60))
        rx = float(rng.integers(10, 22))
        ry = float(rng.integers(22, 55))
        angle = float(rng.uniform(0.0, math.pi))
        cosA, sinA = math.cos(angle), math.sin(angle)
        dx = xs_m - cx
        dy = ys_m - cy
        xr = dx * cosA + dy * sinA
        yr = -dx * sinA + dy * cosA
        d = (xr / rx) ** 2 + (yr / ry) ** 2
        m = np.clip(1.0 - d, 0.0, 1.0) ** 1.4
        dark = (m * 34).astype(np.int16)
        tex[..., 0] -= dark
        tex[..., 1] -= dark
        tex[..., 2] -= (dark * 1.1).astype(np.int16)

    # 3. Cracks — small random walks, stay off the stripes/center line.
    # Side stripe is at x ∈ [10, 20] and [size-20, size-10]; center line
    # at x ∈ [size/2-6, size/2+6]. We mask those zones when stamping.
    center_lo, center_hi = size // 2 - 8, size // 2 + 8
    for _ in range(90):
        x = float(rng.integers(30, size - 30))
        y = float(rng.integers(0, size))
        dx = float(rng.uniform(-1.0, 1.0))
        dy = float(rng.uniform(-1.0, 1.0))
        n = math.hypot(dx, dy) + 1e-9
        dx /= n; dy /= n
        length = int(rng.integers(14, 55))
        darkness = int(rng.integers(25, 45))
        for _ in range(length):
            xi = int(x) % size
            yi = int(y) % size
            if (20 < xi < size - 20) and not (center_lo <= xi < center_hi):
                tex[yi, xi, 0] = max(15, tex[yi, xi, 0] - darkness)
                tex[yi, xi, 1] = max(15, tex[yi, xi, 1] - darkness)
                tex[yi, xi, 2] = max(15, tex[yi, xi, 2] - darkness)
            # wander slightly
            dx += float(rng.uniform(-0.25, 0.25))
            dy += float(rng.uniform(-0.25, 0.25))
            n = math.hypot(dx, dy) + 1e-9
            dx /= n; dy /= n
            x += dx
            y += dy

    # 4. Edge dirt — brownish smudges tires carry inward from shoulders
    for _ in range(40):
        cy = int(rng.integers(0, size))
        if rng.random() < 0.5:
            cx = int(rng.integers(22, 80))
        else:
            cx = int(rng.integers(size - 80, size - 22))
        r = int(rng.integers(4, 11))
        y0, y1 = max(0, cy - r), min(size, cy + r)
        x0, x1 = max(0, cx - r), min(size, cx + r)
        tex[y0:y1, x0:x1, 0] += 4    # warmer
        tex[y0:y1, x0:x1, 1] -= 3
        tex[y0:y1, x0:x1, 2] -= 8    # less blue → brownish

    tex = np.clip(tex, 0, 255).astype(np.uint8)

    # 5. Lane markings painted LAST so cracks/dirt don't contaminate them
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


def make_overcast_texture(w=1024, h=512, seed=71):
    """Dense storm cloud layer: near-full coverage (~95% alpha) with
    heavy mottled bruising and self-shadowing that reads as turbulent,
    low-altitude stratocumulus. Blended on top of the base cloud layer
    at draw time with an alpha proportional to storm_intensity.

    Design criteria from Dobashi 2000 / Harris & Lastra 2001: storm cloud
    coverage is ~100% with strong low-frequency luminance variation
    (whites bleed into darker grays) rather than discrete cloud puffs.
    Mask is biased to concentrate mid-sky rather than at the zenith so
    the horizon still feels 'weathered' rather than cut off.
    """
    rng = np.random.default_rng(seed)
    noise = np.zeros((h, w), dtype=np.float32)
    amp_sum = 0.0
    # Frequency pyramid tuned for visual density at typical viewing FOV.
    # Old (3,2)..(48,24) spanned only 3-48 features around the whole
    # dome — a 60° view only sees ~1/6 of the azimuth → 0.5 to 8 feature
    # cycles visible, the low end reading as FLAT. New pyramid peaks at
    # 16-32 features so even the lowest band shows meaningful variation
    # across a single frame.
    for fx, fy, amp in [(8, 4, 0.45), (16, 8, 0.35), (32, 16, 0.25),
                        (64, 32, 0.15), (128, 64, 0.08)]:
        noise += _wrap_noise(h, w, fx, fy, rng) * amp
        amp_sum += amp
    noise /= amp_sum
    noise = (noise - noise.min()) / (noise.max() - noise.min() + 1e-9)
    # Boost contrast of the noise so light/dark patches are pronounced
    noise = np.clip((noise - 0.35) / 0.30, 0, 1)

    v = np.linspace(0.0, 1.0, h, dtype=np.float32)
    # Altitude mask: near-full coverage from mid-sky to zenith; fade
    # off near horizon so the overcast deck meets the horizon fog smoothly
    # (without a hard seam). CRUCIAL difference from the fair-weather
    # mask: we do NOT drop alpha at the zenith — real overcast skies are
    # thickest directly overhead, not thinning at the top.
    mask = np.clip((v - 0.02) * 4.0, 0, 1)  # ramps 0..1 by v=0.27, then 1

    # Alpha: dense with strong mottling — bright clumps (alpha~0.96)
    # next to thinner patches (alpha~0.45), driven by the contrast-
    # boosted noise so you can SEE where cloud masses are heavier.
    alpha = (0.45 + 0.52 * noise) * mask[:, None]

    # Self-shadow: noise-derived shading. Keep RGB near 1.0 with
    # moderate variation — the cloud_tint modulation at draw time then
    # darkens it to the correct "stormy gray" colour. If we pre-dark it
    # here the tint * shade product compounds and the cloud pattern
    # disappears into a flat gray wash.
    shadow = np.roll(noise, 10, axis=0) - noise
    shade = np.clip(0.95 - shadow * 2.8, 0.45, 1.0)

    rgba = np.zeros((h, w, 4), dtype=np.float32)
    # Slightly warmer-green highlights, cool in shadow — so when the
    # draw-time tint is neutral-gray the lit cloud tops still read as
    # silver-green against charcoal shadows.
    rgba[..., 0] = 0.98 * shade
    rgba[..., 1] = 1.00 * shade
    rgba[..., 2] = 0.96 * shade
    rgba[..., 3] = np.clip(alpha, 0.0, 0.97)
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
    # sky_state can optionally include a 7th element (overcast_tex) for
    # the dense storm cloud layer. Older callers that pass only 6 still
    # work — we just skip the overcast pass.
    if len(sky_state) >= 7:
        verts, tcs, vfrac, idx, cloud_tex, stars_tex, overcast_tex = sky_state[:7]
    else:
        verts, tcs, vfrac, idx, cloud_tex, stars_tex = sky_state
        overcast_tex = None
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

    # Pass 3: fair-weather clouds (alpha blended, tinted by sky/sun).
    # Cloud drift speeds up proportionally to storm_intensity — the
    # same front that darkens the sky also pushes it — but the fair-
    # weather layer fades OUT as the overcast takes over so we don't
    # see two competing cloud patterns once the storm is fully in.
    fair_alpha = max(0.0, 1.0 - storm * 1.2)
    if fair_alpha > 0.02:
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, cloud_tex)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glColor4f(cloud_tint[0], cloud_tint[1], cloud_tint[2], fair_alpha)
        glEnableClientState(GL_TEXTURE_COORD_ARRAY)
        glTexCoordPointer(2, GL_FLOAT, 0, tcs)
        glMatrixMode(GL_TEXTURE)
        glLoadIdentity()
        glTranslatef(t_time * 0.004 * (1.0 + 1.5 * storm), 0.0, 0.0)
        glMatrixMode(GL_MODELVIEW)
        glDrawElements(GL_TRIANGLES, len(idx), GL_UNSIGNED_INT, idx)
        glMatrixMode(GL_TEXTURE); glLoadIdentity(); glMatrixMode(GL_MODELVIEW)
        glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        glDisable(GL_BLEND)

    # Pass 4: overcast cloud deck (only when storm > ~0.05). Drifts
    # faster and in a different direction than the fair-weather layer
    # so the two don't parallax in lockstep during the transition. Its
    # own alpha ramps from 0 at the storm threshold to near-full at
    # storm = 1.
    if overcast_tex is not None and storm > 0.04:
        # Smoothstep so the deck fades in with physical softness rather
        # than a hard edge as storm crosses the threshold.
        s = max(0.0, min(1.0, (storm - 0.04) / 0.96))
        over_alpha = s * s * (3 - 2 * s)
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, overcast_tex)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        # Use the cloud tint directly — the overcast texture's own
        # desaturated RGB will darken it further through MODULATE.
        glColor4f(cloud_tint[0], cloud_tint[1], cloud_tint[2], over_alpha)
        glEnableClientState(GL_TEXTURE_COORD_ARRAY)
        glTexCoordPointer(2, GL_FLOAT, 0, tcs)
        glMatrixMode(GL_TEXTURE)
        glLoadIdentity()
        # Different scroll axis + speed than fair-weather clouds.
        glTranslatef(t_time * 0.0055, t_time * 0.0012, 0.0)
        glMatrixMode(GL_MODELVIEW)
        glDrawElements(GL_TRIANGLES, len(idx), GL_UNSIGNED_INT, idx)
        # Second pass of the same texture at a different offset when the
        # storm is heavy — piles density on for a churning look.
        if storm > 0.55:
            k = (storm - 0.55) / 0.45
            glColor4f(cloud_tint[0], cloud_tint[1], cloud_tint[2],
                      0.55 * k * over_alpha)
            glLoadIdentity()
            glTranslatef(t_time * 0.0030 + 0.37, -t_time * 0.0008, 0.0)
            glMatrixMode(GL_MODELVIEW)
            glDrawElements(GL_TRIANGLES, len(idx), GL_UNSIGNED_INT, idx)
            glMatrixMode(GL_TEXTURE)
        glLoadIdentity()
        glMatrixMode(GL_MODELVIEW)
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
    s_arr = (np.arange(NS, dtype=np.float32) * SEG_LEN) + (s_car - NEAR_EXTEND)
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


# --- Civil structures: bridges ---
# Bridges are placed where the road passes a river biome on either side —
# the road always crosses water there, so guardrails fit naturally.
# Collision avoidance is automatic because the biomes are disjoint:
# trees (forest / frost) and buildings (city) live in different zones
# from rivers, so structures never overlap them.
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

    if bridge_w.max() < 0.25:
        return  # no bridges in the visible window

    base = _structure_tint(amb_rgb, storm_i)

    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, concrete_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDepthMask(GL_TRUE)  # solid structures participate in depth writes

    if bridge_w.max() >= 0.25:
        _draw_bridges(s_arr, bridge_w, base, frost_i, s_car)

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


def draw_road(tex_id, s_car, amb_rgb, storm_i=0.0):
    # Wet asphalt reads darker (higher absorption, lower diffuse),
    # which naturally dampens the visibility of cracks and dirt when
    # it's raining — same optical reason real wet asphalt looks uniform.
    wet = 1.0 - 0.18 * storm_i
    r = min(1.0, amb_rgb[0] * wet)
    g = min(1.0, amb_rgb[1] * wet)
    b = min(1.0, amb_rgb[2] * (wet + 0.02))  # slight blue wet tint
    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, tex_id)
    glColor3f(r, g, b)
    glBegin(GL_QUAD_STRIP)
    for i in range(N_SEG + 1):
        s = s_car - NEAR_EXTEND + i * SEG_LEN
        x = curve_x(s)
        y = curve_y(s)
        z = -(s - s_car)
        v = s * 0.12
        glTexCoord2f(0.0, v); glVertex3f(x - ROAD_WIDTH / 2, y + 0.03, z)
        glTexCoord2f(1.0, v); glVertex3f(x + ROAD_WIDTH / 2, y + 0.03, z)
    glEnd()
    glDisable(GL_TEXTURE_2D)


def draw_road_snow_overlay(snow_tex, s_car, amb_rgb):
    """Progressive snow accumulation on the pavement. Per-vertex alpha at
    each s equals max(wL_frost, wR_frost) so snow fades in smoothly as
    the road enters a frost biome and fades back out when it leaves —
    matches the biome smoothstep transitions without any extra state.

    Tint is slightly darker than fresh snow (road snow is slushy / tire-
    trafficked, not pristine field snow)."""
    NS = N_SEG + 1
    s_arr = np.arange(NS, dtype=np.float32) * SEG_LEN + (s_car - NEAR_EXTEND)
    frost_w = np.maximum(
        biome_weights_vec(s_arr, -1)[:, BIOME_FROST],
        biome_weights_vec(s_arr, +1)[:, BIOME_FROST],
    )
    if frost_w.max() < 0.02:
        return

    # Slushy/dirty road-snow tint
    tint = (amb_rgb[0] * 0.86, amb_rgb[1] * 0.88, amb_rgb[2] * 0.92)

    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, snow_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDepthMask(GL_FALSE)
    glEnable(GL_POLYGON_OFFSET_FILL)
    glPolygonOffset(-1.4, -1.4)  # sit just above the asphalt

    glBegin(GL_QUAD_STRIP)
    for i in range(NS):
        s = float(s_arr[i])
        x = curve_x(s)
        y = curve_y(s)
        z = -(s - s_car)
        alpha = float(min(1.0, frost_w[i] * 0.95))
        glColor4f(tint[0], tint[1], tint[2], alpha)
        v = s * 0.18
        glTexCoord2f(0.0, v); glVertex3f(x - ROAD_WIDTH / 2, y + 0.04, z)
        glTexCoord2f(0.45, v); glVertex3f(x + ROAD_WIDTH / 2, y + 0.04, z)
    glEnd()

    glDisable(GL_POLYGON_OFFSET_FILL)
    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)


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
        if not is_plain_either_side(s):
            continue
        x = curve_x(s); y = curve_y(s); z = -(s - s_car)
        # Mirror the lamp pair across the road — lights bracket both
        # sides simultaneously, so the right side always gets the mirror
        # of whatever the left side gets.
        for side in (-1, 1):
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
        if not is_plain_either_side(s):
            continue
        fade = max(0.0, 1.0 - d / (N_LAMPS * LAMP_SPACING * 0.9)) * night_a
        x = curve_x(s); y = curve_y(s); z = -d
        for side in (-1, 1):
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


# --- Volumetric dust ---
# Camera-local cloud of illuminated airborne motes. Rare event: a triple-
# sine-product intensity gate keeps dust mostly absent, with occasional
# soft blooms (think pollen in a still meadow, or dry-wind haze). Fully
# additive so stacked particles read as volume rather than flat sprites.
# Suppressed by storm (rain clears the air), scaled by ambient brightness
# (dust motes need light to read), and by open-biome exposure (dust rises
# where land is dry and wind-blown).

DUST_N = 360
DUST_BOX_X = 42.0
DUST_BOX_Z = 52.0
DUST_Y_MIN = 0.4
DUST_Y_MAX = 12.0


def dust_intensity_at(t_time):
    """Three detuned sines with a high threshold — tuned so dust events
    are *genuinely rare*: roughly 1 % of sunny-daytime wall-clock time,
    building and clearing smoothly over tens of seconds so each event
    feels like a single breath of haze rather than a flicker."""
    a = 0.5 + 0.5 * math.sin(t_time * 2 * math.pi / 210.0)
    b = 0.5 + 0.5 * math.sin(t_time * 2 * math.pi / 67.0 + 1.7)
    c = 0.5 + 0.5 * math.sin(t_time * 2 * math.pi / 143.0 + 0.5)
    raw = a * b * c
    # Threshold raised and the 0→1 range narrowed; peaks only light up
    # when all three sines align at the top of their cycles.
    x = max(0.0, (raw - 0.78) / 0.22)
    x = min(1.0, x)
    return x * x * (3.0 - 2.0 * x)


def init_dust(seed=201):
    rng = np.random.default_rng(seed)
    pos = np.zeros((DUST_N, 3), dtype=np.float32)
    pos[:, 0] = rng.uniform(-DUST_BOX_X, DUST_BOX_X, DUST_N)
    pos[:, 1] = rng.uniform(DUST_Y_MIN, DUST_Y_MAX, DUST_N)
    pos[:, 2] = rng.uniform(-DUST_BOX_Z, DUST_BOX_Z * 0.3, DUST_N)
    vel = np.zeros((DUST_N, 3), dtype=np.float32)
    vel[:, 0] = rng.uniform(-0.35, 0.35, DUST_N)
    vel[:, 1] = rng.uniform(-0.18, 0.05, DUST_N)  # near-zero gravity — motes hover
    vel[:, 2] = rng.uniform(-0.30, 0.30, DUST_N)
    seeds = rng.uniform(0.0, 2 * math.pi, DUST_N).astype(np.float32)
    return pos, vel, seeds


def update_dust(state, dt, t_time):
    pos, vel, seeds = state
    # Low-frequency swirl per particle — gives each mote its own life
    swx = 0.16 * np.sin(t_time * 0.7 + seeds)
    swy = 0.08 * np.sin(t_time * 0.45 + seeds * 1.3)
    swz = 0.14 * np.cos(t_time * 0.6 + seeds * 0.9)
    pos[:, 0] += (vel[:, 0] + swx) * dt
    pos[:, 1] += (vel[:, 1] + swy) * dt
    pos[:, 2] += (vel[:, 2] + swz) * dt

    below = pos[:, 1] < DUST_Y_MIN
    above = pos[:, 1] > DUST_Y_MAX
    outX = np.abs(pos[:, 0]) > DUST_BOX_X
    outZ = np.abs(pos[:, 2]) > DUST_BOX_Z
    respawn = below | above | outX | outZ
    n = int(respawn.sum())
    if n > 0:
        rng = np.random.default_rng()
        pos[respawn, 0] = rng.uniform(-DUST_BOX_X, DUST_BOX_X, n).astype(np.float32)
        pos[respawn, 1] = rng.uniform(DUST_Y_MIN, DUST_Y_MAX, n).astype(np.float32)
        pos[respawn, 2] = rng.uniform(-DUST_BOX_Z, DUST_BOX_Z * 0.3, n).astype(np.float32)


def draw_dust(state, dust_tex, intensity, cam_x, cam_y, cam_z, amb_rgb):
    """Additive point sprites — stacked particles brighten naturally,
    giving a volumetric read without any actual volumetric rendering."""
    if intensity < 0.03:
        return
    pos, _v, _s = state

    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, dust_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_POINT_SPRITE)
    glTexEnvi(GL_POINT_SPRITE, GL_COORD_REPLACE, GL_TRUE)
    glPointSize(5.5)
    glPointParameterfv(GL_POINT_DISTANCE_ATTENUATION, (0.0, 0.10, 0.0025))
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    glDepthMask(GL_FALSE)

    glPushMatrix()
    glTranslatef(cam_x, cam_y, cam_z)
    # Warm ivory dust tint modulated by ambient, so motes dim at night.
    # Alpha capped low — dust should feel like a soft breath of haze,
    # never a solid fog. The user-facing request was "soft".
    glColor4f(min(1.0, amb_rgb[0] + 0.05),
              min(1.0, amb_rgb[1] * 0.92),
              min(1.0, amb_rgb[2] * 0.72),
              min(1.0, intensity * 0.32))
    glEnableClientState(GL_VERTEX_ARRAY)
    glVertexPointer(3, GL_FLOAT, 0, pos)
    glDrawArrays(GL_POINTS, 0, len(pos))
    glDisableClientState(GL_VERTEX_ARRAY)
    glPopMatrix()

    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)
    glDisable(GL_POINT_SPRITE)


# --- Procedural flowers ---
# Small wild flowers along the roadside in flat / vegetated biomes.
# Rendered as Lighthouse3D-style crossed billboard quads (two textured
# quads at 90° to each other) with alpha-cutout petals and stem baked
# into one RGBA texture.
#
# Diversity: six colour variants generated from a single procedural
# recipe (petals + center disc + stem) by seeding different palettes.
# Placement: plain + hill + forest biomes, not mountain/river/frost/city.
# Wind sway: same rotation-around-base trick as trees but with a larger
# amplitude and a higher sway rate so flowers feel more reactive than
# the canopies above them. A subset of slots becomes a *crop* (a dense
# cluster of 6-12 flowers in a small patch) so the shoulder isn't a
# uniform sprinkling.

FLOWER_SPACING = 0.85           # stride along road for candidate flower slots
FLOWER_MIN_D = 0.15             # right up against the pavement shoulder
FLOWER_MAX_D = 3.0              # stay within the visible strip next to the road
FLOWER_MAX_RENDER_DIST = 280.0  # distance culling — flowers are tiny past this
FLOWER_CLUSTER_PROB = 0.06      # 6% of valid slots become a crop cluster


FLOWER_PALETTES = [
    # (petal_rgb, center_rgb)
    ((1.00, 0.90, 0.20), (0.65, 0.40, 0.08)),   # 0 yellow daisy
    ((0.95, 0.28, 0.20), (0.25, 0.10, 0.08)),   # 1 red poppy
    ((0.62, 0.30, 0.85), (0.85, 0.78, 0.25)),   # 2 purple aster
    ((0.98, 0.96, 0.95), (0.95, 0.80, 0.20)),   # 3 white daisy
    ((0.98, 0.62, 0.78), (0.70, 0.40, 0.35)),   # 4 pink cosmos
    ((1.00, 0.55, 0.20), (0.40, 0.20, 0.08)),   # 5 orange marigold
]


def make_flower_texture(petal_rgb, center_rgb, size=128, seed=0):
    """Procedural RGBA flower — petals radiating around a center disc, with
    a slim green stem below. Top ~55% of the texture is the flower head,
    bottom ~45% is the stem, so the same quad carries both when drawn as
    a vertical billboard."""
    rng = np.random.default_rng(seed + 5000)
    rgba = np.zeros((size, size, 4), dtype=np.float32)
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)

    # Flower head is positioned in the upper part of the texture so the
    # rendered quad will have flower on top, stem trailing below.
    cx = size / 2.0
    cy = size * 0.32   # flower center a bit above the middle
    petal_len = size * 0.24
    petal_wid = size * 0.095
    n_petals = int(rng.integers(6, 10))
    base_angle = float(rng.uniform(0.0, 2 * math.pi / max(1, n_petals)))
    petal_variation = float(rng.uniform(-0.04, 0.08))

    for i in range(n_petals):
        angle = base_angle + i * 2 * math.pi / n_petals + petal_variation * i
        off = petal_len * 0.7
        px = cx + off * math.cos(angle)
        py = cy + off * math.sin(angle)
        cosA = math.cos(angle); sinA = math.sin(angle)
        dx = xs - px
        dy = ys - py
        xr = dx * cosA + dy * sinA
        yr = -dx * sinA + dy * cosA
        d = (xr / petal_len) ** 2 + (yr / petal_wid) ** 2
        m = np.clip(1.0 - d, 0.0, 1.0) ** 1.3
        # Slight hue variation per petal for painterly feel
        jitter = 0.85 + 0.15 * ((i % 3) / 2.0)
        pr = petal_rgb[0] * jitter
        pg = petal_rgb[1] * jitter
        pb = petal_rgb[2] * jitter
        sa = m * 0.96
        rgba[..., 0] = rgba[..., 0] * (1 - sa) + pr * sa
        rgba[..., 1] = rgba[..., 1] * (1 - sa) + pg * sa
        rgba[..., 2] = rgba[..., 2] * (1 - sa) + pb * sa
        rgba[..., 3] = np.maximum(rgba[..., 3], m)

    # Center disc
    dx = xs - cx
    dy = ys - cy
    d = np.sqrt(dx * dx + dy * dy) / (size * 0.085)
    m = np.clip(1.0 - d, 0.0, 1.0) ** 1.6
    sa = m * 1.0
    rgba[..., 0] = rgba[..., 0] * (1 - sa) + center_rgb[0] * sa
    rgba[..., 1] = rgba[..., 1] * (1 - sa) + center_rgb[1] * sa
    rgba[..., 2] = rgba[..., 2] * (1 - sa) + center_rgb[2] * sa
    rgba[..., 3] = np.maximum(rgba[..., 3], m)

    # Slim green stem from just under the center downward to the bottom
    stem_x = int(cx)
    stem_y0 = int(cy + size * 0.02)
    stem_y1 = size
    stem_half_w = 2
    stem_rgb = (0.22, 0.46, 0.14)
    for y in range(stem_y0, stem_y1):
        for dxp in range(-stem_half_w, stem_half_w + 1):
            xi = stem_x + dxp
            if 0 <= xi < size:
                # Fade stem slightly as it goes down
                t = 1.0 - (y - stem_y0) / max(1, (stem_y1 - stem_y0))
                rgba[y, xi, 0] = stem_rgb[0] * (0.7 + 0.3 * t)
                rgba[y, xi, 1] = stem_rgb[1] * (0.7 + 0.3 * t)
                rgba[y, xi, 2] = stem_rgb[2] * (0.7 + 0.3 * t)
                rgba[y, xi, 3] = 1.0

    # A couple of slim leaves on the stem
    for ly in (int(size * 0.55), int(size * 0.70)):
        lenpx = size * 0.07
        for dy_ in range(-3, 4):
            for dx_ in range(-int(lenpx), int(lenpx)):
                m_leaf = max(0.0, 1.0 - (dx_ / lenpx) ** 2 - (dy_ / 3.0) ** 2)
                if m_leaf <= 0:
                    continue
                xi = stem_x + dx_
                yi = ly + dy_
                if 0 <= xi < size and 0 <= yi < size:
                    rgba[yi, xi, 0] = stem_rgb[0] * 1.15
                    rgba[yi, xi, 1] = stem_rgb[1] * 1.15
                    rgba[yi, xi, 2] = stem_rgb[2] * 1.15
                    rgba[yi, xi, 3] = max(rgba[yi, xi, 3], m_leaf)

    return (np.clip(rgba, 0, 1) * 255).astype(np.uint8)


def build_flower_variant(flower_tex):
    """Two crossed textured quads (N-S and E-W oriented) with alpha test
    baked in. Flower head at top of the quad, stem at bottom — the
    display list's texture bind is the flower colour, so instancing just
    means glCallList at the right transform."""
    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    glBindTexture(GL_TEXTURE_2D, flower_tex)
    glEnable(GL_ALPHA_TEST)
    glAlphaFunc(GL_GREATER, 0.45)
    # A unit-size billboard: -0.5..+0.5 in X, 0..1 in Y. Runtime glScalef
    # turns it into 0.3-0.5 m tall flowers.
    for rot in (0.0, 90.0):
        glPushMatrix()
        glRotatef(rot, 0.0, 1.0, 0.0)
        glBegin(GL_QUADS)
        glTexCoord2f(0.0, 0.0); glVertex3f(-0.5, 0.0, 0.0)
        glTexCoord2f(1.0, 0.0); glVertex3f(0.5, 0.0, 0.0)
        glTexCoord2f(1.0, 1.0); glVertex3f(0.5, 1.0, 0.0)
        glTexCoord2f(0.0, 1.0); glVertex3f(-0.5, 1.0, 0.0)
        glEnd()
        glPopMatrix()
    glDisable(GL_ALPHA_TEST)
    glEndList()
    return list_id


def draw_flowers(s_car, flower_variants, amb_rgb, t_time, wind_strength):
    """Instance flowers + clusters along flat-ground biomes. Wind sway
    uses a larger amplitude and higher frequency than trees so flowers
    feel lighter. Distance-culled past ~280 m because they're tiny."""
    s_start = math.floor(s_car / FLOWER_SPACING) * FLOWER_SPACING
    max_s = s_car + min(N_SEG * SEG_LEN, FLOWER_MAX_RENDER_DIST)
    n_steps = int((max_s - s_start) / FLOWER_SPACING) + 1
    nvar = len(flower_variants)
    if nvar == 0 or n_steps <= 0:
        return

    # Wind parameters — larger and faster than the tree sway
    sway = wind_strength > 0.02
    if sway:
        gust = 0.55 + 0.45 * math.sin(t_time * 0.35)
        amp_deg = wind_strength * gust * 10.0      # up to ~8° per axis
        omega = 2.6 + wind_strength * 3.5
        lean_deg = wind_strength * 2.5
    else:
        amp_deg = omega = lean_deg = 0.0

    glEnable(GL_TEXTURE_2D)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glColor3f(min(1.0, amb_rgb[0]),
              min(1.0, amb_rgb[1]),
              min(1.0, amb_rgb[2]))

    for i in range(n_steps):
        s = s_start + i * FLOWER_SPACING
        if s < s_car - 0.5:
            continue
        for side in (-1, +1):
            w_b = biome_weights_vec(np.array([s], dtype=np.float32), side)[0]
            # Coherent with landscape: plain, hill, forest welcome flowers.
            # Skip mountain (rocky), frost (snow), river (water), city.
            ok = float(w_b[BIOME_PLAIN] + w_b[BIOME_HILL]
                       + w_b[BIOME_FOREST])
            bad = float(w_b[BIOME_MOUNTAIN] + w_b[BIOME_RIVER]
                        + w_b[BIOME_FROST] + w_b[BIOME_CITY])
            if ok < 0.55 or bad > 0.30:
                continue

            key = (int(s * 41) * 2654435761
                   + (0 if side < 0 else 3019)) & 0xFFFFFFFF
            # Density gate — probability of a flower at this slot
            density = 0.40 * ok
            if (key & 0xFF) / 255.0 > density:
                continue

            # Rarely a crop cluster instead of a single flower
            is_cluster = ((key >> 24) & 0xFF) / 255.0 < FLOWER_CLUSTER_PROB
            cluster_n = int(rng_range_from_hash(key, 6, 13)) if is_cluster else 1

            d_base = FLOWER_MIN_D + ((key >> 8) & 0xFF) / 255.0 \
                     * (FLOWER_MAX_D - FLOWER_MIN_D)
            variant = ((key >> 16) & 0x0F) % nvar
            scale_base = 0.30 + ((key >> 20) & 0x0F) / 15.0 * 0.25
            # Phase for wind, seeded by slot
            phase0 = ((key >> 12) & 0x3FFF) * (2.0 * math.pi / 0x3FFF)

            for k in range(cluster_n):
                if cluster_n > 1:
                    ck = (key * 0x9E3779B1 + k * 0x85EBCA77) & 0xFFFFFFFF
                    jitter_x = ((ck & 0xFFFF) / 65535.0 - 0.5) * 1.6
                    jitter_z = (((ck >> 16) & 0xFFFF) / 65535.0 - 0.5) * 1.6
                    jitter_d = (ck & 0xFF) / 255.0 * 0.8
                    phase = phase0 + k * 0.37
                else:
                    jitter_x = jitter_z = jitter_d = 0.0
                    phase = phase0

                tx = curve_x(s) + side * (ROAD_WIDTH / 2 + d_base + jitter_d) + jitter_x
                ty = curve_y(s) - 0.08
                tz = -(s - s_car) + jitter_z

                glPushMatrix()
                glTranslatef(tx, ty, tz)
                if sway:
                    sx = lean_deg + amp_deg * math.sin(t_time * omega + phase)
                    sz = amp_deg * 0.65 * math.sin(
                        t_time * omega * 1.3 + phase + 0.5
                    )
                    glRotatef(sx, 1.0, 0.0, 0.0)
                    glRotatef(sz, 0.0, 0.0, 1.0)
                glScalef(scale_base, scale_base, scale_base)
                glCallList(flower_variants[variant])
                glPopMatrix()


def rng_range_from_hash(key, lo, hi):
    """Small stable helper: turn the low bits of a 32-bit hash into an
    integer in [lo, hi]."""
    span = max(1, hi - lo + 1)
    return lo + ((key >> 28) % span)


# --- Procedural houses ---
# Rural / suburban dwellings placed far enough from the road that
# geometric imperfections blur out (50-85 m perpendicular). Each house is
# a rectangular prism body with a gable roof, with two triangular gable
# end walls, a front door, and several windows baked in.
#
# Diversity: a small library of wall and roof textures is cross-combined
# into several variants with randomised dimensions. Each variant is
# compiled once as two display lists:
#   * body_list — 4 walls (wall tex) + 2 gable triangles + untextured
#     door + windows. Bakes its own texture bind.
#   * roof_list — the two slope quads only, leaves no texture bound so
#     the caller can re-bind snow texture for a progressive roof-snow
#     overlay during frost zones.
#
# Placement:
#   * Biomes allowed: plain, forest, frost (flat-ground biomes).
#   * Skipped: mountain (would collide with steep rise), city
#     (skyscrapers already there), river (water), hill (terrain rises
#     too much at range 50-85 m to plant a house cleanly).
#   * Perpendicular distance 50-85 m from the road edge keeps houses
#     clear of trees (max 47 m) and well clear of city skyscrapers
#     (which start at 92 m). Result: no collisions with other biome-
#     placed content.
#   * Deterministic per-slot hash for position, variant, yaw, scale.
#   * Roof snow: a second pass of roof_list with snow ground texture
#     and per-house alpha = frost weight — snow accumulates during
#     frost biomes and melts away when the biome transitions back.

HOUSE_SPACING = 22.0
HOUSE_MIN_D = 50.0
HOUSE_MAX_D = 85.0
HOUSE_WALL_H_RANGE = (3.2, 4.6)
HOUSE_ROOF_H_RANGE = (2.0, 3.5)


def make_brick_wall_texture(size=256, seed=401):
    rng = np.random.default_rng(seed)
    tex = np.zeros((size, size, 3), dtype=np.uint8)
    # mortar base
    tex[..., 0] = 200
    tex[..., 1] = 190
    tex[..., 2] = 175
    rows = 10
    brick_h = size // rows
    cols = 6
    brick_w = size // cols
    for row in range(rows + 1):
        y0 = row * brick_h + 2
        y1 = y0 + brick_h - 4
        offset = 0 if row % 2 == 0 else brick_w // 2
        for col in range(cols + 1):
            x0 = (col * brick_w + offset) % size
            x1 = min(size, x0 + brick_w - 4)
            if x1 <= x0:
                continue
            r = int(rng.integers(140, 190))
            g = int(rng.integers(55, 85))
            b = int(rng.integers(40, 65))
            tex[max(0, y0):min(size, y1), x0:x1, 0] = r
            tex[max(0, y0):min(size, y1), x0:x1, 1] = g
            tex[max(0, y0):min(size, y1), x0:x1, 2] = b
    return tex


def make_wood_siding_texture(size=256, seed=403):
    rng = np.random.default_rng(seed)
    tex = np.zeros((size, size, 3), dtype=np.uint8)
    planks = 8
    plank_w = size // planks
    for p in range(planks + 1):
        x0 = p * plank_w
        x1 = min(size, x0 + plank_w - 2)
        if x1 <= x0:
            continue
        base = int(rng.integers(95, 150))
        tex[:, x0:x1, 0] = base
        tex[:, x0:x1, 1] = int(base * 0.72)
        tex[:, x0:x1, 2] = int(base * 0.45)
        if x1 < size:
            tex[:, x1:x1 + 2, :] = [50, 30, 15]
    noise = rng.integers(-12, 12, (size, 1), dtype=np.int16)
    tex = np.clip(tex.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return tex


def make_plaster_texture(size=256, seed=405):
    rng = np.random.default_rng(seed)
    base = rng.integers(198, 228, (size, size)).astype(np.int16)
    # small dark mildew spots for realism
    for _ in range(30):
        cy, cx = rng.integers(0, size, 2)
        r = int(rng.integers(3, 9))
        y0, y1 = max(0, cy - r), min(size, cy + r)
        x0, x1 = max(0, cx - r), min(size, cx + r)
        base[y0:y1, x0:x1] -= 25
    base = np.clip(base, 80, 255).astype(np.uint8)
    return np.stack([base,
                     np.clip(base.astype(int) - 8, 0, 255).astype(np.uint8),
                     np.clip(base.astype(int) - 18, 0, 255).astype(np.uint8)],
                    axis=-1)


def make_stone_wall_texture(size=256, seed=407):
    rng = np.random.default_rng(seed)
    base = rng.integers(110, 165, (size, size)).astype(np.int16)
    for _ in range(60):
        cy, cx = rng.integers(0, size, 2)
        r = int(rng.integers(10, 30))
        y0, y1 = max(0, cy - r), min(size, cy + r)
        x0, x1 = max(0, cx - r), min(size, cx + r)
        shade = int(rng.integers(-18, 12))
        base[y0:y1, x0:x1] = np.clip(base[y0:y1, x0:x1] + shade, 75, 185)
    base = base.astype(np.uint8)
    return np.stack([base, base,
                     np.clip(base.astype(int) + 6, 0, 255).astype(np.uint8)],
                    axis=-1)


def make_tile_roof_texture(size=256, seed=411):
    rng = np.random.default_rng(seed)
    tex = np.zeros((size, size, 3), dtype=np.uint8)
    rows = 9
    tile_h = size // rows
    cols = 10
    tile_w = size // cols
    for row in range(rows + 1):
        y0 = row * tile_h
        y1 = min(size, y0 + tile_h - 2)
        for col in range(cols + 1):
            x0 = (col * tile_w + (0 if row % 2 == 0 else tile_w // 2)) % size
            x1 = min(size, x0 + tile_w - 2)
            if x1 <= x0:
                continue
            r = int(rng.integers(140, 200))
            g = int(rng.integers(60, 100))
            b = int(rng.integers(40, 70))
            tex[y0:y1, x0:x1, 0] = r
            tex[y0:y1, x0:x1, 1] = g
            tex[y0:y1, x0:x1, 2] = b
        if y1 < size:
            tex[y1:y1 + 2, :, :] = [40, 20, 10]
    return tex


def make_shingle_roof_texture(size=256, seed=413):
    rng = np.random.default_rng(seed)
    tex = np.zeros((size, size, 3), dtype=np.uint8)
    rows = 16
    shingle_h = size // rows
    cols = 10
    shingle_w = size // cols
    for row in range(rows + 1):
        y0 = row * shingle_h
        y1 = min(size, y0 + shingle_h - 1)
        for col in range(cols + 1):
            x0 = (col * shingle_w + (0 if row % 2 == 0 else shingle_w // 2)) % size
            x1 = min(size, x0 + shingle_w - 1)
            if x1 <= x0:
                continue
            base = int(rng.integers(55, 95))
            tex[y0:y1, x0:x1, 0] = base
            tex[y0:y1, x0:x1, 1] = int(base * 0.92)
            tex[y0:y1, x0:x1, 2] = int(base * 0.72)
        if y1 < size:
            tex[y1:y1 + 1, :, :] = [22, 16, 10]
    return tex


def make_slate_roof_texture(size=256, seed=415):
    rng = np.random.default_rng(seed)
    tex = np.zeros((size, size, 3), dtype=np.uint8)
    rows = 12
    slate_h = size // rows
    cols = 7
    slate_w = size // cols
    for row in range(rows + 1):
        y0 = row * slate_h
        y1 = min(size, y0 + slate_h - 2)
        for col in range(cols + 1):
            x0 = (col * slate_w + (0 if row % 2 == 0 else slate_w // 2)) % size
            x1 = min(size, x0 + slate_w - 2)
            if x1 <= x0:
                continue
            base = int(rng.integers(55, 95))
            tex[y0:y1, x0:x1, 0] = int(base * 0.82)
            tex[y0:y1, x0:x1, 1] = int(base * 0.88)
            tex[y0:y1, x0:x1, 2] = int(base * 1.05) % 255
        if y1 < size:
            tex[y1:y1 + 2, :, :] = [18, 20, 24]
    return tex


def build_house_body_list(wall_tex, w, d, wall_h, roof_h):
    """Walls + gable triangles + plinth + door/window recesses with trim.

    Realism criteria from the procedural-architecture literature
    (Müller 2006 split grammar, Lipp 2008 frame-depth study, Musialski
    2013 survey):
      * Plinth (darker foundation band at ground) — single biggest cue
        that a wall meets the earth rather than floats above it.
      * Window recess — the pane is inset a few cm from the wall plane,
        with a lighter sill below and a trim frame around. Frame depth,
        not texture, is what reads as "built" from a distance.
      * Door surround — threshold step + trim frame.
      * Gable triangles that actually reach the roof ridge — previous
        version peaked at wall_h + 0.01, leaving a visible gap under the
        roof.
    Window positions (wall_h*0.55 centre, ±w*0.28 on front/back,
    ±d*0.28 on sides) are kept identical to the emission list so the
    night glow lands exactly on the panes.
    """
    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, wall_tex)
    # NOTE: do not reset glColor here — the caller sets the ambient tint
    # before glCallList. Setting (1,1,1) inside the list would make the
    # walls render at full brightness even at night.
    u_rep = w / 2.5
    u_rep_s = d / 2.5
    v_rep = wall_h / 2.5

    # --- Main walls (4 quads)
    glBegin(GL_QUADS)
    glNormal3f(0, 0, -1)
    glTexCoord2f(0, 0); glVertex3f(-w / 2, 0, -d / 2)
    glTexCoord2f(u_rep, 0); glVertex3f(w / 2, 0, -d / 2)
    glTexCoord2f(u_rep, v_rep); glVertex3f(w / 2, wall_h, -d / 2)
    glTexCoord2f(0, v_rep); glVertex3f(-w / 2, wall_h, -d / 2)
    glNormal3f(0, 0, 1)
    glTexCoord2f(0, 0); glVertex3f(w / 2, 0, d / 2)
    glTexCoord2f(u_rep, 0); glVertex3f(-w / 2, 0, d / 2)
    glTexCoord2f(u_rep, v_rep); glVertex3f(-w / 2, wall_h, d / 2)
    glTexCoord2f(0, v_rep); glVertex3f(w / 2, wall_h, d / 2)
    glNormal3f(-1, 0, 0)
    glTexCoord2f(0, 0); glVertex3f(-w / 2, 0, d / 2)
    glTexCoord2f(u_rep_s, 0); glVertex3f(-w / 2, 0, -d / 2)
    glTexCoord2f(u_rep_s, v_rep); glVertex3f(-w / 2, wall_h, -d / 2)
    glTexCoord2f(0, v_rep); glVertex3f(-w / 2, wall_h, d / 2)
    glNormal3f(1, 0, 0)
    glTexCoord2f(0, 0); glVertex3f(w / 2, 0, -d / 2)
    glTexCoord2f(u_rep_s, 0); glVertex3f(w / 2, 0, d / 2)
    glTexCoord2f(u_rep_s, v_rep); glVertex3f(w / 2, wall_h, d / 2)
    glTexCoord2f(0, v_rep); glVertex3f(w / 2, wall_h, -d / 2)
    glEnd()

    # --- Gable triangles (front + back) — apex at the real ridge height.
    # UVs run from wall_h to apex along V so the wall material
    # continues naturally up the triangle.
    glBegin(GL_TRIANGLES)
    apex_y = wall_h + roof_h
    v_tri = roof_h / 2.5
    for z_face, n in ((-d / 2, -1.0), (d / 2, 1.0)):
        glNormal3f(0, 0, n)
        glTexCoord2f(0, 0); glVertex3f(-w / 2, wall_h, z_face)
        glTexCoord2f(u_rep, 0); glVertex3f(w / 2, wall_h, z_face)
        glTexCoord2f(u_rep / 2, v_tri); glVertex3f(0.0, apex_y, z_face)
    glEnd()

    # --- Plinth / foundation: a 0.35m dark stone band around the whole
    # house, projected outward 0.08m so it reads as a wider base and
    # casts a subtle horizontal line at the wall/ground boundary.
    plinth_h = 0.35
    plinth_out = 0.08
    glDisable(GL_TEXTURE_2D)
    glColor3f(0.28, 0.26, 0.23)
    glBegin(GL_QUADS)
    # Four outer faces
    for nx, nz, x0, z0, x1, z1 in (
        (0, -1, -w / 2 - plinth_out, -d / 2 - plinth_out,
                w / 2 + plinth_out, -d / 2 - plinth_out),
        (0,  1,  w / 2 + plinth_out,  d / 2 + plinth_out,
                -w / 2 - plinth_out,  d / 2 + plinth_out),
        (-1, 0, -w / 2 - plinth_out,  d / 2 + plinth_out,
                -w / 2 - plinth_out, -d / 2 - plinth_out),
        ( 1, 0,  w / 2 + plinth_out, -d / 2 - plinth_out,
                 w / 2 + plinth_out,  d / 2 + plinth_out),
    ):
        glNormal3f(nx, 0, nz)
        glVertex3f(x0, 0, z0)
        glVertex3f(x1, 0, z1)
        glVertex3f(x1, plinth_h, z1)
        glVertex3f(x0, plinth_h, z0)
    # Top of plinth (ring)
    glNormal3f(0, 1, 0)
    for x0, z0, x1, z1 in (
        (-w / 2 - plinth_out, -d / 2 - plinth_out,
          w / 2 + plinth_out, -d / 2),
        (-w / 2 - plinth_out,  d / 2,
          w / 2 + plinth_out,  d / 2 + plinth_out),
        (-w / 2 - plinth_out, -d / 2, -w / 2,  d / 2),
        ( w / 2, -d / 2,  w / 2 + plinth_out,  d / 2),
    ):
        glVertex3f(x0, plinth_h, z0)
        glVertex3f(x1, plinth_h, z0)
        glVertex3f(x1, plinth_h, z1)
        glVertex3f(x0, plinth_h, z1)
    glEnd()

    # --- Door on front (-Z): recessed frame + threshold + door panel
    eps = 0.018
    door_w_ = 0.95
    door_h_ = 2.05
    frame_t = 0.09  # frame thickness
    frame_depth = 0.06
    # Frame (wood trim)
    glColor3f(0.78, 0.68, 0.52)
    glBegin(GL_QUADS)
    # Top lintel
    glVertex3f(-door_w_ / 2 - frame_t, door_h_, -d / 2 - eps)
    glVertex3f(door_w_ / 2 + frame_t, door_h_, -d / 2 - eps)
    glVertex3f(door_w_ / 2 + frame_t, door_h_ + frame_t, -d / 2 - eps)
    glVertex3f(-door_w_ / 2 - frame_t, door_h_ + frame_t, -d / 2 - eps)
    # Left jamb
    glVertex3f(-door_w_ / 2 - frame_t, 0.0, -d / 2 - eps)
    glVertex3f(-door_w_ / 2, 0.0, -d / 2 - eps)
    glVertex3f(-door_w_ / 2, door_h_, -d / 2 - eps)
    glVertex3f(-door_w_ / 2 - frame_t, door_h_, -d / 2 - eps)
    # Right jamb
    glVertex3f(door_w_ / 2, 0.0, -d / 2 - eps)
    glVertex3f(door_w_ / 2 + frame_t, 0.0, -d / 2 - eps)
    glVertex3f(door_w_ / 2 + frame_t, door_h_, -d / 2 - eps)
    glVertex3f(door_w_ / 2, door_h_, -d / 2 - eps)
    glEnd()
    # Recessed door panel (painted wood)
    glColor3f(0.42, 0.22, 0.14)
    door_z = -d / 2 - eps + frame_depth
    glBegin(GL_QUADS)
    glVertex3f(-door_w_ / 2, 0.0, door_z)
    glVertex3f(door_w_ / 2, 0.0, door_z)
    glVertex3f(door_w_ / 2, door_h_, door_z)
    glVertex3f(-door_w_ / 2, door_h_, door_z)
    glEnd()
    # Threshold step
    glColor3f(0.48, 0.46, 0.42)
    step_out = 0.30
    step_h = 0.18
    glBegin(GL_QUADS)
    glNormal3f(0, 1, 0)
    glVertex3f(-door_w_ / 2 - 0.15, step_h, -d / 2 - step_out)
    glVertex3f(door_w_ / 2 + 0.15, step_h, -d / 2 - step_out)
    glVertex3f(door_w_ / 2 + 0.15, step_h, -d / 2)
    glVertex3f(-door_w_ / 2 - 0.15, step_h, -d / 2)
    glNormal3f(0, 0, -1)
    glVertex3f(-door_w_ / 2 - 0.15, 0, -d / 2 - step_out)
    glVertex3f(door_w_ / 2 + 0.15, 0, -d / 2 - step_out)
    glVertex3f(door_w_ / 2 + 0.15, step_h, -d / 2 - step_out)
    glVertex3f(-door_w_ / 2 - 0.15, step_h, -d / 2 - step_out)
    glEnd()
    # Door handle — small bright dot
    glColor3f(0.85, 0.75, 0.35)
    hx = door_w_ * 0.30
    hy = door_h_ * 0.5
    hr = 0.05
    glBegin(GL_QUADS)
    glVertex3f(hx - hr, hy - hr, door_z + 0.005)
    glVertex3f(hx + hr, hy - hr, door_z + 0.005)
    glVertex3f(hx + hr, hy + hr, door_z + 0.005)
    glVertex3f(hx - hr, hy + hr, door_z + 0.005)
    glEnd()

    # --- Windows: frame + sill + glass at layered protrusion depths
    #
    # True recessing would require cutting a hole in the wall. Instead
    # we layer three planes proud of the wall — frame furthest out, glass
    # between, sill projecting horizontally below. Reads as a shallow
    # bas-relief window, consistent regardless of viewing angle.
    win_w_ = 0.95
    win_h_ = 1.05
    win_y = wall_h * 0.55 - win_h_ / 2
    win_frame_t = 0.08
    frame_out = 0.025   # frame trim protrudes this far from the wall
    glass_out = 0.008   # glass sits between wall and frame
    # Frame color (painted trim)
    frame_c = (0.88, 0.84, 0.76)
    glass_c = (0.18, 0.24, 0.34)
    sill_c = (0.60, 0.58, 0.54)

    def _window_on_face(cx, cy, face_axis, face_sign):
        """Draw frame+sill+glass at centre (cx, cy) on a given cardinal
        face. face_axis: 'z' for front/back, 'x' for sides. face_sign ±1.
        """
        if face_axis == 'z':
            n = -face_sign
            face_val = face_sign * d / 2
            # 'outer' = frame plane (most proud), 'inner' = glass plane
            outer = face_val + (-frame_out if face_sign < 0 else frame_out)
            inner = face_val + (-glass_out if face_sign < 0 else glass_out)
            x0, x1 = cx - win_w_ / 2, cx + win_w_ / 2
            x0f, x1f = cx - win_w_ / 2 - win_frame_t, cx + win_w_ / 2 + win_frame_t
            y0, y1 = cy - win_h_ / 2, cy + win_h_ / 2
            y0f, y1f = y0 - win_frame_t, y1 + win_frame_t
            # Frame (4 strips around)
            glColor3f(*frame_c)
            glBegin(GL_QUADS)
            glNormal3f(0, 0, n)
            # top
            glVertex3f(x0f, y1, outer); glVertex3f(x1f, y1, outer)
            glVertex3f(x1f, y1f, outer); glVertex3f(x0f, y1f, outer)
            # bottom
            glVertex3f(x0f, y0f, outer); glVertex3f(x1f, y0f, outer)
            glVertex3f(x1f, y0, outer); glVertex3f(x0f, y0, outer)
            # left
            glVertex3f(x0f, y0, outer); glVertex3f(x0, y0, outer)
            glVertex3f(x0, y1, outer); glVertex3f(x0f, y1, outer)
            # right
            glVertex3f(x1, y0, outer); glVertex3f(x1f, y0, outer)
            glVertex3f(x1f, y1, outer); glVertex3f(x1, y1, outer)
            glEnd()
            # Glass pane (recessed)
            glColor3f(*glass_c)
            glBegin(GL_QUADS)
            glVertex3f(x0, y0, inner); glVertex3f(x1, y0, inner)
            glVertex3f(x1, y1, inner); glVertex3f(x0, y1, inner)
            # Horizontal mullion (muntin) — thin painted bar across
            glColor3f(*frame_c)
            ym = (y0 + y1) / 2
            mt = 0.03
            glVertex3f(x0, ym - mt, outer - 0.005 * face_sign)
            glVertex3f(x1, ym - mt, outer - 0.005 * face_sign)
            glVertex3f(x1, ym + mt, outer - 0.005 * face_sign)
            glVertex3f(x0, ym + mt, outer - 0.005 * face_sign)
            glEnd()
            # Sill (projects outward + down) — small ledge
            glColor3f(*sill_c)
            sill_out = 0.06
            sill_h = 0.05
            glBegin(GL_QUADS)
            glNormal3f(0, 1, 0)
            glVertex3f(x0f, y0f, outer)
            glVertex3f(x1f, y0f, outer)
            glVertex3f(x1f, y0f, outer - sill_out * face_sign)
            glVertex3f(x0f, y0f, outer - sill_out * face_sign)
            glNormal3f(0, 0, n)
            glVertex3f(x0f, y0f - sill_h, outer - sill_out * face_sign)
            glVertex3f(x1f, y0f - sill_h, outer - sill_out * face_sign)
            glVertex3f(x1f, y0f, outer - sill_out * face_sign)
            glVertex3f(x0f, y0f, outer - sill_out * face_sign)
            glEnd()
        else:  # side (x axis); cy=y centre, cx=z offset, face_sign ±1 means side
            face_val = face_sign * w / 2
            outer = face_val + (-frame_out if face_sign < 0 else frame_out)
            inner = face_val + (-glass_out if face_sign < 0 else glass_out)
            z0, z1 = cx - win_w_ / 2, cx + win_w_ / 2
            z0f, z1f = z0 - win_frame_t, z1 + win_frame_t
            y0, y1 = cy - win_h_ / 2, cy + win_h_ / 2
            y0f, y1f = y0 - win_frame_t, y1 + win_frame_t
            n = -face_sign
            glColor3f(*frame_c)
            glBegin(GL_QUADS)
            glNormal3f(n, 0, 0)
            # top
            glVertex3f(outer, y1, z0f); glVertex3f(outer, y1, z1f)
            glVertex3f(outer, y1f, z1f); glVertex3f(outer, y1f, z0f)
            # bottom
            glVertex3f(outer, y0f, z0f); glVertex3f(outer, y0f, z1f)
            glVertex3f(outer, y0, z1f); glVertex3f(outer, y0, z0f)
            # left
            glVertex3f(outer, y0, z0f); glVertex3f(outer, y0, z0)
            glVertex3f(outer, y1, z0); glVertex3f(outer, y1, z0f)
            # right
            glVertex3f(outer, y0, z1); glVertex3f(outer, y0, z1f)
            glVertex3f(outer, y1, z1f); glVertex3f(outer, y1, z1)
            glEnd()
            glColor3f(*glass_c)
            glBegin(GL_QUADS)
            glVertex3f(inner, y0, z0); glVertex3f(inner, y0, z1)
            glVertex3f(inner, y1, z1); glVertex3f(inner, y1, z0)
            glColor3f(*frame_c)
            ym = (y0 + y1) / 2
            mt = 0.03
            glVertex3f(outer - 0.005 * face_sign, ym - mt, z0)
            glVertex3f(outer - 0.005 * face_sign, ym - mt, z1)
            glVertex3f(outer - 0.005 * face_sign, ym + mt, z1)
            glVertex3f(outer - 0.005 * face_sign, ym + mt, z0)
            glEnd()
            glColor3f(*sill_c)
            sill_out = 0.06
            sill_h = 0.05
            glBegin(GL_QUADS)
            glNormal3f(0, 1, 0)
            glVertex3f(outer, y0f, z0f)
            glVertex3f(outer, y0f, z1f)
            glVertex3f(outer - sill_out * face_sign, y0f, z1f)
            glVertex3f(outer - sill_out * face_sign, y0f, z0f)
            glNormal3f(n, 0, 0)
            glVertex3f(outer - sill_out * face_sign, y0f - sill_h, z0f)
            glVertex3f(outer - sill_out * face_sign, y0f - sill_h, z1f)
            glVertex3f(outer - sill_out * face_sign, y0f, z1f)
            glVertex3f(outer - sill_out * face_sign, y0f, z0f)
            glEnd()

    cy_win = win_y + win_h_ / 2
    for xoff in (-w * 0.28, w * 0.28):
        _window_on_face(xoff, cy_win, 'z', -1)  # front
        _window_on_face(xoff, cy_win, 'z', +1)  # back
    for zoff in (-d * 0.28, d * 0.28):
        _window_on_face(zoff, cy_win, 'x', -1)  # left
        _window_on_face(zoff, cy_win, 'x', +1)  # right

    glEnable(GL_TEXTURE_2D)
    glEndList()
    return list_id


def build_house_roof_list(w, d, wall_h, roof_h, eave=0.45):
    """Two slope quads + a gable-end overhang band.

    Eaves project outward ~0.45m past the wall plane on all four sides
    so the roof reads as sheltering the walls, not just capping them.
    Per the Musialski 2013 survey, eave overhang is the cheapest high-
    impact realism cue for low-rise houses. Texture bind is left to
    the caller so a snow pass can swap it.
    """
    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    apex_y = wall_h + roof_h
    # Slope extension: overhang widens the roof footprint along Z
    # (eave side) and adds a small vertical drop at the apex so the
    # overhang continues along the gable ends as well.
    w_eave = w + 2 * eave
    d_eave = d + 2 * eave
    # Slightly lower start so the eave drops below the wall top a touch
    eave_drop = 0.08
    slope_start_y = wall_h - eave_drop
    u_rep = d_eave / 2.5
    v_rep = math.sqrt((w_eave / 2) ** 2 + roof_h ** 2) / 2.5
    glBegin(GL_QUADS)
    # Left slope (-X side)
    glNormal3f(-roof_h, w_eave / 2, 0.0)
    glTexCoord2f(0, 0); glVertex3f(-w_eave / 2, slope_start_y, -d_eave / 2)
    glTexCoord2f(u_rep, 0); glVertex3f(-w_eave / 2, slope_start_y, d_eave / 2)
    glTexCoord2f(u_rep, v_rep); glVertex3f(0.0, apex_y, d_eave / 2)
    glTexCoord2f(0, v_rep); glVertex3f(0.0, apex_y, -d_eave / 2)
    # Right slope (+X side)
    glNormal3f(roof_h, w_eave / 2, 0.0)
    glTexCoord2f(0, 0); glVertex3f(0.0, apex_y, -d_eave / 2)
    glTexCoord2f(u_rep, 0); glVertex3f(0.0, apex_y, d_eave / 2)
    glTexCoord2f(u_rep, v_rep); glVertex3f(w_eave / 2, slope_start_y, d_eave / 2)
    glTexCoord2f(0, v_rep); glVertex3f(w_eave / 2, slope_start_y, -d_eave / 2)
    glEnd()
    glEndList()
    return list_id


def build_house_chimney_list(w, d, wall_h, roof_h):
    """A small brick-coloured chimney sitting on one slope, ~1/3 of the
    way from the ridge toward the eave on the +X side. Compiled as a
    separate list so the night emission pass can add a tiny warm glow
    at its top (embers / interior light spill)."""
    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    # Place chimney on the +X slope, roughly 1/3 from ridge toward eave
    cx = w * 0.22
    cz_off = d * 0.18  # offset toward +Z
    ridge_y = wall_h + roof_h
    # Linear interp of slope height at cx: at x=0 -> ridge_y, at x=w/2 -> wall_h
    t = cx / (w / 2)
    slope_y = ridge_y * (1 - t) + wall_h * t
    base_y = slope_y + 0.05   # sit just above slope
    top_y = ridge_y + 1.2      # extend above the ridge
    cw = 0.55
    cd = 0.55
    glDisable(GL_TEXTURE_2D)
    # brick red
    glColor3f(0.55, 0.33, 0.28)
    # 4 side faces
    for nx, nz, x0, z0, x1, z1 in (
        (0, -1, cx - cw/2, cz_off - cd/2, cx + cw/2, cz_off - cd/2),
        (0,  1, cx + cw/2, cz_off + cd/2, cx - cw/2, cz_off + cd/2),
        (-1, 0, cx - cw/2, cz_off + cd/2, cx - cw/2, cz_off - cd/2),
        ( 1, 0, cx + cw/2, cz_off - cd/2, cx + cw/2, cz_off + cd/2),
    ):
        glBegin(GL_QUADS)
        glNormal3f(nx, 0, nz)
        glVertex3f(x0, base_y, z0)
        glVertex3f(x1, base_y, z1)
        glVertex3f(x1, top_y, z1)
        glVertex3f(x0, top_y, z0)
        glEnd()
    # Cap (slightly wider)
    cap_out = 0.04
    cap_h = 0.08
    glColor3f(0.35, 0.32, 0.30)
    glBegin(GL_QUADS)
    glNormal3f(0, 1, 0)
    glVertex3f(cx - cw/2 - cap_out, top_y + cap_h, cz_off - cd/2 - cap_out)
    glVertex3f(cx + cw/2 + cap_out, top_y + cap_h, cz_off - cd/2 - cap_out)
    glVertex3f(cx + cw/2 + cap_out, top_y + cap_h, cz_off + cd/2 + cap_out)
    glVertex3f(cx - cw/2 - cap_out, top_y + cap_h, cz_off + cd/2 + cap_out)
    # cap sides
    for nx, nz, x0, z0, x1, z1 in (
        (0, -1, cx - cw/2 - cap_out, cz_off - cd/2 - cap_out,
                cx + cw/2 + cap_out, cz_off - cd/2 - cap_out),
        (0,  1, cx + cw/2 + cap_out, cz_off + cd/2 + cap_out,
                cx - cw/2 - cap_out, cz_off + cd/2 + cap_out),
        (-1, 0, cx - cw/2 - cap_out, cz_off + cd/2 + cap_out,
                cx - cw/2 - cap_out, cz_off - cd/2 - cap_out),
        ( 1, 0, cx + cw/2 + cap_out, cz_off - cd/2 - cap_out,
                cx + cw/2 + cap_out, cz_off + cd/2 + cap_out),
    ):
        glNormal3f(nx, 0, nz)
        glVertex3f(x0, top_y, z0)
        glVertex3f(x1, top_y, z1)
        glVertex3f(x1, top_y + cap_h, z1)
        glVertex3f(x0, top_y + cap_h, z0)
    # Dark opening on top (flue)
    glColor3f(0.08, 0.06, 0.05)
    glNormal3f(0, 1, 0)
    ho = 0.08
    glVertex3f(cx - ho, top_y + cap_h + 0.001, cz_off - ho)
    glVertex3f(cx + ho, top_y + cap_h + 0.001, cz_off - ho)
    glVertex3f(cx + ho, top_y + cap_h + 0.001, cz_off + ho)
    glVertex3f(cx - ho, top_y + cap_h + 0.001, cz_off + ho)
    glEnd()
    glEnable(GL_TEXTURE_2D)
    glEndList()
    return list_id


def build_house_emission_list(w, d, wall_h, lit_mask):
    """Compile an additive-blend emission pass for a house: only the windows
    and porch light that should glow after dark. Window positions match
    `build_house_body_list` exactly (same window_y / offsets) so the glow
    sits on top of the dark glass panes.

    `lit_mask` is a sequence of 8 booleans (front-left, front-right, back-
    left, back-right, left-front, left-back, right-front, right-back). The
    porch light over the door is always on at night — it's the single
    visual cue that a house is inhabited and drives most of the warm
    skyline glow in rural zones.
    """
    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    # Dimensions + plane must match build_house_body_list exactly
    # (win_w=0.95, win_h=1.05, glass_out=0.008). Emission sits on the
    # glass plane with a tiny extra offset toward the camera so the
    # additive pass is drawn on top, not z-fighting with the glass.
    win_w_ = 0.95
    win_h_ = 1.05
    win_y = wall_h * 0.55 - win_h_ / 2
    glass_out = 0.008
    z_off_front = glass_out + 0.002  # project OUT from -d/2
    z_off_back = glass_out + 0.002   # project OUT from +d/2
    x_off = glass_out + 0.002
    body_eps = glass_out  # for porch_y offset below
    glBegin(GL_QUADS)
    for i, xoff in enumerate((-w * 0.28, w * 0.28)):
        if not lit_mask[i]:
            continue
        z = -d / 2 - z_off_front
        glVertex3f(xoff - win_w_ / 2, win_y, z)
        glVertex3f(xoff + win_w_ / 2, win_y, z)
        glVertex3f(xoff + win_w_ / 2, win_y + win_h_, z)
        glVertex3f(xoff - win_w_ / 2, win_y + win_h_, z)
    for i, xoff in enumerate((-w * 0.28, w * 0.28)):
        if not lit_mask[2 + i]:
            continue
        z = d / 2 + z_off_back
        glVertex3f(xoff + win_w_ / 2, win_y, z)
        glVertex3f(xoff - win_w_ / 2, win_y, z)
        glVertex3f(xoff - win_w_ / 2, win_y + win_h_, z)
        glVertex3f(xoff + win_w_ / 2, win_y + win_h_, z)
    for i, zoff in enumerate((-d * 0.28, d * 0.28)):
        if not lit_mask[4 + i]:
            continue
        x = -w / 2 - x_off
        glVertex3f(x, win_y, zoff + win_w_ / 2)
        glVertex3f(x, win_y, zoff - win_w_ / 2)
        glVertex3f(x, win_y + win_h_, zoff - win_w_ / 2)
        glVertex3f(x, win_y + win_h_, zoff + win_w_ / 2)
    for i, zoff in enumerate((-d * 0.28, d * 0.28)):
        if not lit_mask[6 + i]:
            continue
        x = w / 2 + x_off
        glVertex3f(x, win_y, zoff - win_w_ / 2)
        glVertex3f(x, win_y, zoff + win_w_ / 2)
        glVertex3f(x, win_y + win_h_, zoff + win_w_ / 2)
        glVertex3f(x, win_y + win_h_, zoff - win_w_ / 2)
    glEnd()
    # Porch light: a small glowing disk above the door (front face).
    porch_r = 0.14
    porch_y = 2.15
    glBegin(GL_TRIANGLE_FAN)
    glVertex3f(0.0, porch_y, -d / 2 - body_eps * 1.2)
    for k in range(13):
        ang = 2.0 * math.pi * k / 12.0
        glVertex3f(math.cos(ang) * porch_r,
                   porch_y + math.sin(ang) * porch_r,
                   -d / 2 - body_eps * 1.2)
    glEnd()
    glEndList()
    return list_id


def build_house_variants(wall_textures, roof_textures, n=8):
    """Generate n variants mixing wall+roof texture combinations and
    randomising dimensions."""
    variants = []
    rng = random.Random(1607)
    for i in range(n):
        wall_tex = wall_textures[i % len(wall_textures)]
        roof_tex = roof_textures[(i // 2) % len(roof_textures)]
        w = rng.uniform(7.5, 11.0)
        d = rng.uniform(6.5, 9.0)
        wall_h = rng.uniform(*HOUSE_WALL_H_RANGE)
        roof_h = rng.uniform(*HOUSE_ROOF_H_RANGE)
        lit_mask = tuple(rng.random() < 0.60 for _ in range(8))
        variants.append({
            "body": build_house_body_list(wall_tex, w, d, wall_h, roof_h),
            "roof": build_house_roof_list(w, d, wall_h, roof_h),
            "chimney": build_house_chimney_list(w, d, wall_h, roof_h),
            "emission": build_house_emission_list(w, d, wall_h, lit_mask),
            "wall_tex": wall_tex,
            "roof_tex": roof_tex,
            "dims": (w, d, wall_h + roof_h),
        })
    return variants


def draw_houses(s_car, house_variants, snow_tex, amb_rgb, night_a=0.0):
    """Instance rural houses across flat-ground biomes, with a per-house
    roof snow pass scaled by the frost weight at that s."""
    s_start = math.floor(s_car / HOUSE_SPACING) * HOUSE_SPACING
    max_s = s_car + N_SEG * SEG_LEN
    n_steps = int((max_s - s_start) / HOUSE_SPACING) + 1
    nvar = len(house_variants)
    if nvar == 0:
        return

    # Precompute biome weights
    s_arr = np.arange(n_steps, dtype=np.float32) * HOUSE_SPACING + s_start

    glEnable(GL_TEXTURE_2D)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)

    for i in range(n_steps):
        s = float(s_arr[i])
        if s < s_car - 2.0:
            continue
        for side in (-1, +1):
            w_b = biome_weights_vec(
                np.array([s], dtype=np.float32), side)[0]
            # allowed: plain, forest, frost. Skip mountain/hill/river/city.
            ok_w = (w_b[BIOME_PLAIN] + w_b[BIOME_FOREST] + w_b[BIOME_FROST])
            bad_w = (w_b[BIOME_MOUNTAIN] + w_b[BIOME_HILL]
                     + w_b[BIOME_RIVER] + w_b[BIOME_CITY])
            if ok_w < 0.6 or bad_w > 0.3:
                continue
            key = (int(s * 23) * 2654435761
                   + (0 if side < 0 else 6491)
                   + 7919) & 0xFFFFFFFF
            if ((key & 0xFF) / 255.0) > 0.22:   # only ~22% of slots get a house
                continue
            d_off = HOUSE_MIN_D + ((key >> 8) & 0xFF) / 255.0 \
                    * (HOUSE_MAX_D - HOUSE_MIN_D)
            variant = ((key >> 16) & 0x0F) % nvar
            yaw = ((key >> 4) & 0x3F) / 63.0 * 360.0
            scale = 0.9 + ((key >> 20) & 0x0F) / 15.0 * 0.35

            tx = curve_x(s) + side * (ROAD_WIDTH / 2 + d_off)
            ty = curve_y(s) - 0.12
            tz = -(s - s_car)

            v = house_variants[variant]
            frost_w_here = float(w_b[BIOME_FROST])

            glPushMatrix()
            glTranslatef(tx, ty, tz)
            glRotatef(yaw, 0, 1, 0)
            glScalef(scale, scale, scale)

            # Body + roof at normal tint — the body list leaves glColor
            # as the last window's sill colour (it no longer resets to
            # white), so reassert the ambient tint before the roof pass.
            tint = (min(1.0, amb_rgb[0]),
                    min(1.0, amb_rgb[1]),
                    min(1.0, amb_rgb[2]))
            glColor3f(*tint)
            glCallList(v["body"])
            glColor3f(*tint)
            glBindTexture(GL_TEXTURE_2D, v["roof_tex"])
            glCallList(v["roof"])
            if "chimney" in v:
                glColor3f(*tint)
                glCallList(v["chimney"])

            # Progressive snow on the roof: fade in/out with frost biome
            if frost_w_here > 0.04:
                glBindTexture(GL_TEXTURE_2D, snow_tex)
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                glDepthMask(GL_FALSE)
                glEnable(GL_POLYGON_OFFSET_FILL)
                glPolygonOffset(-1.2, -1.2)
                a = min(1.0, frost_w_here * 0.95)
                glColor4f(min(1.0, amb_rgb[0] * 0.95),
                          min(1.0, amb_rgb[1] * 0.97),
                          min(1.0, amb_rgb[2] * 1.00),
                          a)
                glCallList(v["roof"])
                glDisable(GL_POLYGON_OFFSET_FILL)
                glDepthMask(GL_TRUE)
                glDisable(GL_BLEND)
                glColor3f(1.0, 1.0, 1.0)

            # Night emission: warm-tinted glow from windows + porch light.
            # GL_LEQUAL so equal-Z additive passes actually paint over
            # the just-drawn body (same trick as draw_city).
            if night_a > 0.03 and "emission" in v:
                glDisable(GL_TEXTURE_2D)
                glEnable(GL_BLEND)
                glBlendFunc(GL_ONE, GL_ONE)
                glDepthFunc(GL_LEQUAL)
                glDepthMask(GL_FALSE)
                glColor4f(min(1.0, night_a * 1.10),
                          min(1.0, night_a * 0.92),
                          min(1.0, night_a * 0.58),
                          1.0)
                glCallList(v["emission"])
                glDepthFunc(GL_LESS)
                glDepthMask(GL_TRUE)
                glDisable(GL_BLEND)
                glEnable(GL_TEXTURE_2D)
                glColor3f(1.0, 1.0, 1.0)

            glPopMatrix()


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


def make_facade_texture(size=512, cols=8, rows=16, seed=51,
                         palette="concrete"):
    """Base facade with a regular window grid.

    Adornment: a thin concrete ledge ("string course") between floors and
    a subtle vertical pilaster between window columns — small relief cues
    that keep the facade from reading as a flat grid up close, mirroring
    the layered-band motif common in Wonka 2003 / Müller 2006 split-grammar
    facades.

    `palette` selects one of several material bases so neighbouring
    buildings don't all read as the same bluish concrete. Windows always
    stay dark-blue glass so the night emission pass lands consistently
    across palettes.
    """
    rng = np.random.default_rng(seed)
    palettes = {
        "concrete": (np.array([62, 63, 72]), np.array([112, 112, 120]),
                     np.array([22, 22, 28])),
        "limestone": (np.array([165, 148, 120]), np.array([200, 188, 160]),
                      np.array([95, 82, 60])),
        "brick":     (np.array([120, 58, 48]),  np.array([170, 95, 78]),
                      np.array([55, 24, 20])),
        "glass":     (np.array([58, 78, 95]),   np.array([140, 160, 180]),
                      np.array([18, 26, 40])),
        "sandstone": (np.array([145, 125, 95]), np.array([195, 175, 140]),
                      np.array([85, 68, 48])),
    }
    base, hi, sh = palettes.get(palette, palettes["concrete"])
    rgb = np.broadcast_to(base, (size, size, 3)).astype(np.int16).copy()
    noise = rng.integers(-10, 10, (size, size, 1), dtype=np.int16)
    rgb = np.clip(rgb + noise, 18, 230).astype(np.uint8)

    cw = size / cols
    rh = size / rows
    # Horizontal ledge between floors — two-pixel bright over one-pixel shadow
    for r in range(1, rows):
        y = int(r * rh)
        if 0 < y < size - 2:
            rgb[y - 1:y + 1, :] = (105, 108, 116)
            rgb[y + 1:y + 2, :] = (38, 40, 46)
    # Vertical pilaster between columns
    for c in range(1, cols):
        x = int(c * cw)
        if 0 < x < size - 1:
            rgb[:, x:x + 1] = (82, 84, 92)
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
    # Ground-floor storefront band: widen + brighten the bottom row to hint
    # at shop windows / lobby glazing
    y0 = int(rh * 0.10)
    y1 = int(rh * 0.92)
    for c in range(cols):
        x0 = int(c * cw + cw * 0.08)
        x1 = int(c * cw + cw * 0.92)
        rgb[y0:y1, x0:x1] = (34, 40, 52)
        rgb[y0:y0 + 1, x0:x1] = (130, 130, 138)
    return rgb


def make_facade_emission_texture(size=512, cols=8, rows=16, seed=73):
    """Sparse lit windows on fully-transparent background. Same UV layout
    as the base facade so lights land exactly on window panes.
    ~45% of windows lit, each with slight warm-color jitter.

    Bright ground-floor storefronts are lit more often (~80%) and cooler
    (fluorescent-leaning), so the base of the building reads like street-
    level retail at night while upper floors are warmer residential/office
    mixes. A faint halo ring around each lit window simulates glass bloom
    under additive blending."""
    rng = np.random.default_rng(seed)
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    cw = size / cols
    rh = size / rows
    for c in range(cols):
        for r in range(rows):
            ground = (r == 0)
            p_lit = 0.80 if ground else 0.45
            if rng.random() > p_lit:
                continue
            x0 = int(c * cw + cw * 0.18)
            x1 = int(c * cw + cw * 0.82)
            y0 = int(r * rh + rh * 0.20)
            y1 = int(r * rh + rh * 0.78)
            if ground:
                # cool fluorescent storefront
                warm = float(rng.uniform(0.85, 1.0))
                rr = int(np.clip(210 * warm, 40, 255))
                gg = int(np.clip(230 * warm, 40, 255))
                bb = int(np.clip(255 * warm, 40, 255))
            else:
                warm = float(rng.uniform(0.80, 1.0))
                hue_shift = float(rng.uniform(-0.10, 0.15))
                rr = int(np.clip(255 * warm, 40, 255))
                gg = int(np.clip(225 * warm * (1.0 - hue_shift * 0.4), 40, 255))
                bb = int(np.clip(150 * warm * (1.0 - hue_shift), 20, 255))
            rgba[y0:y1, x0:x1, 0] = rr
            rgba[y0:y1, x0:x1, 1] = gg
            rgba[y0:y1, x0:x1, 2] = bb
            rgba[y0:y1, x0:x1, 3] = 255
            # halo: 1-pixel faint ring around the pane for additive bloom
            hy0 = max(0, y0 - 2); hy1 = min(size, y1 + 2)
            hx0 = max(0, x0 - 2); hx1 = min(size, x1 + 2)
            halo_patch = rgba[hy0:hy1, hx0:hx1]
            halo_mask = halo_patch[..., 3] == 0
            halo_patch[..., 0] = np.where(halo_mask, rr // 3, halo_patch[..., 0])
            halo_patch[..., 1] = np.where(halo_mask, gg // 3, halo_patch[..., 1])
            halo_patch[..., 2] = np.where(halo_mask, bb // 3, halo_patch[..., 2])
            halo_patch[..., 3] = np.where(halo_mask, 90, halo_patch[..., 3])
            rgba[hy0:hy1, hx0:hx1] = halo_patch
    return rgba


FACADE_PALETTES = ("concrete", "limestone", "brick", "glass", "sandstone")


def build_building_variant(seed, palette_count=len(FACADE_PALETTES)):
    """Compile one building into a display list and return metadata.

    Geometry: 4 textured facade quads, a textured rooftop cap, a short
    parapet wall ringing the roof, and a small rooftop mechanical box +
    antenna mast. The parapet hides the flat-cap seam and gives the
    silhouette a more varied skyline than a perfectly flat prism —
    matching the setback/crown motifs called out in Schwarz & Müller
    2015 as high-value detail for procedural towers.

    UV repetition is baked so windows tile consistently with real-world
    dimensions: one window per WINDOW_H_M horizontally, one floor per
    FLOOR_H_M vertically. The base matches the number of window *columns*
    in the facade texture (8) so a complete grid fits every `cols` windows.
    """
    rng = random.Random(seed)
    w = rng.uniform(7.0, 14.0)
    d = rng.uniform(7.0, 14.0)
    h = rng.uniform(22.0, 78.0)
    parapet_h = rng.uniform(0.8, 1.6)
    # Rooftop antenna is taller on taller buildings; small machine-room
    # box is offset to one quadrant for asymmetric silhouette.
    antenna_h = rng.uniform(3.0, 8.0) * (0.6 + 0.4 * (h / 78.0))
    mech_w = rng.uniform(2.0, 4.0)
    mech_d = rng.uniform(2.0, 4.0)
    mech_h = rng.uniform(1.6, 2.8)
    mech_ox = rng.uniform(-w * 0.2, w * 0.2)
    mech_oz = rng.uniform(-d * 0.2, d * 0.2)
    palette_idx = rng.randrange(palette_count)

    u_front = (w / WINDOW_H_M) / 8.0
    u_side = (d / WINDOW_H_M) / 8.0
    v_up = (h / FLOOR_H_M) / 16.0

    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    glBegin(GL_QUADS)
    # -Z face (front)
    glNormal3f(0.0, 0.0, -1.0)
    glTexCoord2f(0, 0); glVertex3f(-w / 2, 0, -d / 2)
    glTexCoord2f(u_front, 0); glVertex3f(w / 2, 0, -d / 2)
    glTexCoord2f(u_front, v_up); glVertex3f(w / 2, h, -d / 2)
    glTexCoord2f(0, v_up); glVertex3f(-w / 2, h, -d / 2)
    # +Z face (back)
    glNormal3f(0.0, 0.0, 1.0)
    glTexCoord2f(0, 0); glVertex3f(w / 2, 0, d / 2)
    glTexCoord2f(u_front, 0); glVertex3f(-w / 2, 0, d / 2)
    glTexCoord2f(u_front, v_up); glVertex3f(-w / 2, h, d / 2)
    glTexCoord2f(0, v_up); glVertex3f(w / 2, h, d / 2)
    # -X face (left)
    glNormal3f(-1.0, 0.0, 0.0)
    glTexCoord2f(0, 0); glVertex3f(-w / 2, 0, d / 2)
    glTexCoord2f(u_side, 0); glVertex3f(-w / 2, 0, -d / 2)
    glTexCoord2f(u_side, v_up); glVertex3f(-w / 2, h, -d / 2)
    glTexCoord2f(0, v_up); glVertex3f(-w / 2, h, d / 2)
    # +X face (right)
    glNormal3f(1.0, 0.0, 0.0)
    glTexCoord2f(0, 0); glVertex3f(w / 2, 0, -d / 2)
    glTexCoord2f(u_side, 0); glVertex3f(w / 2, 0, d / 2)
    glTexCoord2f(u_side, v_up); glVertex3f(w / 2, h, d / 2)
    glTexCoord2f(0, v_up); glVertex3f(w / 2, h, -d / 2)
    # Rooftop cap (under the parapet, structural) — neutral texel so no
    # windows appear top-down.
    glNormal3f(0.0, 1.0, 0.0)
    glTexCoord2f(0.02, 0.02); glVertex3f(-w / 2, h, -d / 2)
    glTexCoord2f(0.03, 0.02); glVertex3f(w / 2, h, -d / 2)
    glTexCoord2f(0.03, 0.03); glVertex3f(w / 2, h, d / 2)
    glTexCoord2f(0.02, 0.03); glVertex3f(-w / 2, h, d / 2)
    glEnd()

    # --- Parapet: outer + inner + cap strips. Sampled from a neutral
    # (unwindowed) texel of the facade texture so it reads as solid
    # concrete/limestone/etc. matching the main wall.
    pw = 0.25  # parapet wall thickness
    pt = h + parapet_h  # parapet top y
    glBegin(GL_QUADS)
    # Outer four faces
    for nx, nz, x0, z0, x1, z1 in (
        (0.0, -1.0, -w / 2, -d / 2, w / 2, -d / 2),
        (0.0,  1.0,  w / 2,  d / 2, -w / 2, d / 2),
        (-1.0, 0.0, -w / 2,  d / 2, -w / 2, -d / 2),
        (1.0,  0.0,  w / 2, -d / 2,  w / 2,  d / 2),
    ):
        glNormal3f(nx, 0.0, nz)
        glTexCoord2f(0.02, 0.50); glVertex3f(x0, h, z0)
        glTexCoord2f(0.03, 0.50); glVertex3f(x1, h, z1)
        glTexCoord2f(0.03, 0.51); glVertex3f(x1, pt, z1)
        glTexCoord2f(0.02, 0.51); glVertex3f(x0, pt, z0)
    # Cap (top of parapet, ring)
    glNormal3f(0.0, 1.0, 0.0)
    for (x0, z0, x1, z1) in (
        (-w / 2, -d / 2, w / 2, -d / 2 + pw),
        (-w / 2, d / 2 - pw, w / 2, d / 2),
        (-w / 2, -d / 2 + pw, -w / 2 + pw, d / 2 - pw),
        (w / 2 - pw, -d / 2 + pw, w / 2, d / 2 - pw),
    ):
        glTexCoord2f(0.02, 0.02); glVertex3f(x0, pt, z0)
        glTexCoord2f(0.03, 0.02); glVertex3f(x1, pt, z0)
        glTexCoord2f(0.03, 0.03); glVertex3f(x1, pt, z1)
        glTexCoord2f(0.02, 0.03); glVertex3f(x0, pt, z1)

    # --- Rooftop mechanical box
    mx0, mx1 = mech_ox - mech_w / 2, mech_ox + mech_w / 2
    mz0, mz1 = mech_oz - mech_d / 2, mech_oz + mech_d / 2
    my0, my1 = h, h + mech_h
    # 4 sides + top
    for nx, nz, x0, z0, x1, z1 in (
        (0.0, -1.0, mx0, mz0, mx1, mz0),
        (0.0,  1.0, mx1, mz1, mx0, mz1),
        (-1.0, 0.0, mx0, mz1, mx0, mz0),
        (1.0,  0.0, mx1, mz0, mx1, mz1),
    ):
        glNormal3f(nx, 0.0, nz)
        glTexCoord2f(0.02, 0.02); glVertex3f(x0, my0, z0)
        glTexCoord2f(0.03, 0.02); glVertex3f(x1, my0, z1)
        glTexCoord2f(0.03, 0.03); glVertex3f(x1, my1, z1)
        glTexCoord2f(0.02, 0.03); glVertex3f(x0, my1, z0)
    glNormal3f(0.0, 1.0, 0.0)
    glTexCoord2f(0.02, 0.02); glVertex3f(mx0, my1, mz0)
    glTexCoord2f(0.03, 0.02); glVertex3f(mx1, my1, mz0)
    glTexCoord2f(0.03, 0.03); glVertex3f(mx1, my1, mz1)
    glTexCoord2f(0.02, 0.03); glVertex3f(mx0, my1, mz1)
    glEnd()

    # --- Antenna mast: thin cross of two quads (no need for cylinder, it
    # reads as a silhouette pin against the sky)
    ax = mech_ox
    az = mech_oz
    ay0 = my1
    ay1 = ay0 + antenna_h
    ar = 0.06
    glBegin(GL_QUADS)
    glNormal3f(0.0, 0.0, -1.0)
    glTexCoord2f(0.02, 0.02); glVertex3f(ax - ar, ay0, az)
    glTexCoord2f(0.03, 0.02); glVertex3f(ax + ar, ay0, az)
    glTexCoord2f(0.03, 0.03); glVertex3f(ax + ar, ay1, az)
    glTexCoord2f(0.02, 0.03); glVertex3f(ax - ar, ay1, az)
    glNormal3f(-1.0, 0.0, 0.0)
    glTexCoord2f(0.02, 0.02); glVertex3f(ax, ay0, az - ar)
    glTexCoord2f(0.03, 0.02); glVertex3f(ax, ay0, az + ar)
    glTexCoord2f(0.03, 0.03); glVertex3f(ax, ay1, az + ar)
    glTexCoord2f(0.02, 0.03); glVertex3f(ax, ay1, az - ar)
    glEnd()

    glEndList()

    # --- Snow accumulation list: ONLY the upward-facing roof surfaces
    # (rooftop cap, parapet cap strips, mech box top). Drawn in a
    # separate pass with the snow ground texture + alpha scaled by
    # frost_intensity so accumulation fades in and out with the biome.
    snow_list = glGenLists(1)
    glNewList(snow_list, GL_COMPILE)
    glBegin(GL_QUADS)
    glNormal3f(0, 1, 0)
    # Rooftop cap (centre of roof, below the parapet interior)
    sh_eps = 0.02  # small vertical offset so snow sits ON top, not z-fighting
    # Full roof square plus parapet rim: easier to paint the whole roof
    # area at y=pt (parapet top) — this includes the parapet cap and the
    # inside of the roof is covered by the cap band itself.
    glTexCoord2f(0, 0); glVertex3f(-w / 2, pt + sh_eps, -d / 2)
    glTexCoord2f(w / 2.5, 0); glVertex3f(w / 2, pt + sh_eps, -d / 2)
    glTexCoord2f(w / 2.5, d / 2.5); glVertex3f(w / 2, pt + sh_eps, d / 2)
    glTexCoord2f(0, d / 2.5); glVertex3f(-w / 2, pt + sh_eps, d / 2)
    # Mech box top
    glTexCoord2f(0, 0); glVertex3f(mx0, my1 + sh_eps, mz0)
    glTexCoord2f(mech_w / 2.5, 0); glVertex3f(mx1, my1 + sh_eps, mz0)
    glTexCoord2f(mech_w / 2.5, mech_d / 2.5); glVertex3f(mx1, my1 + sh_eps, mz1)
    glTexCoord2f(0, mech_d / 2.5); glVertex3f(mx0, my1 + sh_eps, mz1)
    glEnd()
    glEndList()

    return {
        "list": list_id,
        "snow_list": snow_list,
        "dims": (w, h + parapet_h + mech_h + antenna_h, d),
        "body_dims": (w, h, d),
        "palette": palette_idx,
        # Rooftop beacon at the top of the antenna (drawn per-frame so it
        # can blink on the night emission pass).
        "beacon": (ax, ay1, az),
    }


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


def draw_city(s_car, building_lists, facade_texes, emission_tex,
              amb_rgb, night_a, t_time=0.0, storm_i=0.0, frost_i=0.0,
              snow_tex=None):
    """Two-pass render: tinted facade, then additive emission at night.

    `facade_texes` is a list of GL texture ids (one per palette) so
    neighbouring buildings vary in material — the per-variant palette
    index selects which one is bound before its display list is called.
    """
    slots = list(_iter_city_slots(s_car))
    if not slots:
        return
    # Accept either a single tex id (legacy) or a list/tuple of them.
    if isinstance(facade_texes, (int, np.integer)):
        facade_texes = [facade_texes]

    glEnable(GL_TEXTURE_2D)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)

    # Enable fixed-function lighting for the facade pass so each face
    # picks up different brightness from the baked normals. Without this
    # all four walls shade identically and buildings look flat/paper-like.
    # Rain darkens the facade (wet-stone look, ~0.80x); frost biomes
    # brighten it slightly because snow-lit ambient bounces light back
    # onto the walls.
    wet = 1.0 - 0.20 * storm_i
    snow_bounce = 1.0 + 0.08 * frost_i
    tint = (min(1.0, amb_rgb[0] * 0.85 * wet * snow_bounce),
            min(1.0, amb_rgb[1] * 0.85 * wet * snow_bounce),
            min(1.0, amb_rgb[2] * 0.90 * wet * snow_bounce))
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
        v = building_lists[variant]
        list_id = v["list"]
        pal = v.get("palette", 0) % len(facade_texes)
        glBindTexture(GL_TEXTURE_2D, facade_texes[pal])
        glPushMatrix()
        glTranslatef(tx, ty, tz)
        glRotatef(yaw, 0, 1, 0)
        glCallList(list_id)
        glPopMatrix()

    glDisable(GL_LIGHTING)
    glDisable(GL_COLOR_MATERIAL)

    # --- Rooftop snow pass: an alpha-blended white-ish cap on every
    # upward-facing roof surface, scaled by frost_i so snow fades in
    # and melts out as the biome transitions.
    if frost_i > 0.04 and snow_tex is not None:
        glBindTexture(GL_TEXTURE_2D, snow_tex)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDepthMask(GL_FALSE)
        alpha = min(1.0, frost_i * 0.95)
        glColor4f(min(1.0, amb_rgb[0] * 0.95),
                  min(1.0, amb_rgb[1] * 0.98),
                  min(1.0, amb_rgb[2] * 1.02), alpha)
        for s, side, key, tx, ty, tz, yaw, variant in slots:
            v = building_lists[variant]
            if "snow_list" not in v:
                continue
            glPushMatrix()
            glTranslatef(tx, ty, tz)
            glRotatef(yaw, 0, 1, 0)
            glCallList(v["snow_list"])
            glPopMatrix()
        glDepthMask(GL_TRUE)
        glDisable(GL_BLEND)
        glColor3f(1, 1, 1)

    if night_a < 0.03:
        return

    # Emission pass: straight additive (GL_ONE, GL_ONE) over the
    # transparent-background emission texture. Transparent texels have
    # rgb=(0,0,0) so they add nothing; lit texels contribute their
    # bright colour directly. Fog is disabled for this pass so distant
    # windows don't wash into the horizon — night-time skyscraper lights
    # are supposed to punch through atmospheric haze.
    glBindTexture(GL_TEXTURE_2D, emission_tex)
    glEnable(GL_BLEND)
    glBlendFunc(GL_ONE, GL_ONE)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_REPLACE)
    glDisable(GL_FOG)
    glDepthMask(GL_FALSE)
    # GL_LEQUAL so equal-Z emission fragments pass the depth test that
    # the base pass wrote — otherwise GL_LESS rejects every emission
    # fragment at exactly the facade depth and the city turns invisible
    # at night.
    glDepthFunc(GL_LEQUAL)
    # Run the emission twice: the additive operation saturates windows but
    # also lifts the surrounding halo → gives the "bloom" feel without a
    # shader-based blur. Second pass uses the same texture & blend.
    for _pass in range(2):
        glColor4f(min(1.0, night_a * 1.6),
                  min(1.0, night_a * 1.45),
                  min(1.0, night_a * 1.10),
                  1.0)
        for s, side, key, tx, ty, tz, yaw, variant in slots:
            v = building_lists[variant]
            list_id = v["list"]
            glPushMatrix()
            glTranslatef(tx, ty, tz)
            glRotatef(yaw, 0, 1, 0)
            glCallList(list_id)
            glPopMatrix()

    # Rooftop beacons: red blinking aviation warning light on each
    # building's antenna, sync'd to a slow pulse. Sprites are drawn as
    # simple additive quads since they're a single pixel's worth of
    # emphasis on the skyline.
    beacon_pulse = 0.5 + 0.5 * math.sin(t_time * 2.6)
    glDisable(GL_TEXTURE_2D)
    glColor4f(min(1.0, 1.0 * beacon_pulse * night_a),
              min(1.0, 0.15 * beacon_pulse * night_a),
              min(1.0, 0.10 * beacon_pulse * night_a),
              1.0)
    for s, side, key, tx, ty, tz, yaw, variant in slots:
        v = building_lists[variant]
        bx, by, bz = v["beacon"]
        glPushMatrix()
        glTranslatef(tx, ty, tz)
        glRotatef(yaw, 0, 1, 0)
        glTranslatef(bx, by + 0.25, bz)
        # Two crossed billboards so the beacon reads from any angle.
        size = 0.55
        glBegin(GL_QUADS)
        for rot in (0.0, 90.0):
            c = math.cos(math.radians(rot))
            s2 = math.sin(math.radians(rot))
            glVertex3f(-size * c, -size, -size * s2)
            glVertex3f(size * c, -size, size * s2)
            glVertex3f(size * c, size, size * s2)
            glVertex3f(-size * c, size, -size * s2)
        glEnd()
        glPopMatrix()
    glEnable(GL_TEXTURE_2D)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)

    glDepthFunc(GL_LESS)
    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)
    glEnable(GL_FOG)


# --- Procedural cars ---
# Technique ported from the sibling cars/ project: a lower body built from
# lofted superellipse cross-sections along the length axis (squircle
# stations), a narrower tumblehome cabin extruded on top, quadric wheels
# with brake disc + chrome hub + radial spokes, and parametric sockets for
# mirrors / spoiler / grille / head- and taillights. Paint uses a procedural
# metallic texture (gradient + noise + sparkle pixels) plus Blinn-Phong
# high-shininess specular for a clearcoat look. Each variant is baked into
# a single display list at startup; the live scene places a pool of them on
# two lanes — player's left lane travels same direction as the player
# (cars drift away), right lane is oncoming (cars close toward the camera).
CAR_LANE_HALF = 2.5
N_CAR_VARIANTS = 18
N_CARS_PER_LANE = 5
# Same-direction (player's left) cars enter from behind the camera, pass
# through, and recede into the distance. So their spawn range is *behind*
# s_car, and they're despawned only when they drift off the far horizon.
CAR_SPAWN_BEHIND_MIN = 30.0    # metres behind s_car (min)
CAR_SPAWN_BEHIND_MAX = 260.0   # metres behind s_car (max)
# Oncoming (right lane) cars enter from far ahead and close on the camera,
# despawning just after they pass.
CAR_SPAWN_AHEAD_MIN = 120.0
CAR_SPAWN_AHEAD_MAX = 780.0
CAR_DESPAWN_BEHIND = 35.0      # oncoming: how far past the camera before respawn
CAR_DESPAWN_AHEAD = 840.0      # same-dir:  how far ahead before respawn
CAR_SPEED_ONCOMING_MIN = 20.0
CAR_SPEED_ONCOMING_MAX = 34.0
# Same-direction cars must always outrun the camera so they pull ahead
# and visibly "go further" rather than being overtaken and popping out
# of the visible window. The speed is clamped to player_speed + this
# margin every frame inside update_cars, with a floor so stopped players
# still see traffic move.
CAR_SPEED_AWAY_OVER_MIN = 4.0
CAR_SPEED_AWAY_OVER_MAX = 12.0
CAR_SPEED_AWAY_FLOOR = 22.0

CAR_PALETTE = [
    (0.85, 0.10, 0.10), (0.10, 0.22, 0.70), (0.93, 0.93, 0.95),
    (0.07, 0.07, 0.09), (0.18, 0.18, 0.22), (0.72, 0.72, 0.74),
    (0.20, 0.55, 0.28), (0.92, 0.76, 0.12), (0.32, 0.24, 0.56),
    (0.75, 0.38, 0.08), (0.08, 0.50, 0.55), (0.62, 0.08, 0.22),
    (0.12, 0.35, 0.60), (0.55, 0.62, 0.70), (0.88, 0.84, 0.78),
    (0.40, 0.10, 0.12), (0.14, 0.48, 0.38), (0.98, 0.58, 0.30),
    (0.25, 0.30, 0.38), (0.65, 0.70, 0.22),
]


def _car_mat(amb, dif, spec, shine, emit=(0.0, 0.0, 0.0)):
    glMaterialfv(GL_FRONT_AND_BACK, GL_AMBIENT,
                 (amb[0], amb[1], amb[2], 1.0))
    glMaterialfv(GL_FRONT_AND_BACK, GL_DIFFUSE,
                 (dif[0], dif[1], dif[2], 1.0))
    glMaterialfv(GL_FRONT_AND_BACK, GL_SPECULAR,
                 (spec[0], spec[1], spec[2], 1.0))
    glMaterialf(GL_FRONT_AND_BACK, GL_SHININESS, shine)
    glMaterialfv(GL_FRONT_AND_BACK, GL_EMISSION,
                 (emit[0], emit[1], emit[2], 1.0))


def _car_mat_paint(c):
    _car_mat((c[0] * 0.55, c[1] * 0.55, c[2] * 0.55),
             (min(1.0, c[0] * 1.1), min(1.0, c[1] * 1.1),
              min(1.0, c[2] * 1.1)),
             (1.0, 1.0, 1.0), 110.0)


def _car_mat_glass():
    _car_mat((0.05, 0.06, 0.08), (0.09, 0.12, 0.18),
             (1.0, 1.0, 1.0), 128.0)


def _car_mat_tire():
    _car_mat((0.05, 0.05, 0.05), (0.09, 0.09, 0.09),
             (0.25, 0.25, 0.25), 8.0)


def _car_mat_rim():
    _car_mat((0.30, 0.30, 0.32), (0.80, 0.80, 0.84),
             (1.0, 1.0, 1.0), 110.0)


def _car_mat_hub():
    _car_mat((0.08, 0.08, 0.09), (0.18, 0.18, 0.20),
             (0.6, 0.6, 0.6), 50.0)


def _car_mat_brake():
    _car_mat((0.14, 0.14, 0.16), (0.30, 0.30, 0.34),
             (0.6, 0.6, 0.65), 32.0)


def _car_mat_dark():
    _car_mat((0.04, 0.04, 0.04), (0.08, 0.08, 0.08),
             (0.0, 0.0, 0.0), 1.0)


def _car_mat_grille():
    _car_mat((0.04, 0.04, 0.04), (0.08, 0.08, 0.08),
             (0.4, 0.4, 0.4), 20.0)


def _car_mat_chrome():
    _car_mat((0.30, 0.30, 0.33), (0.90, 0.90, 0.95),
             (1.0, 1.0, 1.0), 128.0)


def _car_mat_emit(c):
    _car_mat((c[0] * 0.55, c[1] * 0.55, c[2] * 0.55),
             (c[0], c[1], c[2]), (0.95, 0.95, 0.95), 60.0, emit=c)


def _car_mat_clear():
    glMaterialfv(GL_FRONT_AND_BACK, GL_EMISSION, (0.0, 0.0, 0.0, 1.0))


def make_car_paint_texture(base_color, rng, size=128):
    noise = rng.uniform(-0.035, 0.035, (size, size))
    grad = np.linspace(1.05, 0.82, size).reshape(-1, 1)
    tex = np.zeros((size, size, 3), dtype=np.float32)
    for ch in range(3):
        tex[:, :, ch] = np.clip(base_color[ch] * grad + noise, 0, 1)
    sparkle = rng.random((size, size)) > 0.993
    tex[sparkle] = np.clip(tex[sparkle] + 0.35, 0, 1)
    img = (tex * 255).astype(np.uint8)
    return upload_texture(img)


def _car_superellipse_section(z_half, y_bot, y_top, exp_top, exp_bot, K=36):
    y_c = 0.5 * (y_bot + y_top)
    y_half = 0.5 * (y_top - y_bot)
    pts = []
    for i in range(K):
        t = 2.0 * math.pi * i / K
        c = math.cos(t)
        s = math.sin(t)
        e = exp_top if s >= 0 else exp_bot
        inv = 2.0 / e
        z = math.copysign(abs(c) ** inv, c) * z_half
        y = y_c + math.copysign(abs(s) ** inv, s) * y_half
        pts.append((z, y))
    return pts


def _generate_car_params(rng):
    styles = ['sedan', 'coupe', 'suv', 'hatch', 'sport']
    style = styles[int(rng.integers(0, len(styles)))]
    L = float(rng.uniform(4.1, 4.8))
    W = float(rng.uniform(1.72, 1.92))
    WR = float(rng.uniform(0.34, 0.40))
    LBH = float(rng.uniform(0.48, 0.60))
    HL = float(rng.uniform(0.95, 1.25))
    TL = float(rng.uniform(0.60, 0.95))
    CH = float(rng.uniform(0.56, 0.70))
    WS = float(rng.uniform(0.30, 0.50))
    RW = float(rng.uniform(0.25, 0.40))
    spoiler = False
    spoke_count = int(rng.choice([5, 6, 7, 8, 10]))

    if style == 'suv':
        LBH = float(rng.uniform(0.68, 0.82))
        WR = float(rng.uniform(0.40, 0.46))
        CH = float(rng.uniform(0.78, 0.95))
        L = float(rng.uniform(4.3, 4.95))
    elif style == 'coupe':
        RW = float(rng.uniform(0.55, 0.75))
        CH = float(rng.uniform(0.52, 0.62))
        TL = float(rng.uniform(0.50, 0.75))
        spoiler = rng.random() < 0.35
    elif style == 'hatch':
        TL = float(rng.uniform(0.25, 0.45))
        L = float(rng.uniform(3.8, 4.2))
    elif style == 'sport':
        LBH = float(rng.uniform(0.42, 0.52))
        CH = float(rng.uniform(0.44, 0.56))
        WS = float(rng.uniform(0.45, 0.65))
        RW = float(rng.uniform(0.50, 0.70))
        WR = float(rng.uniform(0.35, 0.40))
        spoiler = rng.random() < 0.75

    BH = WR * 0.85
    body_top_y = BH + LBH
    roof_y = body_top_y + CH

    rear_x = -L / 2
    front_x = L / 2
    cabin_rear_bot = rear_x + TL
    cabin_rear_top = cabin_rear_bot + RW
    cabin_front_bot = front_x - HL
    cabin_front_top = cabin_front_bot - WS
    if cabin_front_top <= cabin_rear_top + 0.45:
        cabin_front_top = cabin_rear_top + 0.55

    W_cabin = W * float(rng.uniform(0.82, 0.90))
    color = CAR_PALETTE[int(rng.integers(0, len(CAR_PALETTE)))]

    zh = W / 2
    station_defs = [
        (rear_x,           0.46, BH * 0.55, body_top_y * 0.55, 3.0, 3.2),
        (rear_x + 0.18,    0.88, BH * 0.50, body_top_y * 0.85, 4.0, 4.5),
        (rear_x + 0.45,    0.98, BH * 0.45, body_top_y,        6.0, 6.0),
        (cabin_rear_bot,   1.00, BH * 0.45, body_top_y,        6.5, 6.5),
        ((cabin_rear_bot + cabin_front_bot) / 2,
                           1.00, BH * 0.42, body_top_y,        7.0, 6.5),
        (cabin_front_bot,  1.00, BH * 0.45, body_top_y,        6.5, 6.5),
        (cabin_front_bot + 0.30,
                           0.98, BH * 0.48, body_top_y * 0.97, 5.5, 5.5),
        (front_x - 0.35,   0.92, BH * 0.52, body_top_y * 0.82, 4.2, 4.5),
        (front_x,          0.48, BH * 0.55, body_top_y * 0.58, 3.0, 3.2),
    ]
    K = 36
    stations = []
    for (x, z_scale, y_bot, y_top, et, eb) in station_defs:
        pts2d = _car_superellipse_section(zh * z_scale, y_bot, y_top,
                                          et, eb, K)
        stations.append({'x': x, 'pts': [(x, y, z) for (z, y) in pts2d]})

    cabin = [
        (cabin_rear_bot,  body_top_y),
        (cabin_rear_top,  roof_y),
        (cabin_front_top, roof_y),
        (cabin_front_bot, body_top_y),
    ]

    rear_wx = rear_x + WR + float(rng.uniform(0.20, 0.32))
    front_wx = front_x - WR - float(rng.uniform(0.20, 0.32))
    inset = 0.04
    wheels = [
        (rear_wx,  WR,  W / 2 - inset),
        (rear_wx,  WR, -W / 2 + inset),
        (front_wx, WR,  W / 2 - inset),
        (front_wx, WR, -W / 2 + inset),
    ]

    return {
        'style': style, 'L': L, 'W': W, 'W_cabin': W_cabin, 'WR': WR,
        'stations': stations, 'K': K, 'cabin': cabin, 'wheels': wheels,
        'color': color, 'spoiler': spoiler, 'spoke_count': spoke_count,
        'body_top_y': body_top_y, 'roof_y': roof_y,
        'cabin_rear_bot': cabin_rear_bot,
        'cabin_front_bot': cabin_front_bot,
        'cabin_rear_top': cabin_rear_top,
        'cabin_front_top': cabin_front_top,
        'rear_x': rear_x, 'front_x': front_x, 'BH': BH,
    }


def _car_station_center(s):
    n = len(s['pts'])
    cx = sum(p[0] for p in s['pts']) / n
    cy = sum(p[1] for p in s['pts']) / n
    cz = sum(p[2] for p in s['pts']) / n
    return (cx, cy, cz)


def _car_draw_lower_body(car, paint_tex):
    stations = car['stations']
    K = car['K']
    N = len(stations)

    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, paint_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    _car_mat_paint(car['color'])

    centers = [_car_station_center(s) for s in stations]

    def normal_for(si, pi):
        p = stations[si]['pts'][pi]
        _, cy, cz = centers[si]
        ny = p[1] - cy
        nz = p[2] - cz
        r_here = math.hypot(ny, nz)
        if si == 0:
            nx = -0.8 * r_here
        elif si == N - 1:
            nx = 0.8 * r_here
        else:
            p_prev = stations[si - 1]['pts'][pi]
            cp = centers[si - 1]
            r_prev = math.hypot(p_prev[1] - cp[1], p_prev[2] - cp[2])
            p_next = stations[si + 1]['pts'][pi]
            cn = centers[si + 1]
            r_next = math.hypot(p_next[1] - cn[1], p_next[2] - cn[2])
            nx = (r_prev - r_next) * 0.8
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) + 1e-9
        return (nx / ln, ny / ln, nz / ln)

    for si in range(N - 1):
        s0 = stations[si]['pts']
        s1 = stations[si + 1]['pts']
        glBegin(GL_QUAD_STRIP)
        for i in range(K + 1):
            idx = i % K
            n0 = normal_for(si, idx)
            n1 = normal_for(si + 1, idx)
            p0 = s0[idx]
            p1 = s1[idx]
            u0 = (p0[0] + car['L']) / (2 * car['L'])
            u1 = (p1[0] + car['L']) / (2 * car['L'])
            v = i / K
            glNormal3f(*n0); glTexCoord2f(u0, v); glVertex3f(*p0)
            glNormal3f(*n1); glTexCoord2f(u1, v); glVertex3f(*p1)
        glEnd()

    for si, nx_sign in [(0, -1.0), (N - 1, 1.0)]:
        c = centers[si]
        glBegin(GL_TRIANGLE_FAN)
        glNormal3f(nx_sign, 0, 0)
        glTexCoord2f(0.5, 0.5)
        glVertex3f(*c)
        pts = stations[si]['pts']
        order = range(K + 1) if nx_sign > 0 else range(K, -1, -1)
        for i in order:
            idx = i % K
            p = pts[idx]
            u = 0.5 + 0.5 * (p[2] / car['W'])
            v = 0.5 + 0.5 * (p[1] / car['body_top_y'])
            glTexCoord2f(u, v)
            glVertex3f(*p)
        glEnd()

    glDisable(GL_TEXTURE_2D)


def _car_draw_cabin(car, paint_tex):
    cabin = car['cabin']
    Wc = car['W_cabin']
    color = car['color']

    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, paint_tex)
    _car_mat_paint(color)
    for z, nz in [(Wc / 2, 1), (-Wc / 2, -1)]:
        glBegin(GL_POLYGON)
        glNormal3f(0, 0, nz)
        pts = list(reversed(cabin)) if nz > 0 else list(cabin)
        for (x, y) in pts:
            glTexCoord2f((x + 3) / 6, y / 2)
            glVertex3f(x, y, z)
        glEnd()

    for i in range(len(cabin)):
        if i == 3:
            continue
        p0, p1 = cabin[i], cabin[(i + 1) % len(cabin)]
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        nlen = math.hypot(dx, dy)
        if nlen < 1e-6:
            continue
        nx, ny = -dy / nlen, dx / nlen

        if i in (0, 2):
            glDisable(GL_TEXTURE_2D)
            _car_mat_glass()
        else:
            glEnable(GL_TEXTURE_2D)
            glBindTexture(GL_TEXTURE_2D, paint_tex)
            _car_mat_paint(color)

        glBegin(GL_QUADS)
        glNormal3f(nx, ny, 0)
        glTexCoord2f(0, 0); glVertex3f(p0[0], p0[1],  Wc / 2)
        glTexCoord2f(1, 0); glVertex3f(p1[0], p1[1],  Wc / 2)
        glTexCoord2f(1, 1); glVertex3f(p1[0], p1[1], -Wc / 2)
        glTexCoord2f(0, 1); glVertex3f(p0[0], p0[1], -Wc / 2)
        glEnd()

    glDisable(GL_TEXTURE_2D)

    _car_mat_glass()
    cx_mid = (cabin[0][0] + cabin[3][0]) / 2
    cy_mid = (cabin[1][1] + cabin[2][1]) / 2
    shrunk = []
    for (x, y) in cabin:
        sx = x + (cx_mid - x) * 0.12
        sy = y + (cy_mid - y) * 0.18 if y < cy_mid - 0.01 else y - 0.05
        shrunk.append((sx, sy))
    for z, nz in [(Wc / 2 + 0.002, 1.0), (-Wc / 2 - 0.002, -1.0)]:
        glBegin(GL_POLYGON)
        glNormal3f(0, 0, nz)
        pts = shrunk if nz > 0 else list(reversed(shrunk))
        for (x, y) in pts:
            glVertex3f(x, y, z)
        glEnd()


def _car_unit_cube():
    faces = [
        ((0, 0, 1),  [(-0.5, -0.5,  0.5), ( 0.5, -0.5,  0.5),
                      ( 0.5,  0.5,  0.5), (-0.5,  0.5,  0.5)]),
        ((0, 0, -1), [(-0.5,  0.5, -0.5), ( 0.5,  0.5, -0.5),
                      ( 0.5, -0.5, -0.5), (-0.5, -0.5, -0.5)]),
        ((0, 1, 0),  [(-0.5,  0.5,  0.5), ( 0.5,  0.5,  0.5),
                      ( 0.5,  0.5, -0.5), (-0.5,  0.5, -0.5)]),
        ((0, -1, 0), [(-0.5, -0.5, -0.5), ( 0.5, -0.5, -0.5),
                      ( 0.5, -0.5,  0.5), (-0.5, -0.5,  0.5)]),
        ((1, 0, 0),  [( 0.5, -0.5,  0.5), ( 0.5, -0.5, -0.5),
                      ( 0.5,  0.5, -0.5), ( 0.5,  0.5,  0.5)]),
        ((-1, 0, 0), [(-0.5, -0.5, -0.5), (-0.5, -0.5,  0.5),
                      (-0.5,  0.5,  0.5), (-0.5,  0.5, -0.5)]),
    ]
    glBegin(GL_QUADS)
    for n, verts in faces:
        glNormal3f(*n)
        for v in verts:
            glVertex3f(*v)
    glEnd()


def _car_draw_details(car):
    W = car['W']
    Wc = car['W_cabin']
    color = car['color']

    _car_mat_paint(color)
    mirror_x = car['cabin_front_bot'] - 0.05
    mirror_y = car['body_top_y'] + 0.18
    for sign in (1, -1):
        glPushMatrix()
        glTranslatef(mirror_x, mirror_y, sign * (Wc / 2 + 0.09))
        glScalef(0.14, 0.09, 0.18)
        _car_unit_cube()
        glPopMatrix()

        _car_mat_chrome()
        glPushMatrix()
        glTranslatef(mirror_x + 0.02, mirror_y,
                     sign * (Wc / 2 + 0.19))
        glBegin(GL_QUADS)
        glNormal3f(0, 0, sign)
        glVertex3f(-0.09, -0.055, 0)
        glVertex3f( 0.09, -0.055, 0)
        glVertex3f( 0.09,  0.055, 0)
        glVertex3f(-0.09,  0.055, 0)
        glEnd()
        glPopMatrix()
        _car_mat_paint(color)

    if car['spoiler']:
        _car_mat_paint(color)
        sx1 = car['cabin_rear_bot'] - 0.05
        sx0 = sx1 - 0.25
        sy = car['body_top_y'] + 0.22
        sz = Wc / 2 - 0.05
        glPushMatrix()
        glTranslatef((sx0 + sx1) / 2, sy, 0)
        glScalef((sx1 - sx0), 0.04, sz * 2)
        _car_unit_cube()
        glPopMatrix()
        for zside in (sz - 0.05, -(sz - 0.05)):
            glPushMatrix()
            glTranslatef((sx0 + sx1) / 2 + 0.03,
                         car['body_top_y'] + 0.11, zside)
            glScalef(0.04, 0.22, 0.04)
            _car_unit_cube()
            glPopMatrix()

    _car_mat_grille()
    fx = car['front_x'] - 0.01
    gy_top = car['body_top_y'] * 0.55
    gy_bot = car['BH'] * 0.75
    gz = W * 0.28
    glBegin(GL_QUADS)
    glNormal3f(1, 0, 0)
    glVertex3f(fx, gy_bot,  gz)
    glVertex3f(fx, gy_bot, -gz)
    glVertex3f(fx, gy_top, -gz)
    glVertex3f(fx, gy_top,  gz)
    glEnd()
    _car_mat_chrome()
    bs_top = gy_bot - 0.02
    bs_bot = car['BH'] * 0.35
    glBegin(GL_QUADS)
    glNormal3f(1, 0, 0)
    glVertex3f(fx + 0.004, bs_bot,  W * 0.40)
    glVertex3f(fx + 0.004, bs_bot, -W * 0.40)
    glVertex3f(fx + 0.004, bs_top, -W * 0.40)
    glVertex3f(fx + 0.004, bs_top,  W * 0.40)
    glEnd()

    # --- Head- and taillights ---
    # Real car lamps are rectangular lenses mounted flush on the rear/front
    # panels, not dome spheres, and at distance the shape + brightness is
    # what reads. So we draw a thin dark bezel quad for the housing and a
    # strongly emissive red (tail) / warm-white (head) lens quad on top,
    # nudged out along the body normal to avoid z-fighting with the end cap.
    # Emission is applied via GL_EMISSION so the lamps stay the correct
    # colour regardless of scene lighting — they always glow.
    head_y = car['body_top_y'] * 0.55
    head_half_w = W * 0.11          # horizontal half-extent (centre-to-edge)
    head_half_h = 0.06              # vertical half-extent
    head_center_z = W * 0.33
    for zc in (head_center_z, -head_center_z):
        fx = car['front_x']
        _car_mat_dark()
        glBegin(GL_QUADS)
        glNormal3f(1, 0, 0)
        bz = head_half_w + 0.015
        by = head_half_h + 0.012
        glVertex3f(fx + 0.008, head_y - by, zc - bz)
        glVertex3f(fx + 0.008, head_y - by, zc + bz)
        glVertex3f(fx + 0.008, head_y + by, zc + bz)
        glVertex3f(fx + 0.008, head_y + by, zc - bz)
        glEnd()
        _car_mat_emit((1.0, 0.97, 0.82))
        glBegin(GL_QUADS)
        glNormal3f(1, 0, 0)
        glVertex3f(fx + 0.014, head_y - head_half_h, zc - head_half_w)
        glVertex3f(fx + 0.014, head_y - head_half_h, zc + head_half_w)
        glVertex3f(fx + 0.014, head_y + head_half_h, zc + head_half_w)
        glVertex3f(fx + 0.014, head_y + head_half_h, zc - head_half_w)
        glEnd()
        _car_mat_clear()

    # Taillights: wider, more prominent horizontal bars so they read clearly
    # from behind even at distance. Saturated red with strong emission.
    tail_y = car['body_top_y'] * 0.72
    tail_half_w = W * 0.13
    tail_half_h = 0.065
    tail_center_z = W * 0.33
    rx = car['rear_x']
    for zc in (tail_center_z, -tail_center_z):
        _car_mat_dark()
        glBegin(GL_QUADS)
        glNormal3f(-1, 0, 0)
        bz = tail_half_w + 0.018
        by = tail_half_h + 0.014
        glVertex3f(rx - 0.008, tail_y - by, zc + bz)
        glVertex3f(rx - 0.008, tail_y - by, zc - bz)
        glVertex3f(rx - 0.008, tail_y + by, zc - bz)
        glVertex3f(rx - 0.008, tail_y + by, zc + bz)
        glEnd()
        _car_mat_emit((1.0, 0.08, 0.06))
        glBegin(GL_QUADS)
        glNormal3f(-1, 0, 0)
        glVertex3f(rx - 0.014, tail_y - tail_half_h, zc + tail_half_w)
        glVertex3f(rx - 0.014, tail_y - tail_half_h, zc - tail_half_w)
        glVertex3f(rx - 0.014, tail_y + tail_half_h, zc - tail_half_w)
        glVertex3f(rx - 0.014, tail_y + tail_half_h, zc + tail_half_w)
        glEnd()
        _car_mat_clear()

    # Centre brake-light strip — narrower, slightly higher, optional
    # visual anchor that helps the rear read as a car silhouette from far.
    _car_mat_emit((1.0, 0.12, 0.08))
    strip_half_w = W * 0.18
    strip_half_h = 0.02
    strip_y = car['body_top_y'] * 0.88
    glBegin(GL_QUADS)
    glNormal3f(-1, 0, 0)
    glVertex3f(rx - 0.014, strip_y - strip_half_h,  strip_half_w)
    glVertex3f(rx - 0.014, strip_y - strip_half_h, -strip_half_w)
    glVertex3f(rx - 0.014, strip_y + strip_half_h, -strip_half_w)
    glVertex3f(rx - 0.014, strip_y + strip_half_h,  strip_half_w)
    glEnd()
    _car_mat_clear()


def _car_draw_wheel(x, y, zc, r, side, spoke_count):
    tire_w = r * 0.56
    hw = tire_w / 2
    glPushMatrix()
    glTranslatef(x, y, zc - hw)

    q = gluNewQuadric()
    gluQuadricNormals(q, GLU_SMOOTH)

    r_mid = r
    r_wall = r * 0.92
    tread_w = tire_w * 0.45
    wall_w = (tire_w - tread_w) / 2

    _car_mat_tire()
    gluCylinder(q, r_wall, r_mid, wall_w, 20, 1)
    glPushMatrix(); glTranslatef(0, 0, wall_w)
    gluCylinder(q, r_mid, r_mid, tread_w, 20, 1)
    glPopMatrix()
    glPushMatrix(); glTranslatef(0, 0, wall_w + tread_w)
    gluCylinder(q, r_mid, r_wall, wall_w, 20, 1)
    glPopMatrix()

    inner_local_z = 0.0 if side > 0 else tire_w
    glPushMatrix()
    glTranslatef(0, 0, inner_local_z)
    if side > 0:
        glRotatef(180, 1, 0, 0)
    _car_mat_tire()
    gluDisk(q, 0, r_wall, 18, 1)
    glPopMatrix()

    outer_local_z = tire_w if side > 0 else 0.0
    glPushMatrix()
    glTranslatef(0, 0, outer_local_z)
    if side < 0:
        glRotatef(180, 1, 0, 0)

    glPushMatrix()
    glTranslatef(0, 0, -tire_w * 0.35)
    _car_mat_brake()
    gluDisk(q, r * 0.22, r * 0.72, 20, 1)
    glPopMatrix()

    glPushMatrix()
    glTranslatef(0, 0, -0.012)
    _car_mat_dark()
    gluDisk(q, r * 0.20, r_wall * 0.96, 20, 1)
    glPopMatrix()

    _car_mat_hub()
    gluDisk(q, 0, r * 0.18, 16, 1)

    _car_mat_chrome()
    gluDisk(q, r_wall * 0.93, r_wall * 0.99, 20, 1)

    _car_mat_rim()
    hub_r = r * 0.19
    out_r = r_wall * 0.94
    spoke_w_hub = 0.07
    spoke_w_out = 0.05
    glBegin(GL_QUADS)
    glNormal3f(0, 0, 1)
    for i in range(spoke_count):
        a = 2 * math.pi * i / spoke_count
        ca, sa = math.cos(a), math.sin(a)
        px, py = -sa, ca
        x1 = ca * hub_r + px * spoke_w_hub
        y1 = sa * hub_r + py * spoke_w_hub
        x2 = ca * hub_r - px * spoke_w_hub
        y2 = sa * hub_r - py * spoke_w_hub
        x3 = ca * out_r - px * spoke_w_out
        y3 = sa * out_r - py * spoke_w_out
        x4 = ca * out_r + px * spoke_w_out
        y4 = sa * out_r + py * spoke_w_out
        glVertex3f(x1, y1, 0.002)
        glVertex3f(x2, y2, 0.002)
        glVertex3f(x3, y3, 0.002)
        glVertex3f(x4, y4, 0.002)
    glEnd()

    glPopMatrix()
    gluDeleteQuadric(q)
    glPopMatrix()


def build_car_variant(seed):
    rng = np.random.default_rng(seed)
    car = _generate_car_params(rng)
    paint_tex = make_car_paint_texture(car['color'], rng)
    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    _car_draw_lower_body(car, paint_tex)
    _car_draw_cabin(car, paint_tex)
    _car_draw_details(car)
    for (wx, wy, wz) in car['wheels']:
        _car_draw_wheel(wx, wy, wz, car['WR'],
                        1 if wz > 0 else -1, car['spoke_count'])
    glEndList()
    # Light hardpoints kept for the night additive-glow pass, which renders
    # per-frame billboards (so intensity can be modulated by night_a).
    return {
        'list': list_id, 'paint_tex': paint_tex,
        'L': car['L'], 'W': car['W'],
        'head_x': car['front_x'] + 0.015,
        'tail_x': car['rear_x'] - 0.015,
        'head_y': car['body_top_y'] * 0.55,
        'tail_y': car['body_top_y'] * 0.72,
        'head_zoff': car['W'] * 0.33,
        'tail_zoff': car['W'] * 0.33,
    }


def build_car_variants(n=N_CAR_VARIANTS):
    return [build_car_variant(5000 + i) for i in range(n)]


def _car_speed_for(lane, player_speed, rng):
    if lane == -1:
        # Always faster than the camera so the car recedes away.
        base = max(CAR_SPEED_AWAY_FLOOR, float(player_speed))
        return base + float(rng.uniform(CAR_SPEED_AWAY_OVER_MIN,
                                        CAR_SPEED_AWAY_OVER_MAX))
    return float(rng.uniform(CAR_SPEED_ONCOMING_MIN,
                             CAR_SPEED_ONCOMING_MAX))


def _car_respawn_s(lane, s_car, rng):
    if lane == -1:
        # Enter from behind the camera — offset is negative.
        return s_car - float(rng.uniform(CAR_SPAWN_BEHIND_MIN,
                                         CAR_SPAWN_BEHIND_MAX))
    # Oncoming: far ahead so they have room to close on the camera.
    return s_car + float(rng.uniform(CAR_SPAWN_AHEAD_MIN,
                                     CAR_SPAWN_AHEAD_MAX))


def init_cars(seed=303, s_car=0.0, player_speed=0.0,
              n_variants=N_CAR_VARIANTS, n_per_lane=N_CARS_PER_LANE):
    rng = np.random.default_rng(seed)
    cars = []
    for lane in (-1, +1):
        for _ in range(n_per_lane):
            cars.append({
                'variant': int(rng.integers(0, n_variants)),
                'lane': lane,
                's': _car_respawn_s(lane, s_car, rng),
                'speed': _car_speed_for(lane, player_speed, rng),
            })
    return {'cars': cars, 'rng': rng, 'n_variants': n_variants}


def update_cars(state, dt, s_car, player_speed):
    rng = state['rng']
    for c in state['cars']:
        if c['lane'] == -1:
            # Clamp same-direction cars to always outrun the camera so
            # they actually pull ahead and pass it after spawning behind.
            margin = max(CAR_SPEED_AWAY_OVER_MIN,
                         c['speed'] - max(CAR_SPEED_AWAY_FLOOR,
                                          float(player_speed)))
            margin = min(margin, CAR_SPEED_AWAY_OVER_MAX)
            c['speed'] = max(CAR_SPEED_AWAY_FLOOR,
                             float(player_speed)) + margin
            c['s'] += c['speed'] * dt
            # Respawn behind the camera once the car has disappeared off
            # the far horizon ahead. (Never respawn on the "behind" side —
            # they *live* behind until they pass the camera.)
            if c['s'] > s_car + CAR_DESPAWN_AHEAD:
                c['s'] = _car_respawn_s(-1, s_car, rng)
                c['variant'] = int(rng.integers(0, state['n_variants']))
                c['speed'] = _car_speed_for(-1, player_speed, rng)
        else:
            # Oncoming: closing on the camera.
            c['s'] -= c['speed'] * dt
            if c['s'] < s_car - CAR_DESPAWN_BEHIND:
                c['s'] = _car_respawn_s(+1, s_car, rng)
                c['variant'] = int(rng.integers(0, state['n_variants']))
                c['speed'] = _car_speed_for(+1, player_speed, rng)


def draw_cars(state, car_variants, s_car, amb_rgb, sun_dir,
              night_a, flare_tex):
    if not state['cars']:
        return

    glEnable(GL_LIGHTING)
    glEnable(GL_LIGHT0)
    glEnable(GL_NORMALIZE)
    glDisable(GL_COLOR_MATERIAL)
    # Directional light loosely tracking the sun; clamp altitude so night
    # keeps some top-down fill (otherwise unlit sides go pure black).
    sx = float(sun_dir[0])
    sy = max(0.25, float(sun_dir[1]))
    sz = float(sun_dir[2])
    glLightfv(GL_LIGHT0, GL_POSITION, (sx, sy, sz, 0.0))
    glLightfv(GL_LIGHT0, GL_AMBIENT,
              (amb_rgb[0] * 0.55, amb_rgb[1] * 0.55,
               amb_rgb[2] * 0.58, 1.0))
    glLightfv(GL_LIGHT0, GL_DIFFUSE,
              (min(1.0, amb_rgb[0] * 1.10),
               min(1.0, amb_rgb[1] * 1.08),
               min(1.0, amb_rgb[2] * 1.00), 1.0))
    glLightfv(GL_LIGHT0, GL_SPECULAR,
              (min(1.0, amb_rgb[0] * 1.10),
               min(1.0, amb_rgb[1] * 1.08),
               min(1.0, amb_rgb[2] * 1.00), 1.0))
    glLightModelfv(GL_LIGHT_MODEL_AMBIENT,
                   (amb_rgb[0] * 0.35, amb_rgb[1] * 0.35,
                    amb_rgb[2] * 0.38, 1.0))
    glLightModeli(GL_LIGHT_MODEL_LOCAL_VIEWER, GL_TRUE)

    for c in state['cars']:
        s = c['s']
        if s < s_car - 15.0 or s > s_car + N_SEG * SEG_LEN:
            continue
        cx = curve_x(s)
        cy = curve_y(s)
        cz = -(s - s_car)
        lane_sign = c['lane']  # -1 = left of player, +1 = right
        cx += lane_sign * CAR_LANE_HALF

        ds = 0.5
        dxds = (curve_x(s + ds) - curve_x(s - ds)) / (2.0 * ds)
        if c['lane'] == -1:
            yaw_deg = math.degrees(math.atan2(1.0, dxds))
        else:
            yaw_deg = math.degrees(math.atan2(-1.0, -dxds))

        glPushMatrix()
        glTranslatef(cx, cy, cz)
        glRotatef(yaw_deg, 0.0, 1.0, 0.0)
        glCallList(car_variants[c['variant']]['list'])
        glPopMatrix()

    _car_mat_clear()
    glDisable(GL_LIGHTING)
    glDisable(GL_LIGHT0)
    glLightModelfv(GL_LIGHT_MODEL_AMBIENT, (0.2, 0.2, 0.2, 1.0))
    glLightModeli(GL_LIGHT_MODEL_LOCAL_VIEWER, GL_FALSE)

    # --- Night headlight / taillight pass ---
    # Additive-blended, camera-facing billboards at each light's world
    # position. Drawn per-frame (not baked) so intensity scales with the
    # current night factor. Headlights read warm-white and only project
    # from the car's front face; taillights read red and only from the
    # rear. We gate per-face by checking whether the car's forward vector
    # points toward or away from the camera, so recediing cars glow red
    # and oncoming cars glow yellow — matching real traffic at night.
    if night_a <= 0.03:
        return
    glDisable(GL_LIGHTING)
    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, flare_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    glDepthMask(GL_FALSE)

    mv = glGetFloatv(GL_MODELVIEW_MATRIX)
    # Camera-space right/up expressed in world coords: columns 0/1 of the
    # upper-left 3x3 of the current modelview matrix.
    cam_right = (float(mv[0][0]), float(mv[1][0]), float(mv[2][0]))
    cam_up = (float(mv[0][1]), float(mv[1][1]), float(mv[2][1]))

    head_col = (1.0 * night_a, 0.96 * night_a, 0.78 * night_a)
    # Taillights get +50% additive intensity at night (user request) —
    # the halo reads much brighter than headlights around the rear lens
    # without touching the baked daytime emission.
    tail_boost = 1.5
    tail_col = (min(1.0, 0.95 * night_a * tail_boost),
                min(1.0, 0.10 * night_a * tail_boost),
                min(1.0, 0.08 * night_a * tail_boost))
    head_size = 0.55
    tail_size = 0.60

    for c in state['cars']:
        s = c['s']
        if s < s_car - 15.0 or s > s_car + N_SEG * SEG_LEN:
            continue
        cx = curve_x(s)
        cy = curve_y(s)
        cz = -(s - s_car)
        cx += c['lane'] * CAR_LANE_HALF
        ds = 0.5
        dxds = (curve_x(s + ds) - curve_x(s - ds)) / (2.0 * ds)
        # Forward direction of the car in world coords.
        if c['lane'] == -1:
            fx, fz = dxds, -1.0
        else:
            fx, fz = -dxds, 1.0
        fn = math.hypot(fx, fz) + 1e-9
        fx /= fn; fz /= fn
        # Right perpendicular (for lateral light offsets).
        rx, rz = -fz, fx

        v = car_variants[c['variant']]
        # View direction from car to camera (camera is at +z = CAM_BACK,
        # x = curve_x(s_cam), y = road_y + CAM_HEIGHT).
        to_cam_x = -cx  # approx — the camera's x moves with the road too
        to_cam_z = CAM_BACK - cz
        to_cam_n = math.hypot(to_cam_x, to_cam_z) + 1e-9
        to_cam_x /= to_cam_n; to_cam_z /= to_cam_n
        face = fx * to_cam_x + fz * to_cam_z  # >0: car front faces camera

        # Emit a camera-facing billboard at world position p.
        def emit(p, col, size):
            r0, r1, r2 = cam_right
            u0, u1, u2 = cam_up
            hx, hy, hz = r0 * size, r1 * size, r2 * size
            vx, vy, vz = u0 * size, u1 * size, u2 * size
            glColor4f(col[0], col[1], col[2], 1.0)
            glBegin(GL_QUADS)
            glTexCoord2f(0, 0); glVertex3f(p[0] - hx - vx,
                                           p[1] - hy - vy,
                                           p[2] - hz - vz)
            glTexCoord2f(1, 0); glVertex3f(p[0] + hx - vx,
                                           p[1] + hy - vy,
                                           p[2] + hz - vz)
            glTexCoord2f(1, 1); glVertex3f(p[0] + hx + vx,
                                           p[1] + hy + vy,
                                           p[2] + hz + vz)
            glTexCoord2f(0, 1); glVertex3f(p[0] - hx + vx,
                                           p[1] - hy + vy,
                                           p[2] - hz + vz)
            glEnd()

        # Headlights visible when car front faces the camera (face > 0);
        # taillights when it faces away (face < 0). A narrow crossfade
        # around face=0 avoids flicker at perpendicular angles.
        head_w = max(0.0, min(1.0, (face + 0.05) / 0.15))
        tail_w = max(0.0, min(1.0, (-face + 0.05) / 0.15))

        if head_w > 0.01:
            hx_off = v['head_x']
            hy_off = v['head_y']
            hz_off = v['head_zoff']
            for lateral in (+1, -1):
                px = cx + fx * hx_off + rx * hz_off * lateral
                pz = cz + fz * hx_off + rz * hz_off * lateral
                py = cy + hy_off
                emit((px, py, pz),
                     (head_col[0] * head_w,
                      head_col[1] * head_w,
                      head_col[2] * head_w),
                     head_size)

        if tail_w > 0.01:
            tx_off = v['tail_x']
            ty_off = v['tail_y']
            tz_off = v['tail_zoff']
            for lateral in (+1, -1):
                px = cx + fx * tx_off + rx * tz_off * lateral
                pz = cz + fz * tx_off + rz * tz_off * lateral
                py = cy + ty_off
                emit((px, py, pz),
                     (tail_col[0] * tail_w,
                      tail_col[1] * tail_w,
                      tail_col[2] * tail_w),
                     tail_size)

    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)
    glDisable(GL_TEXTURE_2D)


# --- Procedural trucks ---
# Ported from the sibling trucks/ project. Shares the car module's loft,
# cabin extrusion, wheel, materials, and paint texture — a truck is just a
# bigger, boxier car with an extra cargo section (pickup bed / box / semi
# fifth-wheel / flatbed deck / dump hopper) and a few extra hardpoints
# (exhaust stacks, mud flaps, beacon, tow hitch). Traffic logic reuses
# `_car_respawn_s` and `_car_speed_for` so trucks follow the same flow as
# cars: left lane spawns behind the camera and overtakes it, right lane
# spawns far ahead and closes on the camera. Density is ~1/4 of cars.
N_TRUCK_VARIANTS = 10
N_TRUCKS_PER_LANE = 2   # halved — trucks are rare compared to cars
TRUCK_STYLES = ['pickup', 'box_truck', 'semi', 'flatbed', 'dump']

TRUCK_PALETTE = [
    (0.85, 0.10, 0.10), (0.10, 0.22, 0.70), (0.93, 0.93, 0.95),
    (0.07, 0.07, 0.09), (0.18, 0.18, 0.22), (0.72, 0.72, 0.74),
    (0.20, 0.55, 0.28), (0.92, 0.76, 0.12), (0.32, 0.24, 0.56),
    (0.75, 0.38, 0.08), (0.08, 0.50, 0.55), (0.55, 0.15, 0.08),
]


def _truck_draw_box(x0, x1, y0, y1, z0, z1):
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    cz = (z0 + z1) / 2
    glPushMatrix()
    glTranslatef(cx, cy, cz)
    glScalef(x1 - x0, y1 - y0, z1 - z0)
    _car_unit_cube()
    glPopMatrix()


def _generate_truck_params(rng):
    style = TRUCK_STYLES[int(rng.integers(0, len(TRUCK_STYLES)))]
    L = float(rng.uniform(5.2, 7.8))
    W = float(rng.uniform(1.90, 2.25))
    WR = float(rng.uniform(0.46, 0.60))
    LBH = float(rng.uniform(0.70, 0.95))
    CH = float(rng.uniform(0.85, 1.15))
    HL = float(rng.uniform(0.35, 0.55))
    cab_frac = float(rng.uniform(0.32, 0.45))
    dual_rear = False
    stacks = False
    beacon = False
    bed_rails = False
    mud_flaps = True
    trailer_plate = False

    if style == 'pickup':
        L = float(rng.uniform(5.2, 6.2))
        W = float(rng.uniform(1.90, 2.05))
        WR = float(rng.uniform(0.44, 0.54))
        LBH = float(rng.uniform(0.68, 0.86))
        CH = float(rng.uniform(0.82, 1.00))
        cab_frac = float(rng.uniform(0.38, 0.48))
        dual_rear = rng.random() < 0.30
        bed_rails = rng.random() < 0.35
    elif style == 'box_truck':
        L = float(rng.uniform(6.4, 7.8))
        W = float(rng.uniform(2.00, 2.25))
        WR = float(rng.uniform(0.48, 0.58))
        LBH = float(rng.uniform(0.78, 0.95))
        CH = float(rng.uniform(0.92, 1.15))
        cab_frac = float(rng.uniform(0.28, 0.36))
        dual_rear = True
        beacon = rng.random() < 0.40
    elif style == 'semi':
        L = float(rng.uniform(5.8, 6.8))
        W = float(rng.uniform(2.05, 2.25))
        WR = float(rng.uniform(0.50, 0.60))
        LBH = float(rng.uniform(0.82, 0.98))
        CH = float(rng.uniform(1.05, 1.30))
        cab_frac = float(rng.uniform(0.55, 0.72))
        HL = float(rng.uniform(0.45, 0.70))
        dual_rear = True
        stacks = True
        trailer_plate = True
        beacon = rng.random() < 0.25
    elif style == 'flatbed':
        L = float(rng.uniform(5.8, 7.2))
        W = float(rng.uniform(1.95, 2.15))
        WR = float(rng.uniform(0.46, 0.56))
        LBH = float(rng.uniform(0.74, 0.90))
        CH = float(rng.uniform(0.88, 1.05))
        cab_frac = float(rng.uniform(0.32, 0.42))
        dual_rear = rng.random() < 0.55
    elif style == 'dump':
        L = float(rng.uniform(6.0, 7.2))
        W = float(rng.uniform(2.05, 2.25))
        WR = float(rng.uniform(0.50, 0.60))
        LBH = float(rng.uniform(0.82, 0.98))
        CH = float(rng.uniform(0.92, 1.10))
        cab_frac = float(rng.uniform(0.30, 0.38))
        dual_rear = True
        beacon = rng.random() < 0.60

    spoke_count = int(rng.choice([5, 6, 8, 10]))

    BH = WR * 0.85
    body_top_y = BH + LBH
    roof_y = body_top_y + CH

    rear_x = -L / 2
    front_x = L / 2

    cab_len = L * cab_frac
    cab_front_bot = front_x - HL
    cab_front_top = cab_front_bot - float(rng.uniform(0.08, 0.22))
    cab_rear_bot = cab_front_bot - cab_len
    cab_rear_top = cab_rear_bot + float(rng.uniform(0.05, 0.18))

    cargo_front_x = cab_rear_bot - 0.05
    cargo_rear_x = rear_x + 0.05

    if style == 'box_truck':
        cargo_top_y = roof_y + float(rng.uniform(0.05, 0.20))
    elif style == 'semi':
        cargo_top_y = body_top_y + float(rng.uniform(0.05, 0.15))
    elif style == 'pickup':
        cargo_top_y = body_top_y + float(rng.uniform(0.22, 0.38))
    elif style == 'flatbed':
        cargo_top_y = body_top_y + float(rng.uniform(0.02, 0.08))
    else:  # dump
        cargo_top_y = body_top_y + float(rng.uniform(0.55, 0.85))

    W_cabin = W * float(rng.uniform(0.88, 0.96))
    color = TRUCK_PALETTE[int(rng.integers(0, len(TRUCK_PALETTE)))]

    zh = W / 2
    hood_mid = (front_x + cab_front_bot) / 2
    station_defs = [
        (rear_x,              0.60, BH * 0.55, body_top_y * 0.60, 3.5, 3.8),
        (rear_x + 0.22,       0.94, BH * 0.50, body_top_y * 0.90, 5.0, 6.0),
        (rear_x + 0.55,       1.00, BH * 0.48, body_top_y,        7.5, 7.5),
        (cargo_rear_x,        1.00, BH * 0.48, body_top_y,        8.0, 8.0),
        (cargo_front_x,       1.00, BH * 0.48, body_top_y,        8.0, 8.0),
        (cab_rear_bot,        1.00, BH * 0.48, body_top_y,        7.5, 7.5),
        (cab_front_bot,       1.00, BH * 0.48, body_top_y * 0.98, 7.0, 7.0),
        (hood_mid,            0.98, BH * 0.52, body_top_y * 0.92, 5.5, 6.0),
        (front_x - 0.08,      0.92, BH * 0.58, body_top_y * 0.80, 4.2, 4.5),
        (front_x,             0.62, BH * 0.60, body_top_y * 0.58, 3.2, 3.5),
    ]
    K = 36
    stations = []
    for (x, z_scale, y_bot, y_top, et, eb) in station_defs:
        pts2d = _car_superellipse_section(zh * z_scale, y_bot, y_top,
                                          et, eb, K)
        stations.append({'x': x, 'pts': [(x, y, z) for (z, y) in pts2d]})

    cabin = [
        (cab_rear_bot,  body_top_y),
        (cab_rear_top,  roof_y),
        (cab_front_top, roof_y),
        (cab_front_bot, body_top_y),
    ]

    front_ax = front_x - HL - float(rng.uniform(0.10, 0.25))
    if style == 'semi':
        rear_ax = cab_rear_bot + float(rng.uniform(0.10, 0.35))
    elif style in ('box_truck', 'dump'):
        rear_ax = cargo_rear_x + float(rng.uniform(0.35, 0.70))
    else:
        rear_ax = cargo_rear_x + float(rng.uniform(0.55, 0.95))
    rear_ax = max(rear_x + WR + 0.12, min(cargo_front_x - 0.15, rear_ax))

    inset = 0.02
    wheels = []
    wheels.append((front_ax, WR,  W / 2 - inset))
    wheels.append((front_ax, WR, -W / 2 + inset))
    if dual_rear:
        tire_w = WR * 0.56
        offset = tire_w * 1.02
        wheels.append((rear_ax, WR,  W / 2 - inset))
        wheels.append((rear_ax, WR,  W / 2 - inset - offset))
        wheels.append((rear_ax, WR, -W / 2 + inset))
        wheels.append((rear_ax, WR, -W / 2 + inset + offset))
        if style in ('semi', 'box_truck', 'dump'):
            rear_ax2 = rear_ax - WR * 2.25
            rear_ax2 = max(rear_x + WR + 0.12, rear_ax2)
            wheels.append((rear_ax2, WR,  W / 2 - inset))
            wheels.append((rear_ax2, WR,  W / 2 - inset - offset))
            wheels.append((rear_ax2, WR, -W / 2 + inset))
            wheels.append((rear_ax2, WR, -W / 2 + inset + offset))
    else:
        wheels.append((rear_ax, WR,  W / 2 - inset))
        wheels.append((rear_ax, WR, -W / 2 + inset))

    # Stash a small static dump-hopper tilt (mirrors the original
    # truck.py randomization so each variant bakes a consistent shape).
    dump_lift = float(rng.uniform(0.00, 0.15))

    return {
        'style': style, 'L': L, 'W': W, 'W_cabin': W_cabin, 'WR': WR,
        'stations': stations, 'K': K, 'cabin': cabin, 'wheels': wheels,
        'color': color, 'spoke_count': spoke_count,
        'body_top_y': body_top_y, 'roof_y': roof_y,
        'cab_rear_bot': cab_rear_bot, 'cab_front_bot': cab_front_bot,
        'cab_rear_top': cab_rear_top, 'cab_front_top': cab_front_top,
        'cargo_front_x': cargo_front_x, 'cargo_rear_x': cargo_rear_x,
        'cargo_top_y': cargo_top_y,
        'rear_x': rear_x, 'front_x': front_x, 'BH': BH, 'HL': HL,
        'dual_rear': dual_rear, 'stacks': stacks, 'beacon': beacon,
        'bed_rails': bed_rails, 'mud_flaps': mud_flaps,
        'trailer_plate': trailer_plate,
        'dump_lift': dump_lift,
    }


def _truck_draw_cargo(truck, paint_tex):
    style = truck['style']
    color = truck['color']
    W = truck['W']
    x0 = truck['cargo_rear_x']
    x1 = truck['cargo_front_x']
    y0 = truck['body_top_y']
    yt = truck['cargo_top_y']

    if style == 'pickup':
        wall_h = yt - y0
        wall_t = 0.06
        zw = W * 0.48
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, paint_tex)
        _car_mat_paint(color)
        _truck_draw_box(x0, x1, y0 - 0.02, y0, -zw, zw)
        _truck_draw_box(x0, x1, y0, y0 + wall_h, zw - wall_t, zw)
        _truck_draw_box(x0, x1, y0, y0 + wall_h, -zw, -zw + wall_t)
        _truck_draw_box(x0, x0 + wall_t, y0, y0 + wall_h, -zw, zw)
        glDisable(GL_TEXTURE_2D)
        # Bed-liner interior (matte plastic)
        _car_mat(((0.05, 0.05, 0.06)), (0.11, 0.11, 0.12),
                 (0.15, 0.15, 0.18), 10.0)
        _truck_draw_box(x0 + wall_t + 0.01, x1 - 0.02,
                        y0 + 0.001, y0 + 0.02,
                        -zw + wall_t + 0.01, zw - wall_t - 0.01)
        if truck['bed_rails']:
            _car_mat_chrome()
            for zs in (zw - wall_t * 0.5, -(zw - wall_t * 0.5)):
                _truck_draw_box(x0 + 0.05, x1 - 0.05,
                                y0 + wall_h + 0.02, y0 + wall_h + 0.08,
                                zs - 0.025, zs + 0.025)

    elif style == 'box_truck':
        zw = W * 0.52
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, paint_tex)
        _car_mat_paint((0.93, 0.93, 0.95))  # white box regardless of cab
        _truck_draw_box(x0, x1, y0 + 0.02, yt, -zw, zw)
        glDisable(GL_TEXTURE_2D)
        _car_mat_dark()
        _truck_draw_box(x0 - 0.01, x0 + 0.005, y0 + 0.08, yt - 0.06,
                        -zw + 0.08, zw - 0.08)
        _car_mat_chrome()
        for frac in (0.28, 0.55, 0.82):
            ys = y0 + 0.02 + (yt - y0 - 0.02) * frac
            _truck_draw_box(x0 + 0.01, x1 - 0.01, ys - 0.012, ys + 0.012,
                            zw - 0.005, zw + 0.005)
            _truck_draw_box(x0 + 0.01, x1 - 0.01, ys - 0.012, ys + 0.012,
                            -zw - 0.005, -zw + 0.005)

    elif style == 'semi':
        _car_mat_dark()
        plate_z = W * 0.40
        _truck_draw_box(x0 + 0.10, x1 - 0.05, y0 + 0.02, y0 + 0.12,
                        -plate_z, plate_z)
        _car_mat_chrome()
        _truck_draw_box(x0 + 0.45, x0 + 0.75, y0 + 0.12, y0 + 0.16,
                        -0.25, 0.25)
        q = gluNewQuadric()
        _car_mat_chrome()
        tank_r = 0.22
        tank_len = min(1.2, (x1 - x0) * 0.6)
        tx = (x0 + x1) / 2 - tank_len / 2
        for side in (1, -1):
            glPushMatrix()
            glTranslatef(tx, y0 - tank_r * 0.3, side * (W / 2 + 0.02))
            glRotatef(-90, 0, 1, 0)
            gluCylinder(q, tank_r, tank_r, tank_len, 18, 1)
            gluDisk(q, 0, tank_r, 16, 1)
            glTranslatef(0, 0, tank_len)
            gluDisk(q, 0, tank_r, 16, 1)
            glPopMatrix()
        gluDeleteQuadric(q)

    elif style == 'flatbed':
        zw = W * 0.52
        _car_mat_dark()
        _truck_draw_box(x0, x1, y0 + 0.01, yt, -zw, zw)
        _car_mat_chrome()
        rail_h = 0.08
        for zs in (zw - 0.02, -(zw - 0.02)):
            _truck_draw_box(x0 + 0.02, x1 - 0.02,
                            yt, yt + rail_h,
                            zs - 0.02, zs + 0.02)
        _car_mat_dark()
        n = 5
        for i in range(n):
            t = (i + 0.5) / n
            xs = x0 + (x1 - x0) * t
            for zs in (zw - 0.02, -(zw - 0.02)):
                _truck_draw_box(xs - 0.03, xs + 0.03,
                                yt, yt + rail_h + 0.04,
                                zs - 0.025, zs + 0.025)

    elif style == 'dump':
        zw = W * 0.52
        floor_y = y0 + 0.02
        lift = truck['dump_lift']
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, paint_tex)
        _car_mat_paint(color)
        glBegin(GL_QUADS)
        glNormal3f(0, 1, 0)
        glVertex3f(x0, floor_y,        -zw)
        glVertex3f(x1, floor_y + lift, -zw)
        glVertex3f(x1, floor_y + lift,  zw)
        glVertex3f(x0, floor_y,         zw)
        glEnd()
        for zs, nz in ((zw, 1), (-zw, -1)):
            glBegin(GL_QUADS)
            glNormal3f(0, 0, nz)
            glVertex3f(x0, floor_y,        zs)
            glVertex3f(x1, floor_y + lift, zs)
            glVertex3f(x1, yt + lift,      zs)
            glVertex3f(x0, yt,             zs)
            glEnd()
        glBegin(GL_QUADS)
        glNormal3f(1, 0, 0)
        glVertex3f(x1, floor_y + lift, -zw)
        glVertex3f(x1, yt + lift,      -zw)
        glVertex3f(x1, yt + lift,       zw)
        glVertex3f(x1, floor_y + lift,  zw)
        glEnd()
        glBegin(GL_QUADS)
        glNormal3f(-1, 0, 0)
        glVertex3f(x0, floor_y,    zw)
        glVertex3f(x0, yt - 0.15,  zw)
        glVertex3f(x0, yt - 0.15, -zw)
        glVertex3f(x0, floor_y,   -zw)
        glEnd()
        glDisable(GL_TEXTURE_2D)
        _car_mat_chrome()
        q = gluNewQuadric()
        glPushMatrix()
        glTranslatef(truck['cab_rear_bot'] + 0.05, y0 + 0.10, 0)
        glRotatef(-70, 0, 0, 1)
        gluCylinder(q, 0.06, 0.06, 0.45, 12, 1)
        glPopMatrix()
        gluDeleteQuadric(q)


def _truck_draw_details(truck):
    W = truck['W']
    Wc = truck['W_cabin']
    color = truck['color']

    # Mirrors: truck-style extended arms + large housing
    mirror_x = truck['cab_front_bot'] - 0.05
    mirror_y = truck['body_top_y'] + 0.35
    _car_mat_paint(color)
    for sign in (1, -1):
        glPushMatrix()
        glTranslatef(mirror_x, mirror_y, sign * (Wc / 2 + 0.10))
        glScalef(0.08, 0.08, 0.20)
        _car_unit_cube()
        glPopMatrix()
        glPushMatrix()
        glTranslatef(mirror_x, mirror_y, sign * (Wc / 2 + 0.22))
        glScalef(0.12, 0.28, 0.10)
        _car_unit_cube()
        glPopMatrix()
        _car_mat_chrome()
        glPushMatrix()
        glTranslatef(mirror_x + 0.01, mirror_y, sign * (Wc / 2 + 0.28))
        glBegin(GL_QUADS)
        glNormal3f(0, 0, sign)
        glVertex3f(-0.05, -0.13, 0)
        glVertex3f( 0.05, -0.13, 0)
        glVertex3f( 0.05,  0.13, 0)
        glVertex3f(-0.05,  0.13, 0)
        glEnd()
        glPopMatrix()
        _car_mat_paint(color)

    # Exhaust stacks behind the cab (semi only).
    if truck['stacks']:
        _car_mat_chrome()
        q = gluNewQuadric()
        stack_r = 0.07
        stack_h = 1.3
        for sign in (1, -1):
            sx = truck['cab_rear_bot'] + 0.08
            sz = sign * (Wc / 2 + 0.03)
            sy = truck['body_top_y'] + 0.02
            glPushMatrix()
            glTranslatef(sx, sy, sz)
            glRotatef(-90, 1, 0, 0)
            gluCylinder(q, stack_r, stack_r, stack_h, 14, 2)
            glTranslatef(0, 0, stack_h)
            gluDisk(q, 0, stack_r, 14, 1)
            glPopMatrix()
        gluDeleteQuadric(q)

    # Roof beacon (amber).
    if truck['beacon']:
        _car_mat_emit((1.0, 0.55, 0.05))
        q = gluNewQuadric()
        bx = (truck['cab_rear_top'] + truck['cab_front_top']) / 2
        by = truck['roof_y'] + 0.06
        glPushMatrix()
        glTranslatef(bx, by, 0)
        glScalef(0.12, 0.06, 0.20)
        gluSphere(q, 1.0, 14, 10)
        glPopMatrix()
        gluDeleteQuadric(q)
        _car_mat_clear()

    # Grille with horizontal chrome bars + chrome bumper.
    _car_mat_grille()
    fx = truck['front_x'] - 0.01
    gy_top = truck['body_top_y'] * 0.80
    gy_bot = truck['BH'] * 0.85
    gz = W * 0.32
    glBegin(GL_QUADS)
    glNormal3f(1, 0, 0)
    glVertex3f(fx, gy_bot,  gz)
    glVertex3f(fx, gy_bot, -gz)
    glVertex3f(fx, gy_top, -gz)
    glVertex3f(fx, gy_top,  gz)
    glEnd()
    _car_mat_chrome()
    nb = 4
    for i in range(nb):
        t = (i + 0.5) / nb
        ys = gy_bot + (gy_top - gy_bot) * t
        glBegin(GL_QUADS)
        glNormal3f(1, 0, 0)
        glVertex3f(fx + 0.005, ys - 0.015,  gz)
        glVertex3f(fx + 0.005, ys - 0.015, -gz)
        glVertex3f(fx + 0.005, ys + 0.015, -gz)
        glVertex3f(fx + 0.005, ys + 0.015,  gz)
        glEnd()
    _car_mat_chrome()
    bs_top = truck['BH'] * 0.80
    bs_bot = truck['BH'] * 0.30
    glBegin(GL_QUADS)
    glNormal3f(1, 0, 0)
    glVertex3f(fx + 0.008, bs_bot,  W * 0.46)
    glVertex3f(fx + 0.008, bs_bot, -W * 0.46)
    glVertex3f(fx + 0.008, bs_top, -W * 0.46)
    glVertex3f(fx + 0.008, bs_top,  W * 0.46)
    glEnd()

    # Headlights: large rectangular lenses on the front face.
    head_y = truck['body_top_y'] * 0.72
    head_half_w = W * 0.12
    head_half_h = 0.10
    head_center_z = W * 0.36
    fx2 = truck['front_x']
    for zc in (head_center_z, -head_center_z):
        _car_mat_dark()
        bz = head_half_w + 0.018
        by = head_half_h + 0.014
        glBegin(GL_QUADS)
        glNormal3f(1, 0, 0)
        glVertex3f(fx2 + 0.008, head_y - by, zc - bz)
        glVertex3f(fx2 + 0.008, head_y - by, zc + bz)
        glVertex3f(fx2 + 0.008, head_y + by, zc + bz)
        glVertex3f(fx2 + 0.008, head_y + by, zc - bz)
        glEnd()
        _car_mat_emit((1.0, 0.97, 0.82))
        glBegin(GL_QUADS)
        glNormal3f(1, 0, 0)
        glVertex3f(fx2 + 0.014, head_y - head_half_h, zc - head_half_w)
        glVertex3f(fx2 + 0.014, head_y - head_half_h, zc + head_half_w)
        glVertex3f(fx2 + 0.014, head_y + head_half_h, zc + head_half_w)
        glVertex3f(fx2 + 0.014, head_y + head_half_h, zc - head_half_w)
        glEnd()
        _car_mat_clear()

    # Taillights: larger proportional red lenses on the rear face.
    tail_y = truck['body_top_y'] * 0.70
    tail_half_w = W * 0.13
    tail_half_h = 0.085
    tail_center_z = W * 0.36
    rx = truck['rear_x']
    for zc in (tail_center_z, -tail_center_z):
        _car_mat_dark()
        bz = tail_half_w + 0.022
        by = tail_half_h + 0.018
        glBegin(GL_QUADS)
        glNormal3f(-1, 0, 0)
        glVertex3f(rx - 0.008, tail_y - by, zc + bz)
        glVertex3f(rx - 0.008, tail_y - by, zc - bz)
        glVertex3f(rx - 0.008, tail_y + by, zc - bz)
        glVertex3f(rx - 0.008, tail_y + by, zc + bz)
        glEnd()
        _car_mat_emit((1.0, 0.08, 0.06))
        glBegin(GL_QUADS)
        glNormal3f(-1, 0, 0)
        glVertex3f(rx - 0.014, tail_y - tail_half_h, zc + tail_half_w)
        glVertex3f(rx - 0.014, tail_y - tail_half_h, zc - tail_half_w)
        glVertex3f(rx - 0.014, tail_y + tail_half_h, zc - tail_half_w)
        glVertex3f(rx - 0.014, tail_y + tail_half_h, zc + tail_half_w)
        glEnd()
        _car_mat_clear()
    # High-mount centre brake strip on the back of the cab / cargo top.
    _car_mat_emit((1.0, 0.12, 0.08))
    strip_half_w = W * 0.22
    strip_half_h = 0.025
    strip_y = truck['body_top_y'] * 0.92
    glBegin(GL_QUADS)
    glNormal3f(-1, 0, 0)
    glVertex3f(rx - 0.014, strip_y - strip_half_h,  strip_half_w)
    glVertex3f(rx - 0.014, strip_y - strip_half_h, -strip_half_w)
    glVertex3f(rx - 0.014, strip_y + strip_half_h, -strip_half_w)
    glVertex3f(rx - 0.014, strip_y + strip_half_h,  strip_half_w)
    glEnd()
    _car_mat_clear()

    # Mud flaps behind the rearmost axle.
    if truck['mud_flaps']:
        _car_mat_dark()
        rear_axle_x = min(w[0] for w in truck['wheels'])
        flap_x = rear_axle_x - truck['WR'] * 0.95
        for sign in (1, -1):
            glPushMatrix()
            glTranslatef(flap_x, truck['WR'] * 0.55,
                         sign * (W / 2 - 0.05))
            glScalef(0.03, truck['WR'] * 1.1, 0.28)
            _car_unit_cube()
            glPopMatrix()

    # Tow hitch (chrome ball + dark bar).
    _car_mat_chrome()
    q = gluNewQuadric()
    glPushMatrix()
    glTranslatef(truck['rear_x'] - 0.05, truck['BH'] * 0.55, 0)
    gluSphere(q, 0.055, 12, 10)
    glPopMatrix()
    gluDeleteQuadric(q)
    _car_mat_dark()
    _truck_draw_box(truck['rear_x'] - 0.10, truck['rear_x'] + 0.02,
                    truck['BH'] * 0.45, truck['BH'] * 0.60,
                    -0.05, 0.05)


def build_truck_variant(seed):
    rng = np.random.default_rng(seed)
    truck = _generate_truck_params(rng)
    paint_tex = make_car_paint_texture(truck['color'], rng)
    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    _car_draw_lower_body(truck, paint_tex)
    _car_draw_cabin(truck, paint_tex)
    _truck_draw_cargo(truck, paint_tex)
    _truck_draw_details(truck)
    for (wx, wy, wz) in truck['wheels']:
        _car_draw_wheel(wx, wy, wz, truck['WR'],
                        1 if wz > 0 else -1, truck['spoke_count'])
    glEndList()
    return {
        'list': list_id, 'paint_tex': paint_tex,
        'L': truck['L'], 'W': truck['W'],
        'head_x': truck['front_x'] + 0.015,
        'tail_x': truck['rear_x'] - 0.015,
        'head_y': truck['body_top_y'] * 0.72,
        'tail_y': truck['body_top_y'] * 0.70,
        'head_zoff': truck['W'] * 0.36,
        'tail_zoff': truck['W'] * 0.36,
    }


def build_truck_variants(n=N_TRUCK_VARIANTS):
    return [build_truck_variant(7000 + i) for i in range(n)]


def init_trucks(seed=707, s_car=0.0, player_speed=0.0):
    return init_cars(seed=seed, s_car=s_car, player_speed=player_speed,
                     n_variants=N_TRUCK_VARIANTS,
                     n_per_lane=N_TRUCKS_PER_LANE)


def draw_trucks(state, truck_variants, s_car, amb_rgb, sun_dir,
                night_a, flare_tex):
    # draw_cars is vehicle-agnostic: it just iterates state['cars'] and
    # calls `variants[c['variant']]['list']`. We pass the truck state and
    # truck variant pool and reuse it verbatim — including the night
    # headlight/taillight billboard pass.
    draw_cars(state, truck_variants, s_car, amb_rgb, sun_dir,
              night_a, flare_tex)


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
    overcast_tex = upload_texture(make_overcast_texture(),
                                    internal=GL_RGBA, src=GL_RGBA)
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
    # Volumetric dust — reuse the soft-disc snowflake texture (same shape,
    # we tint it warm at draw time) to avoid another tiny RGBA upload.
    dust_state = init_dust()
    # One facade texture per palette — buildings pick an index per variant
    # so the skyline shows brick next to limestone next to dark glass
    # without each building needing its own bespoke tile set.
    facade_texes = [
        upload_texture(make_facade_texture(palette=p,
                                            seed=51 + i * 11))
        for i, p in enumerate(FACADE_PALETTES)
    ]
    # Emission uploaded *without* mipmaps: lit windows occupy only a few
    # texels; mip minification averages them with transparent neighbours
    # and the glow fades to nothing as the tex drops mip levels. Linear
    # min filter preserves the punch.
    emission_tex = upload_texture(make_facade_emission_texture(),
                                    internal=GL_RGBA, src=GL_RGBA,
                                    mipmaps=False)
    building_lists = build_building_variants()
    concrete_tex = upload_texture(make_concrete_texture())
    # Rural houses — four wall textures × three roof textures worth of
    # material variety, cross-combined into eight baked variants.
    house_wall_texes = [
        upload_texture(make_brick_wall_texture()),
        upload_texture(make_wood_siding_texture()),
        upload_texture(make_plaster_texture()),
        upload_texture(make_stone_wall_texture()),
    ]
    house_roof_texes = [
        upload_texture(make_tile_roof_texture()),
        upload_texture(make_shingle_roof_texture()),
        upload_texture(make_slate_roof_texture()),
    ]
    house_variants = build_house_variants(house_wall_texes, house_roof_texes)
    # Procedural traffic: a pool of lofted-superellipse car variants
    # driven along both lanes. Built once at startup; cars are looked-up
    # by index per frame.
    car_variants = build_car_variants()
    car_state = init_cars(player_speed=SPEED)
    # Trucks share the traffic logic but run ~1/4 the density of cars and
    # spawn from their own pool of lofted variants (pickup / box / semi /
    # flatbed / dump). Same two-lane flow: left side overtakes, right is
    # oncoming.
    truck_variants = build_truck_variants()
    truck_state = init_trucks(player_speed=SPEED)
    # Flowers: six colour palettes, each compiled as a crossed-quad
    # billboard display list.
    flower_variants = []
    for idx, (petal_rgb, center_rgb) in enumerate(FLOWER_PALETTES):
        flower_tex = upload_texture(
            make_flower_texture(petal_rgb, center_rgb, seed=idx),
            internal=GL_RGBA, src=GL_RGBA,
        )
        flower_variants.append(build_flower_variant(flower_tex))
    rain_state = init_rain()
    pond_tex = upload_texture(make_pond_texture(), internal=GL_RGBA, src=GL_RGBA)
    flare_tex = upload_texture(make_flare_disc_texture(), internal=GL_RGBA, src=GL_RGBA)
    flare_smoothed = 0.0
    # Lens weather overlay — droplets + snowflakes on the "camera lens".
    lens_drop_tex = upload_texture(make_lens_drop_texture(),
                                    internal=GL_RGBA, src=GL_RGBA)
    lens_flake_tex = upload_texture(make_lens_flake_texture(),
                                     internal=GL_RGBA, src=GL_RGBA)
    lens_overlay = LensWeatherOverlay()

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
    # Separate CA electric-charge state. Life is measured in frames so
    # the flash is genuinely brief — 1 to 4 frames (about 16-66 ms at
    # 60 fps), chosen randomly when the charge is spawned.
    active_ca_charge = None
    ca_charge_frames_left = 0
    ca_time_to_strike = 5.0
    # Seed the EMA with the starting storm value so we don't ramp in from 0
    storm_smoothed = storm_intensity_at(0.0)
    sv, stc, sfr, sidx = build_sky_dome()
    sky_state = (sv, stc, sfr, sidx, cloud_tex, stars_tex, overcast_tex)

    clock = pygame.time.Clock()
    s_car = 0.0
    t_time = 0.0
    # start near dawn so first sight is pretty
    t_day = 0.18
    speed = SPEED
    camera_yaw = 0.0    # degrees; + rotates view right, - rotates left
    manual_strike_queued = False
    manual_ca_queued = False
    running = True
    while running:
        dt = clock.tick(60) / 1000.0
        for e in pygame.event.get():
            if e.type == QUIT:
                running = False
            elif e.type == KEYDOWN:
                if e.key == K_ESCAPE:
                    running = False
                elif e.key == K_SPACE:
                    # Re-center the camera. Snap back to forward view.
                    camera_yaw = 0.0
                elif e.key == K_t:
                    # Manual lightning trigger — convenient while
                    # exploring; still goes through the normal bolt +
                    # flash pipeline + thunder clap.
                    manual_strike_queued = True

        # Up/Down + Left/Right: held-key polling so changes feel
        # proportional to how long the key is held.
        keys = pygame.key.get_pressed()
        if keys[K_UP]:
            speed += SPEED_ACCEL * dt
        if keys[K_DOWN]:
            speed -= SPEED_ACCEL * dt
        if speed < SPEED_MIN:
            speed = SPEED_MIN
        elif speed > SPEED_MAX:
            speed = SPEED_MAX
        if keys[K_LEFT]:
            camera_yaw -= CAMERA_YAW_RATE * dt
        if keys[K_RIGHT]:
            camera_yaw += CAMERA_YAW_RATE * dt
        if camera_yaw < -CAMERA_YAW_LIMIT:
            camera_yaw = -CAMERA_YAW_LIMIT
        elif camera_yaw > CAMERA_YAW_LIMIT:
            camera_yaw = CAMERA_YAW_LIMIT

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

        # Apply Left/Right camera yaw: rotate the look-vector around the
        # world Y axis pinned at the camera. Positive yaw turns right
        # (look drifts toward +X), negative yaw turns left. Pitch (look
        # y) is preserved, so the camera only pivots horizontally.
        if camera_yaw != 0.0:
            yaw_r = math.radians(camera_yaw)
            cos_y = math.cos(yaw_r)
            sin_y = math.sin(yaw_r)
            fx = lx - cx
            fz = lz - cz
            rx = fx * cos_y - fz * sin_y
            rz = fx * sin_y + fz * cos_y
            lx = cx + rx
            lz = cz + rz

        gluLookAt(cx, cy, cz, lx, ly, lz, 0.0, 1.0, 0.0)

        # Try to trigger a new bolt now that we have the camera position.
        # Strikes are deliberately rare: poll once per second, low success
        # Regular fractal bolt — ~2 % of rain wall-clock
        time_to_strike -= dt
        triggered_bolt = False
        if active_bolt is None and manual_strike_queued:
            active_bolt = generate_bolt(bolt_rng, cx, cy, cz)
            bolt_age = 0.0
            time_to_strike = float(bolt_rng.uniform(3.0, 6.0))
            triggered_bolt = True
            # T-key also fires a CA electric charge beside the bolt
            manual_ca_queued = True
            manual_strike_queued = False
        elif active_bolt is None and time_to_strike <= 0.0:
            if storm_i > 0.25 and bolt_rng.random() < storm_i * 0.35:
                active_bolt = generate_bolt(bolt_rng, cx, cy, cz)
                bolt_age = 0.0
                time_to_strike = float(bolt_rng.uniform(3.0, 8.0))
                triggered_bolt = True
            else:
                time_to_strike = 1.0

        if triggered_bolt and audio_player is not None:
            thunder_vol = 0.40 + max(storm_i, 0.5) * 0.40 \
                          + float(bolt_rng.uniform(-0.05, 0.15))
            audio_player.trigger_thunder(volume=thunder_vol)

        # CA electric charge — *separate* flash, 1-4 frames of full
        # brightness. Fires independently on rare dice rolls during
        # storms OR alongside a manual T-key trigger. Never lingers
        # more than 4 frames.
        if active_ca_charge is not None:
            ca_charge_frames_left -= 1
            if ca_charge_frames_left <= 0:
                active_ca_charge = None

        ca_time_to_strike -= dt
        fired_ca = False
        if active_ca_charge is None:
            if manual_ca_queued:
                active_ca_charge = generate_ca_charge(bolt_rng, cx, cy, cz)
                ca_charge_frames_left = int(bolt_rng.integers(1, 5))  # 1-4
                ca_time_to_strike = float(bolt_rng.uniform(6.0, 14.0))
                manual_ca_queued = False
                fired_ca = True
            elif ca_time_to_strike <= 0.0:
                # Rarer than the fractal bolt — about 1/3 the chance so the
                # flash feels like a special event within a storm.
                if storm_i > 0.25 and bolt_rng.random() < storm_i * 0.12:
                    active_ca_charge = generate_ca_charge(bolt_rng, cx, cy, cz)
                    ca_charge_frames_left = int(bolt_rng.integers(1, 5))
                    ca_time_to_strike = float(bolt_rng.uniform(10.0, 28.0))
                    fired_ca = True
                else:
                    ca_time_to_strike = 2.0

        if fired_ca and audio_player is not None:
            # Shorter, tighter clap than a regular bolt — the CA flash
            # is a quick bright snap, not a sustained rumble.
            audio_player.trigger_thunder(
                volume=0.30 + max(storm_i, 0.4) * 0.30
            )

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
        draw_city(s_car, building_lists, facade_texes, emission_tex,
                  amb, night_a, t_time,
                  storm_i=storm_i, frost_i=frost_i,
                  snow_tex=snow_ground_tex)
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
        # Rural houses: farther than trees, closer than city skyscrapers.
        # Snow accumulates on roofs during frost biome, melts off when
        # the biome transitions back to plain/forest.
        draw_houses(s_car, house_variants, snow_ground_tex, amb, night_a)
        # Flowers along the shoulders — same wind as the trees so the
        # whole landscape pulses together when a gust rolls through.
        draw_flowers(s_car, flower_variants, amb, t_time, wind_strength)
        # Road pavement with subtle wet darkening during rain, then the
        # snow overlay pass that fades in/out with frost biome transitions.
        draw_road(road_tex, s_car, amb, storm_i)
        draw_road_snow_overlay(snow_ground_tex, s_car, amb)
        # Ponds draw *after* the road so any puddle sits on top of the
        # pavement (including its imperfections and snow overlay). This
        # is the reason cracks / dirt never show through a puddle — the
        # puddle's alpha composite is the last paint.
        draw_ponds(pond_tex, s_car, storm_i, horizon, amb, t_time)
        # Bridges + tunnels: placed where biomes dictate (river / mountain)
        # so they never collide with trees, buildings, or other structures.
        # Concrete tint reacts to storm (wet darkening) and frost (snow cap).
        draw_civil_structures(s_car, concrete_tex, amb, storm_i, frost_i)
        draw_snow_shoulders(snow_ground_tex, s_car, amb)
        draw_lamps(s_car, night_a)

        # Procedural traffic. Two lanes: player's left lane travels the
        # same direction as the player (cars recede), right lane is
        # oncoming (cars close toward the camera).
        update_cars(car_state, dt, s_car, speed)
        draw_cars(car_state, car_variants, s_car, amb, sun_d,
                  night_a, flare_tex)
        update_cars(truck_state, dt, s_car, speed)
        draw_trucks(truck_state, truck_variants, s_car, amb, sun_d,
                    night_a, flare_tex)

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

        # Volumetric dust motes — rare, gated so they only appear in
        # *sunny* moments. Hard sunny gate (storm ≈ 0 AND bright ≈ 1)
        # via smooth thresholds, multiplied by the open-biome exposure
        # and by the already-rare dust_intensity_at(t_time) event
        # function. Peak overall intensity ~0.5 so the effect stays a
        # soft haze, never a fog bank.
        sunny_gate = _smooth((1.0 - storm_i - 0.05) / 0.25) \
                     * _smooth((bright - 0.7) / 0.2)
        dust_base = dust_intensity_at(t_time) * sunny_gate
        dust_base *= (0.30 + 0.70 * open_exp_trees)
        dust_i = min(0.55, dust_base)
        update_dust(dust_state, dt, t_time)
        draw_dust(dust_state, snowflake_tex, dust_i, cx, cy, cz, amb)

        # lightning bolt (if one is currently active)
        draw_bolt(active_bolt, bolt_age)
        # CA electric-charge flash (separate system, frame-counted life)
        draw_ca_charge(active_ca_charge)

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

        # Lens weather drops: spawn + advance + draw, gated by rain/snow
        # intensity so they only appear in their respective weather.
        rain_lens_i = storm_i * (1.0 - frost_i)
        snow_lens_i = frost_i
        lens_overlay.update(dt, rain_lens_i, snow_lens_i, W, H)
        lens_overlay.draw(lens_drop_tex, lens_flake_tex, W, H)

        pygame.display.flip()

    if piano_player is not None:
        piano_player.stop()
    if audio_player is not None:
        audio_player.stop()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
