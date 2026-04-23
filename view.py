"""Single-object procedural viewer for the roads project.

A lightweight companion to `app.py` that isolates one procedural asset
(house, building, mountain, flower, tree, car, truck) and renders it at
the centre of a turntable stage. Intended for tight iteration loops on
the generators themselves — changing a shape parameter, an emission
colour, or a wind-sway constant and seeing the effect on one instance
without running the whole driving simulation.

Everything is CLI-driven so the script can be used interactively or
wired into an automation loop:

  # Interactive (fullscreen, day, sunny):
  python view.py --object house

  # Night view of a building with lit windows, auto-rotate, windowed:
  python view.py --object building --time 21 --auto-rotate 30 --windowed

  # Headless screenshot burst — four angles, rainy evening:
  python view.py --object house --weather rain --time 19.5 \
                  --angles 0,90,180,270 --screenshot out/house_rain.png \
                  --exit-after 0.0

Keybindings while interactive:
  Esc / Q         quit
  Arrow keys      orbit (left/right yaw, up/down pitch)
  +  /  -         zoom in / zoom out
  S               save a screenshot (path comes from --screenshot, else
                  `view_<object>_<seed>_<timestamp>.png`)
  Space           reset camera to default

The renderer follows app.py's conventions: fixed-function OpenGL,
per-object display lists, additive night emission pass for windows, a
lens weather overlay for rain/snow, and a fog/sky tinted by
time-of-day. It reuses the same builder functions and texture makers so
an object displayed here is pixel-compatible with the one the main
simulation would place in the world.

Research inspiration (for the 'why' behind the model):
  * Parish & Müller 2001, "Procedural Modeling of Cities" — seeded
    grid-of-variants approach for skylines.
  * Wonka et al. 2003, "Instant Architecture" and Müller et al. 2006,
    "Procedural Modeling of Buildings" — split grammars produce
    facade subdivisions whose scars (ledges, pilasters, storefront
    bands) we mimic at texture-authoring time rather than at geometry-
    authoring time for speed.
  * Lipp et al. 2008 / Schwarz & Müller 2015 — interactive editing of
    facade grammars; the additive emission texture matches their
    night-vs-day attribute layering.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime

import numpy as np
import pygame
from pygame.locals import (
    DOUBLEBUF, OPENGL, FULLSCREEN, QUIT, KEYDOWN,
    K_ESCAPE, K_q, K_s, K_SPACE, K_UP, K_DOWN, K_LEFT, K_RIGHT,
    K_PLUS, K_EQUALS, K_MINUS, K_KP_PLUS, K_KP_MINUS,
)
from OpenGL.GL import *
from OpenGL.GLU import gluPerspective, gluLookAt

# app.py does side-effect imports (sounddevice, fluidsynth). Those are
# try/except-guarded inside app.py, so importing the module never runs
# main() and never requires audio hardware. We suppress the "audio
# disabled" help banner by setting a marker before import — any stray
# print to stderr is harmless but noisy for headless loops.
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import app  # noqa: E402


# -------------------------- CLI ----------------------------------------

OBJECTS = ("house", "building", "mountain", "flower", "tree", "car", "truck")
WEATHERS = ("clear", "rain", "snow", "storm")


def parse_angles(s: str):
    if not s:
        return None
    return [float(a.strip()) for a in s.split(",") if a.strip()]


def build_parser():
    p = argparse.ArgumentParser(
        description="Single-object procedural viewer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--object", "-o", choices=OBJECTS, required=True,
                   help="Which procedural asset to render.")
    p.add_argument("--seed", type=int, default=0,
                   help="Variant index. 0 picks the first baked variant; "
                        "larger values rotate through available variants "
                        "(or reseed the generator for mountain).")
    p.add_argument("--time", "-t", type=float, default=13.0,
                   help="Time of day in hours (0-24). 12=noon, 0=midnight.")
    p.add_argument("--weather", "-w", choices=WEATHERS, default="clear",
                   help="Atmospheric condition. Rain / snow enable the "
                        "lens overlay; storm also darkens the sky.")
    p.add_argument("--wind", type=float, default=0.15,
                   help="Wind strength 0-1 for tree/flower sway.")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--fullscreen", action="store_true",
                   help="Force fullscreen (default in --auto modes).")
    p.add_argument("--windowed", action="store_true",
                   help="Force windowed. Overrides --fullscreen.")
    p.add_argument("--auto-rotate", type=float, default=0.0,
                   metavar="DEG_PER_SEC",
                   help="Continuously rotate the stage around Y.")
    p.add_argument("--zoom", type=float, default=1.0,
                   help="Initial zoom multiplier (higher = closer).")
    p.add_argument("--yaw", type=float, default=25.0,
                   help="Initial camera yaw in degrees.")
    p.add_argument("--pitch", type=float, default=12.0,
                   help="Initial camera pitch in degrees.")
    p.add_argument("--screenshot", type=str, default=None,
                   metavar="PATH",
                   help="Save a screenshot on S key or on --exit-after. "
                        "With --angles, PATH becomes a prefix and the "
                        "angle is appended.")
    p.add_argument("--angles", type=str, default=None,
                   help="Comma-separated list of yaw angles (degrees) to "
                        "capture automatically. Forces headless exit "
                        "after the burst is complete.")
    p.add_argument("--exit-after", type=float, default=None,
                   metavar="SECONDS",
                   help="Close the window after N seconds. 0 means "
                        "'render one frame and exit' (useful for CI).")
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--ground", action="store_true", default=True,
                   help="Show a small ground plane under the object.")
    p.add_argument("--no-ground", dest="ground", action="store_false")
    return p


# --------------------- OpenGL setup ------------------------------------


def init_display(args):
    pygame.init()
    pygame.display.init()
    flags = DOUBLEBUF | OPENGL
    if args.fullscreen and not args.windowed:
        info = pygame.display.Info()
        W, H = info.current_w, info.current_h
        flags |= FULLSCREEN
    else:
        W, H = args.width, args.height
    pygame.display.set_mode((W, H), flags)
    pygame.display.set_caption(f"view.py — {args.object}")
    pygame.mouse.set_visible(True)

    glEnable(GL_DEPTH_TEST)
    glEnable(GL_LINE_SMOOTH)
    glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    gluPerspective(55.0, W / float(H), 0.05, 2000.0)
    glMatrixMode(GL_MODELVIEW)

    glEnable(GL_FOG)
    glFogi(GL_FOG_MODE, GL_LINEAR)
    glFogf(GL_FOG_START, 60.0)
    glFogf(GL_FOG_END, 400.0)
    return W, H


# -------------------- Object construction ------------------------------


def _ensure_house_textures(cache):
    if "house" in cache:
        return cache["house"]
    walls = [
        app.upload_texture(app.make_brick_wall_texture()),
        app.upload_texture(app.make_wood_siding_texture()),
        app.upload_texture(app.make_plaster_texture()),
        app.upload_texture(app.make_stone_wall_texture()),
    ]
    roofs = [
        app.upload_texture(app.make_tile_roof_texture()),
        app.upload_texture(app.make_shingle_roof_texture()),
        app.upload_texture(app.make_slate_roof_texture()),
    ]
    snow = app.upload_texture(app.load_texture_file(
        "textures/Snow001_1K-JPG_Color.jpg"))
    variants = app.build_house_variants(walls, roofs)
    cache["house"] = {"variants": variants, "snow": snow}
    return cache["house"]


def _build_procedural_mountain(seed=0, size=140.0, rings=28):
    """Generate a standalone mountain as a radial height-field mesh.

    Re-uses the same frequency palette as `terrain_heights`'s mountain
    branch so the silhouette matches the in-scene mountains. Radial
    falloff ensures the mesh is finite and the skirt drops to the
    ground plane cleanly for compositing on the flat stage.
    """
    rng = np.random.default_rng(1000 + seed * 7)
    nx = 64
    nz = 64
    xs = np.linspace(-size / 2, size / 2, nx)
    zs = np.linspace(-size / 2, size / 2, nz)
    X, Z = np.meshgrid(xs, zs, indexing="xy")
    R = np.sqrt(X * X + Z * Z)
    falloff = np.clip(1.0 - R / (size / 2), 0.0, 1.0) ** 1.4
    # mountain field (from terrain_heights), with d ↔ radius
    phase_s = rng.uniform(0, 10)
    phase_d = rng.uniform(0, 10)
    rise = np.clip(R / 18.0, 0.0, 1.0) ** 1.6
    h = (32.0 * rise * (0.55 + 0.40 * np.sin(X * 0.022 + phase_s))
         + 4.0 * rise * np.abs(np.sin(X * 0.057 + Z * 0.05 + phase_d))
         + 3.0 * rise * np.sin(X * 0.14 + Z * 0.24))
    h = h * falloff - 0.2
    # Colours: stone grey with snow cap on the upper fifth
    base = np.array([0.38, 0.36, 0.34], dtype=np.float32)
    snow = np.array([0.95, 0.96, 0.98], dtype=np.float32)
    peak = float(h.max())
    snow_line = peak * 0.62 if peak > 5.0 else 1e9
    def col_at(hy):
        t = np.clip((hy - snow_line) / max(1.0, peak - snow_line), 0.0, 1.0)
        return base * (1.0 - t) + snow * t
    list_id = glGenLists(1)
    glNewList(list_id, GL_COMPILE)
    glDisable(GL_TEXTURE_2D)
    for iz in range(nz - 1):
        glBegin(GL_TRIANGLE_STRIP)
        for ix in range(nx):
            for row in (iz, iz + 1):
                hy = float(h[row, ix])
                c = col_at(hy)
                # flat-shade normal pointing up — good enough for a stage
                glNormal3f(0.0, 1.0, 0.0)
                glColor3f(float(c[0]), float(c[1]), float(c[2]))
                glVertex3f(float(X[row, ix]), hy, float(Z[row, ix]))
        glEnd()
    glEndList()
    return list_id, (size, float(peak), size)


def build_object(obj_name, seed, cache):
    """Return (draw_fn, dims) for the requested object.

    draw_fn(ctx) is called every frame with a small context dict that
    carries live state (night_a, amb_rgb, frost, snow_i, wind, t_time)
    so the draw_fn can pick the right texture / emission pass.
    """
    if obj_name == "house":
        hc = _ensure_house_textures(cache)
        v = hc["variants"][seed % len(hc["variants"])]
        dims = v["dims"]

        def draw(ctx):
            glEnable(GL_TEXTURE_2D)
            glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
            amb = ctx["amb"]
            storm_i = ctx.get("storm", 0.0)
            wet = 1.0 - 0.18 * storm_i
            glBindTexture(GL_TEXTURE_2D, v["wall_tex"])
            tint = (min(1.0, amb[0] * wet),
                    min(1.0, amb[1] * wet),
                    min(1.0, amb[2] * wet))
            glColor3f(*tint)
            glCallList(v["body"])
            # body list no longer restores glColor; reassert tint before
            # each subsequent list so roof/chimney are also dim at night.
            glColor3f(*tint)
            glBindTexture(GL_TEXTURE_2D, v["roof_tex"])
            glCallList(v["roof"])
            if "chimney" in v:
                glColor3f(*tint)
                glCallList(v["chimney"])
            if ctx["frost"] > 0.04:
                glBindTexture(GL_TEXTURE_2D, hc["snow"])
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                glDepthMask(GL_FALSE)
                glEnable(GL_POLYGON_OFFSET_FILL)
                glPolygonOffset(-1.2, -1.2)
                a = min(1.0, ctx["frost"] * 0.95)
                glColor4f(amb[0] * 0.95, amb[1] * 0.97, amb[2] * 1.0, a)
                glCallList(v["roof"])
                glDisable(GL_POLYGON_OFFSET_FILL)
                glDepthMask(GL_TRUE)
                glDisable(GL_BLEND)
                glColor3f(1, 1, 1)
            night_a = ctx["night_a"]
            if night_a > 0.03:
                glDisable(GL_TEXTURE_2D)
                glEnable(GL_BLEND)
                glBlendFunc(GL_ONE, GL_ONE)
                glDepthFunc(GL_LEQUAL)
                glDepthMask(GL_FALSE)
                glColor4f(min(1.0, night_a * 1.10),
                          min(1.0, night_a * 0.92),
                          min(1.0, night_a * 0.58), 1.0)
                glCallList(v["emission"])
                glDepthFunc(GL_LESS)
                glDepthMask(GL_TRUE)
                glDisable(GL_BLEND)
                glEnable(GL_TEXTURE_2D)
            glColor3f(1, 1, 1)
        return draw, dims

    if obj_name == "building":
        if "building" not in cache:
            cache["building"] = {
                "facades": [
                    app.upload_texture(
                        app.make_facade_texture(palette=p, seed=51 + i * 11))
                    for i, p in enumerate(app.FACADE_PALETTES)
                ],
                # No mipmaps — mip averaging would wash out the lit-window
                # alpha and the additive emission would barely register.
                "emission": app.upload_texture(
                    app.make_facade_emission_texture(),
                    internal=GL_RGBA, src=GL_RGBA, mipmaps=False),
                "variants": app.build_building_variants(),
                "snow": app.upload_texture(app.load_texture_file(
                    "textures/Snow001_1K-JPG_Color.jpg")),
            }
        bc = cache["building"]
        v = bc["variants"][seed % len(bc["variants"])]
        dims = v["dims"]
        list_id = v["list"]
        pal = v.get("palette", 0) % len(bc["facades"])

        def draw(ctx):
            glEnable(GL_TEXTURE_2D)
            glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
            glBindTexture(GL_TEXTURE_2D, bc["facades"][pal])
            amb = ctx["amb"]
            storm_i = ctx.get("storm", 0.0)
            frost_i = ctx.get("frost", 0.0)
            wet = 1.0 - 0.20 * storm_i
            snow_bounce = 1.0 + 0.08 * frost_i
            glEnable(GL_LIGHTING)
            glEnable(GL_LIGHT0)
            glEnable(GL_COLOR_MATERIAL)
            glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
            glLightfv(GL_LIGHT0, GL_POSITION, (0.6, 1.0, 0.45, 0.0))
            glLightfv(GL_LIGHT0, GL_AMBIENT,
                      (amb[0] * 0.6, amb[1] * 0.6, amb[2] * 0.62, 1.0))
            glLightfv(GL_LIGHT0, GL_DIFFUSE,
                      (amb[0] * 0.7, amb[1] * 0.7, amb[2] * 0.72, 1.0))
            glLightfv(GL_LIGHT0, GL_SPECULAR, (0, 0, 0, 1))
            glColor3f(min(1.0, amb[0] * 0.95 * wet * snow_bounce),
                      min(1.0, amb[1] * 0.95 * wet * snow_bounce),
                      min(1.0, amb[2] * 0.98 * wet * snow_bounce))
            glCallList(list_id)
            glDisable(GL_LIGHTING)
            glDisable(GL_COLOR_MATERIAL)

            # Rooftop snow accumulation
            if frost_i > 0.04 and "snow_list" in v:
                glBindTexture(GL_TEXTURE_2D, bc["snow"])
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                glDepthMask(GL_FALSE)
                a = min(1.0, frost_i * 0.95)
                glColor4f(min(1.0, amb[0] * 0.95),
                          min(1.0, amb[1] * 0.98),
                          min(1.0, amb[2] * 1.02), a)
                glCallList(v["snow_list"])
                glDepthMask(GL_TRUE)
                glDisable(GL_BLEND)
                glColor3f(1, 1, 1)

            night_a = ctx["night_a"]
            if night_a > 0.03:
                glBindTexture(GL_TEXTURE_2D, bc["emission"])
                glEnable(GL_BLEND)
                glBlendFunc(GL_ONE, GL_ONE)
                glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_REPLACE)
                glDisable(GL_FOG)
                glDepthMask(GL_FALSE)
                # CRITICAL: the base pass wrote depth at every pixel of
                # the facade; re-drawing the same geometry now with the
                # default GL_LESS depth test would fail everywhere because
                # fragments are at EQUAL z, not LESS. Switch to LEQUAL so
                # the emission pass actually lands on top.
                glDepthFunc(GL_LEQUAL)
                for _ in range(2):
                    glCallList(list_id)
                glDepthFunc(GL_LESS)
                glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
                # Rooftop beacon: red blinking light, same math as
                # draw_city so the object reads identically on the stage.
                t = ctx["t_time"]
                bx, by, bz = v["beacon"]
                pulse = 0.5 + 0.5 * math.sin(t * 2.6)
                glDisable(GL_TEXTURE_2D)
                glColor4f(min(1.0, 1.0 * pulse * night_a),
                          min(1.0, 0.15 * pulse * night_a),
                          min(1.0, 0.10 * pulse * night_a), 1.0)
                glPushMatrix()
                glTranslatef(bx, by + 0.25, bz)
                size = 0.55
                glBegin(GL_QUADS)
                for rot in (0.0, 90.0):
                    cc = math.cos(math.radians(rot))
                    ss = math.sin(math.radians(rot))
                    glVertex3f(-size * cc, -size, -size * ss)
                    glVertex3f(size * cc, -size, size * ss)
                    glVertex3f(size * cc, size, size * ss)
                    glVertex3f(-size * cc, size, -size * ss)
                glEnd()
                glPopMatrix()
                glEnable(GL_TEXTURE_2D)
                glDepthMask(GL_TRUE)
                glDisable(GL_BLEND)
                glEnable(GL_FOG)
            glColor3f(1, 1, 1)
        return draw, dims

    if obj_name == "mountain":
        list_id, dims = _build_procedural_mountain(seed=seed)

        def draw(ctx):
            amb = ctx["amb"]
            glDisable(GL_TEXTURE_2D)
            glPushMatrix()
            glColor3f(min(1.0, amb[0]),
                      min(1.0, amb[1]),
                      min(1.0, amb[2]))
            glCallList(list_id)
            glPopMatrix()
            glColor3f(1, 1, 1)
        return draw, dims

    if obj_name == "tree":
        if "tree" not in cache:
            bark_rgb = app.load_texture_file(
                "textures/Bark001_1K-JPG_Color.jpg")
            bark = app.upload_texture(bark_rgb)
            leaf = app.upload_texture(
                app.make_leaf_texture(), internal=GL_RGBA, src=GL_RGBA)
            snow_bark = app.upload_texture(app.make_snow_bark_texture(bark_rgb))
            snow_leaf = app.upload_texture(
                app.make_snow_leaf_texture(), internal=GL_RGBA, src=GL_RGBA)
            cache["tree"] = {
                "variants": app.build_tree_variants(bark, leaf),
                "frost": app.build_tree_variants(snow_bark, snow_leaf),
            }
        tc = cache["tree"]

        def draw(ctx):
            glEnable(GL_TEXTURE_2D)
            glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
            amb = ctx["amb"]
            glColor3f(min(1.0, amb[0]), min(1.0, amb[1]), min(1.0, amb[2]))
            lists = tc["frost"] if ctx["frost"] > 0.5 else tc["variants"]
            lid = lists[seed % len(lists)]
            # Wind sway, same math as draw_forest but single-tree
            wind = ctx["wind"]
            t = ctx["t_time"]
            sx = wind * 2.5 * math.sin(t * 2.2)
            sz = wind * 1.8 * math.sin(t * 2.7 + 1.1)
            glPushMatrix()
            glRotatef(sx, 1, 0, 0)
            glRotatef(sz, 0, 0, 1)
            glCallList(lid)
            glPopMatrix()
            glDisable(GL_ALPHA_TEST)
            glColor3f(1, 1, 1)
        return draw, (3.0, 6.0, 3.0)

    if obj_name == "flower":
        if "flower" not in cache:
            variants = []
            for idx, (petal, centre) in enumerate(app.FLOWER_PALETTES):
                tex = app.upload_texture(
                    app.make_flower_texture(petal, centre, seed=idx),
                    internal=GL_RGBA, src=GL_RGBA)
                variants.append(app.build_flower_variant(tex))
            cache["flower"] = {"variants": variants}
        fv = cache["flower"]["variants"]
        lid = fv[seed % len(fv)]

        def draw(ctx):
            glEnable(GL_TEXTURE_2D)
            amb = ctx["amb"]
            glColor3f(min(1.0, amb[0]), min(1.0, amb[1]), min(1.0, amb[2]))
            glPushMatrix()
            # Scale up the unit billboard (~1m) so it's legible on-stage.
            glScalef(1.0, 1.0, 1.0)
            wind = ctx["wind"]
            t = ctx["t_time"]
            glRotatef(wind * 8.0 * math.sin(t * 3.0), 1, 0, 0)
            glCallList(lid)
            glPopMatrix()
            glDisable(GL_ALPHA_TEST)
            glColor3f(1, 1, 1)
        return draw, (1.0, 1.0, 1.0)

    if obj_name == "car":
        if "car" not in cache:
            cache["car"] = {"variants": app.build_car_variants()}
        cv = cache["car"]["variants"]
        car = cv[seed % len(cv)]
        # Car display lists are oriented with length on X, width on Z,
        # wheels sitting just above Y=0. Use L/W from the variant dict
        # and assume ~1.5 m overall height for camera framing.
        dims = (float(car["L"]), 1.5, float(car["W"]))

        def draw(ctx):
            glEnable(GL_LIGHTING)
            glEnable(GL_LIGHT0)
            amb = ctx["amb"]
            glLightfv(GL_LIGHT0, GL_POSITION, (0.6, 1.0, 0.45, 0.0))
            glLightfv(GL_LIGHT0, GL_AMBIENT,
                      (amb[0] * 0.6, amb[1] * 0.6, amb[2] * 0.62, 1.0))
            glLightfv(GL_LIGHT0, GL_DIFFUSE,
                      (amb[0] * 0.95, amb[1] * 0.95, amb[2] * 0.97, 1.0))
            glLightfv(GL_LIGHT0, GL_SPECULAR, (1, 1, 1, 1))
            glCallList(car["list"])
            glDisable(GL_LIGHTING)
        return draw, dims

    if obj_name == "truck":
        if "truck" not in cache:
            cache["truck"] = {"variants": app.build_truck_variants()}
        tv = cache["truck"]["variants"]
        truck = tv[seed % len(tv)]
        dims = (float(truck["L"]), 3.0, float(truck["W"]))

        def draw(ctx):
            glEnable(GL_LIGHTING)
            glEnable(GL_LIGHT0)
            amb = ctx["amb"]
            glLightfv(GL_LIGHT0, GL_POSITION, (0.6, 1.0, 0.45, 0.0))
            glLightfv(GL_LIGHT0, GL_AMBIENT,
                      (amb[0] * 0.6, amb[1] * 0.6, amb[2] * 0.62, 1.0))
            glLightfv(GL_LIGHT0, GL_DIFFUSE,
                      (amb[0] * 0.95, amb[1] * 0.95, amb[2] * 0.97, 1.0))
            glLightfv(GL_LIGHT0, GL_SPECULAR, (1, 1, 1, 1))
            glCallList(truck["list"])
            glDisable(GL_LIGHTING)
        return draw, dims

    raise SystemExit(f"unknown object: {obj_name}")


# --------------------- Stage / ground ----------------------------------


def draw_ground(amb, frost_i, size=60.0):
    """Large neutral ground plane so the object isn't floating in fog."""
    glDisable(GL_TEXTURE_2D)
    r = 0.38 * amb[0]
    g = 0.42 * amb[1]
    b = 0.32 * amb[2]
    if frost_i > 0.05:
        t = min(1.0, frost_i)
        r = r * (1 - t) + 0.92 * amb[0] * t
        g = g * (1 - t) + 0.94 * amb[1] * t
        b = b * (1 - t) + 0.99 * amb[2] * t
    glColor3f(r, g, b)
    y = -0.05
    glBegin(GL_QUADS)
    glNormal3f(0, 1, 0)
    glVertex3f(-size, y, -size)
    glVertex3f(size, y, -size)
    glVertex3f(size, y, size)
    glVertex3f(-size, y, size)
    glEnd()
    glColor3f(1, 1, 1)


# --------------------- Screenshot --------------------------------------


def save_screenshot(path, W, H):
    """Capture the current front buffer. Call this AFTER pygame.display.flip()
    so the just-rendered frame is what we grab. Reading GL_FRONT (instead
    of the default GL_BACK) avoids the classic double-buffer mistake
    where the screenshot lags one frame behind the on-screen view."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    glFinish()
    glReadBuffer(GL_FRONT)
    buf = glReadPixels(0, 0, W, H, GL_RGB, GL_UNSIGNED_BYTE)
    glReadBuffer(GL_BACK)
    img = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3)
    img = np.flipud(img)  # OpenGL origin is bottom-left
    surface = pygame.image.fromstring(img.tobytes(), (W, H), "RGB")
    pygame.image.save(surface, path)
    print(f"saved: {path}", file=sys.stderr)


def screenshot_path_for(base, angle):
    if not base:
        return None
    stem, ext = os.path.splitext(base)
    if not ext:
        ext = ".png"
    return f"{stem}_yaw{int(round(angle))}{ext}"


# --------------------- Main loop ---------------------------------------


def time_hours_to_t_day(hours):
    return (hours / 24.0) % 1.0


def weather_to_params(weather):
    """Map a weather string to (storm_i, rain_i, snow_i, frost_i)."""
    if weather == "clear":
        return 0.0, 0.0, 0.0, 0.0
    if weather == "rain":
        return 0.65, 1.0, 0.0, 0.0
    if weather == "snow":
        return 0.35, 0.0, 1.0, 1.0
    if weather == "storm":
        return 1.0, 1.0, 0.0, 0.0
    return 0.0, 0.0, 0.0, 0.0


def run():
    args = build_parser().parse_args()
    angles = parse_angles(args.angles) if args.angles else None

    W, H = init_display(args)

    # Lens overlay + textures for rain/snow.
    lens_drop_tex = app.upload_texture(
        app.make_lens_drop_texture(), internal=GL_RGBA, src=GL_RGBA)
    lens_flake_tex = app.upload_texture(
        app.make_lens_flake_texture(), internal=GL_RGBA, src=GL_RGBA)
    lens = app.LensWeatherOverlay()

    cache = {}
    draw_obj, dims = build_object(args.object, args.seed, cache)
    # Choose orbit radius so the object's bounding sphere fills ~60% of
    # the frame at the default zoom. dims = (width, height, depth).
    bound = max(dims) * 0.5
    base_radius = max(3.0, bound * 2.6)
    # Sky dome (shared with app.py) — nice backdrop that tracks time-of-day.
    sv, stc, sfr, sidx = app.build_sky_dome()
    cloud_tex = app.upload_texture(app.make_cloud_texture(),
                                    internal=GL_RGBA, src=GL_RGBA)
    overcast_tex = app.upload_texture(app.make_overcast_texture(),
                                        internal=GL_RGBA, src=GL_RGBA)
    stars_tex = app.upload_texture(app.make_stars_texture(), mipmaps=False)
    sky_state = (sv, stc, sfr, sidx, cloud_tex, stars_tex, overcast_tex)

    clock = pygame.time.Clock()
    t_day = time_hours_to_t_day(args.time)
    t_time = 0.0
    yaw = args.yaw
    pitch = args.pitch
    zoom = max(0.1, args.zoom)
    running = True
    elapsed = 0.0

    # Burst-screenshot state
    burst = list(angles) if angles else []
    burst_active = bool(burst)
    burst_delay = 0.20  # give fog/sprites a tick to settle per frame

    storm_i, rain_i, snow_i, frost_i = weather_to_params(args.weather)

    while running:
        dt = clock.tick(args.fps) / 1000.0
        t_time += dt
        elapsed += dt

        for e in pygame.event.get():
            if e.type == QUIT:
                running = False
            elif e.type == KEYDOWN:
                if e.key in (K_ESCAPE, K_q):
                    running = False
                elif e.key == K_s:
                    path = args.screenshot or (
                        f"view_{args.object}_s{args.seed}_"
                        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                    save_screenshot(path, W, H)
                elif e.key == K_SPACE:
                    yaw, pitch, zoom = args.yaw, args.pitch, args.zoom
                elif e.key in (K_PLUS, K_EQUALS, K_KP_PLUS):
                    zoom *= 1.10
                elif e.key in (K_MINUS, K_KP_MINUS):
                    zoom /= 1.10

        keys = pygame.key.get_pressed()
        rot_rate = 90.0
        if keys[K_LEFT]:
            yaw -= rot_rate * dt
        if keys[K_RIGHT]:
            yaw += rot_rate * dt
        if keys[K_UP]:
            pitch = min(85.0, pitch + rot_rate * dt)
        if keys[K_DOWN]:
            pitch = max(-15.0, pitch - rot_rate * dt)
        if args.auto_rotate:
            yaw += args.auto_rotate * dt
        # In burst mode, lock yaw to the next queued angle every frame.
        if burst_active and burst:
            yaw = burst[0]

        # Sky / ambient for this instant
        zenith, horizon = app.sky_colors_at(t_day, storm_i, 0.0)
        bright, tint = app.ambient_at(t_day, storm_i, 0.0)
        amb = (tint[0] * bright, tint[1] * bright, tint[2] * bright)
        night_a = app.night_factor_at(t_day)
        sun_d = app.sun_dir_at(t_day)

        glClearColor(horizon[0], horizon[1], horizon[2], 1.0)
        glFogfv(GL_FOG_COLOR, (horizon[0] * 0.9 + 0.05,
                                horizon[1] * 0.9 + 0.05,
                                horizon[2] * 0.9 + 0.05, 1.0))
        glFogf(GL_FOG_END, 380.0 / (1.0 + 0.3 * storm_i + 0.1 * frost_i))
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()

        # Orbit camera. Camera height and target both track the object's
        # vertical centre so tall buildings frame as nicely as a flower.
        radius = base_radius / max(0.1, zoom)
        yaw_r = math.radians(yaw)
        pitch_r = math.radians(pitch)
        target_y = dims[1] * 0.45
        cx = math.cos(pitch_r) * math.sin(yaw_r) * radius
        cz = math.cos(pitch_r) * math.cos(yaw_r) * radius
        cy = target_y + math.sin(pitch_r) * radius
        gluLookAt(cx, cy, cz,  0.0, target_y, 0.0,  0.0, 1.0, 0.0)

        # Sky dome centred on the camera (same convention as app.py).
        app.draw_sky(sky_state, cx, cy, cz, t_time, t_day, storm_i, 0.0)

        # Ground plane for context
        if args.ground:
            draw_ground(amb, frost_i)

        # The actual object, at origin
        ctx = {
            "amb": amb, "night_a": night_a, "frost": frost_i,
            "storm": storm_i, "wind": args.wind, "t_time": t_time,
            "sun_d": sun_d,
        }
        draw_obj(ctx)

        # Lens overlay for rain/snow — runs in 2D after the 3D pass.
        lens.update(dt, rain_i, snow_i, W, H)
        lens.draw(lens_drop_tex, lens_flake_tex, W, H)

        pygame.display.flip()

        # Automation: burst screenshots (one per frame, cycle through).
        if burst_active:
            # Let one additional frame flush before saving so overlays
            # catch up after the yaw snap.
            if elapsed > burst_delay:
                ang = burst.pop(0)
                path = screenshot_path_for(args.screenshot, ang) \
                    or f"view_{args.object}_yaw{int(ang)}.png"
                save_screenshot(path, W, H)
                elapsed = 0.0
                if not burst:
                    running = False

        if args.exit_after is not None and not burst_active:
            if elapsed >= args.exit_after:
                if args.screenshot and args.exit_after <= 0.0:
                    save_screenshot(args.screenshot, W, H)
                running = False

    # Single-shot on clean exit when exit_after > 0 + a screenshot path
    if args.screenshot and not angles and args.exit_after and args.exit_after > 0:
        save_screenshot(args.screenshot, W, H)

    pygame.quit()


if __name__ == "__main__":
    run()
