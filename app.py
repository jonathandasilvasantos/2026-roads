import math
import sys
import numpy as np
import pygame
from pygame.locals import DOUBLEBUF, OPENGL, FULLSCREEN, QUIT, KEYDOWN, K_ESCAPE
import random
from OpenGL.GL import *
from OpenGL.GLU import (
    gluPerspective, gluLookAt, gluNewQuadric, gluQuadricTexture,
    gluQuadricNormals, gluCylinder, GLU_SMOOTH,
)


# --- Road ---
ROAD_WIDTH = 10.0
SEG_LEN = 2.0
N_SEG = 180
SPEED = 28.0
CAM_HEIGHT = 3.4
CAM_BACK = 5.0
LOOK_AHEAD = 20.0

# --- Lamps ---
LAMP_SPACING = 10.0
N_LAMPS = 40

# --- Terrain ---
K_BANDS = 14
D_STEP = 6.0
TERRAIN_EDGE_D = 0.1

# --- Biomes ---
ZONE_LEN = 240.0
TRANS_LEN = 45.0
(BIOME_PLAIN, BIOME_HILL, BIOME_MOUNTAIN, BIOME_RIVER,
 BIOME_FOREST, BIOME_FROST) = 0, 1, 2, 3, 4, 5
BIOME_COUNT = 6

BIOME_COLOR = np.array([
    [0.46, 0.70, 0.26],   # plain grass
    [0.32, 0.56, 0.20],   # hill grass
    [0.62, 0.54, 0.46],   # mountain rock
    [0.26, 0.48, 0.68],   # river water
    [0.20, 0.36, 0.14],   # forest floor (dark undergrowth)
    [0.88, 0.92, 1.00],   # frost (snow field, cold blue-white)
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
SKY_DOME_R = 400.0


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
    key = (zone_idx * 2654435761 + (0 if side < 0 else 9277) + 1013904223) & 0xFFFFFFFF
    return key % BIOME_COUNT


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
    return plain, hill, mountain, river, forest, frost


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


def sky_colors_at(t_day):
    t = t_day % 1.0
    for i in range(len(SKY_KEYS) - 1):
        t0, z0, h0 = SKY_KEYS[i]
        t1, z1, h1 = SKY_KEYS[i + 1]
        if t0 <= t <= t1:
            a = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return _lerp3(z0, z1, a), _lerp3(h0, h1, a)
    return SKY_KEYS[0][1], SKY_KEYS[0][2]


def ambient_at(t_day):
    """Returns (brightness scalar, RGB tint) affecting terrain/road."""
    el = sun_dir_at(t_day)[1]
    # brightness ramps from 0.20 (deep night) to 1.0 at sun_el >= 0.35
    day = _smooth((el + 0.15) / 0.50)
    bright = 0.22 + 0.78 * day
    # Warm tint near sunrise/sunset, neutral at noon, cool-blue at night
    warm = _smooth(1.0 - abs(el) / 0.25) * (1.0 if el > -0.25 else 0.0)
    # cold factor at night
    cold = _smooth((-el - 0.10) / 0.60)
    r = 1.0 + 0.20 * warm - 0.25 * cold
    g = 1.0 + 0.05 * warm - 0.18 * cold
    b = 1.0 - 0.15 * warm + 0.15 * cold
    return bright, (r, g, b)


def night_factor_at(t_day):
    """1 at deep night, 0 during day — gates lamps and stars."""
    el = sun_dir_at(t_day)[1]
    return _smooth((-el + 0.05) / 0.35)


def cloud_tint_at(t_day):
    el = sun_dir_at(t_day)[1]
    day = _smooth((el + 0.15) / 0.50)
    warm = _smooth(1.0 - abs(el) / 0.25) * (1.0 if el > -0.25 else 0.0)
    # base: dim gray at night, white noon, orange at sunrise/sunset
    base_r = 0.35 + 0.65 * day
    base_g = 0.38 + 0.60 * day
    base_b = 0.45 + 0.55 * day
    r = base_r + 0.30 * warm
    g = base_g + 0.10 * warm
    b = base_b - 0.05 * warm
    return (min(1.0, r), min(1.0, g), min(1.0, b))


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
    """Sparse star field with varying brightness. u wraps horizontally."""
    rng = np.random.default_rng(19)
    tex = np.zeros((h, w), dtype=np.float32)
    for _ in range(2400):
        y = int(rng.integers(0, h))
        x = int(rng.integers(0, w))
        brightness = float(rng.random() ** 2.5)
        if rng.random() < 0.15:
            brightness *= 2.0
        size = 1 if rng.random() < 0.82 else 2
        y0, y1 = max(0, y - size + 1), min(h, y + size)
        x0, x1 = max(0, x - size + 1), min(w, x + size)
        tex[y0:y1, x0:x1] = np.maximum(tex[y0:y1, x0:x1], brightness)
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


def draw_sky(sky_state, cam_x, cam_y, cam_z, t_time, t_day):
    verts, tcs, vfrac, idx, cloud_tex, stars_tex = sky_state
    zenith, horizon = sky_colors_at(t_day)
    colors = compute_dome_colors(vfrac, zenith, horizon)
    night_a = night_factor_at(t_day)
    cloud_tint = cloud_tint_at(t_day)

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
    NS = len(s_arr)
    K = K_BANDS
    d = TERRAIN_EDGE_D + np.arange(K, dtype=np.float32) * D_STEP
    rx = curve_x_np(s_arr)
    ry = curve_y_np(s_arr)
    rz = -(s_arr - s_car)
    weights = biome_weights_vec(s_arr, side)
    s2 = s_arr[:, None]
    d2 = d[None, :]
    plain, hill, mnt, river, forest, frost = terrain_heights(s2, d2, t_time)
    off = (weights[:, 0:1] * plain + weights[:, 1:2] * hill
           + weights[:, 2:3] * mnt + weights[:, 3:4] * river
           + weights[:, 4:5] * forest + weights[:, 5:6] * frost)
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
    return verts, col, tcs


def draw_terrain(terrain_tex, s_car, t_time, amb_rgb):
    NS = N_SEG + 1
    s_arr = (np.arange(NS, dtype=np.float32) * SEG_LEN) + s_car
    idx = grid_indices(NS, K_BANDS)
    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, terrain_tex)
    glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
    glEnableClientState(GL_VERTEX_ARRAY)
    glEnableClientState(GL_COLOR_ARRAY)
    glEnableClientState(GL_TEXTURE_COORD_ARRAY)
    for side in (-1, +1):
        verts, cols, tcs = build_side_arrays(s_arr, s_car, side, t_time, amb_rgb)
        glVertexPointer(3, GL_FLOAT, 0, verts)
        glColorPointer(3, GL_FLOAT, 0, cols)
        glTexCoordPointer(2, GL_FLOAT, 0, tcs)
        glDrawElements(GL_TRIANGLES, len(idx), GL_UNSIGNED_INT, idx)
    glDisableClientState(GL_VERTEX_ARRAY)
    glDisableClientState(GL_COLOR_ARRAY)
    glDisableClientState(GL_TEXTURE_COORD_ARRAY)


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


def draw_forest(s_car, tree_lists, frost_tree_lists, amb_rgb):
    """Instance baked tree lists across positions in forest/frost biome zones.
    Picks the frost (snow-covered) variant set where the slot is in a frost
    zone; forest zones get the normal green variants.
    """
    s_start = math.floor(s_car / TREE_SPACING) * TREE_SPACING
    max_s = s_car + N_SEG * SEG_LEN
    n_steps = int((max_s - s_start) / TREE_SPACING) + 1
    nvar = len(tree_lists)

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


def draw_snow(state, snow_tex, intensity, cam_x, cam_y, cam_z):
    """Renders point-sprite flakes translated to camera. `intensity` is the
    frost biome weight at camera — never draws above 0 outside frost zones."""
    if intensity < 0.02:
        return
    pos, _vel, _seed = state

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
    glColor4f(1.0, 1.0, 1.0, min(1.0, intensity * 1.2))
    glEnableClientState(GL_VERTEX_ARRAY)
    glVertexPointer(3, GL_FLOAT, 0, pos)
    glDrawArrays(GL_POINTS, 0, len(pos))
    glDisableClientState(GL_VERTEX_ARRAY)
    glPopMatrix()

    glDepthMask(GL_TRUE)
    glDisable(GL_BLEND)
    glDisable(GL_POINT_SPRITE)


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
    gluPerspective(68.0, W / float(H), 0.1, 1200.0)
    glMatrixMode(GL_MODELVIEW)

    glEnable(GL_FOG)
    glFogi(GL_FOG_MODE, GL_EXP2)
    glFogf(GL_FOG_DENSITY, 0.0055)

    road_tex = upload_texture(make_road_texture())
    terrain_tex = upload_texture(make_terrain_texture())
    cloud_tex = upload_texture(make_cloud_texture(), internal=GL_RGBA, src=GL_RGBA)
    stars_tex = upload_texture(make_stars_texture())
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
    sv, stc, sfr, sidx = build_sky_dome()
    sky_state = (sv, stc, sfr, sidx, cloud_tex, stars_tex)

    clock = pygame.time.Clock()
    s_car = 0.0
    t_time = 0.0
    # start near dawn so first sight is pretty
    t_day = 0.18
    running = True
    while running:
        dt = clock.tick(60) / 1000.0
        for e in pygame.event.get():
            if e.type == QUIT:
                running = False
            elif e.type == KEYDOWN and e.key == K_ESCAPE:
                running = False

        s_car += SPEED * dt
        t_time += dt
        t_day = (t_day + dt / DAY_PERIOD) % 1.0

        # day/night derived values
        zenith, horizon = sky_colors_at(t_day)
        bright, tint = ambient_at(t_day)
        amb = (tint[0] * bright, tint[1] * bright, tint[2] * bright)
        night_a = night_factor_at(t_day)
        sun_d = sun_dir_at(t_day)
        moon_d = -sun_d  # opposite

        # fog fades into the horizon color of the moment
        fog_c = (horizon[0] * bright * 0.9 + 0.05,
                 horizon[1] * bright * 0.9 + 0.05,
                 horizon[2] * bright * 0.9 + 0.05, 1.0)
        glFogfv(GL_FOG_COLOR, fog_c)
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

        draw_sky(sky_state, cx, cy, cz, t_time, t_day)

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

        draw_terrain(terrain_tex, s_car, t_time, amb)
        draw_forest(s_car, tree_lists, frost_tree_lists, amb)
        draw_road(road_tex, s_car, amb)
        draw_snow_shoulders(snow_ground_tex, s_car, amb)
        draw_lamps(s_car, night_a)

        # snowfall: only in frost biomes, intensity tracks frost weight at camera
        snow_i = frost_intensity_at(s_car)
        update_snow(snow_state, dt, t_time)
        draw_snow(snow_state, snowflake_tex, snow_i, cx, cy, cz)

        pygame.display.flip()

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
