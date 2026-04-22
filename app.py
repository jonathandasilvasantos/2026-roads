import math
import sys
import numpy as np
import pygame
from pygame.locals import DOUBLEBUF, OPENGL, FULLSCREEN, QUIT, KEYDOWN, K_ESCAPE
from OpenGL.GL import *
from OpenGL.GLU import gluPerspective, gluLookAt


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
BIOME_PLAIN, BIOME_HILL, BIOME_MOUNTAIN, BIOME_RIVER = 0, 1, 2, 3

BIOME_COLOR = np.array([
    [0.46, 0.70, 0.26],
    [0.32, 0.56, 0.20],
    [0.62, 0.54, 0.46],
    [0.26, 0.48, 0.68],
], dtype=np.float32)

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
    return key % 4


def biome_weights_vec(s_arr, side):
    NS = len(s_arr)
    out = np.zeros((NS, 4), dtype=np.float32)
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
    return plain, hill, mountain, river


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
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
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
    plain, hill, mnt, river = terrain_heights(s2, d2, t_time)
    off = (weights[:, 0:1] * plain + weights[:, 1:2] * hill
           + weights[:, 2:3] * mnt + weights[:, 3:4] * river)
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
        draw_road(road_tex, s_car, amb)
        draw_lamps(s_car, night_a)

        pygame.display.flip()

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
