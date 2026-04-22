# 2026-roads

An endless driving demo in the spirit of the Atari 2600 *Enduro* camera, built
with Python + PyOpenGL. A procedural road winds through procedural terrain
(plains, hills, mountains, rivers, forests) under a full day/night cycle with
a dynamic sky dome, sun, moon, stars, clouds, street lamps, and procedurally
generated trees.

Everything is generated at runtime — no scene files, no pre-baked meshes.

## Running

```bash
python3.12 -m venv env
./env/bin/python -m pip install -r requirements.txt
./env/bin/python app.py
```

Press **Esc** to quit. Runs fullscreen at the native display resolution.

## What's in the scene

- **Road** — procedural winding path with layered sine curves on X (wide
  sweeps + tight wiggles) and on Y (rolling hills + crests). Chase camera
  sits behind the "car" and pitches into climbs and descents by sampling the
  path ahead.
- **Biomes** — per-side, zone-based. Each ~240m zone on each side of the road
  independently rolls one of: plain, hill, mountain, river, forest. 45m
  smoothstep transitions blend heights and colors between zones.
- **Terrain** — 14-band triangle strip per side (~80m outward) with per-biome
  height profiles: plain flat, hill gentle waves, mountain steep ridged
  rise, river dipped valley with animated water, forest near-flat floor.
  Single tileable fBm noise texture tinted per-vertex by biome color.
- **Day/night cycle** — full cycle in 120s (one minute midnight → noon).
  Sun arcs across a slightly-tilted path; moon sits opposite. Ambient
  brightness/tint drives terrain, road, fog, and cloud colors; street lamps
  gate off during the day.
- **Sky dome** — 24×48 hemisphere rendered in three passes: per-vertex
  gradient (9-keyframe table for midnight/dawn/noon/dusk/etc.), an additive
  starfield at night, and fBm clouds with self-shadow and horizon/zenith
  altitude mask. Clouds and stars drift via the texture matrix.
- **Sun & moon** — billboard discs with layered halo glows, warm orange at
  sunrise/sunset, white at noon, silvery cool at night.
- **Trees** — recursive fractal branching generator (tapered `gluCylinder`
  trunk + random yaw/pitch children + terminal leaf clusters as crossed
  alpha-cutout quads). Six variants baked once into display lists at
  startup, instanced across forest zones via a deterministic per-slot hash
  for placement, yaw, scale, and variant.

## Textures

- **Bark** — [ambientCG Bark001](https://ambientcg.com/view?id=Bark001), CC0.
- **Leaves** — procedural (RGBA foliage cluster with soft alpha + faint vein).
- **Road asphalt** — procedural noise + dashed center line + side stripes.
- **Terrain ground** — procedural multi-octave fBm, tinted at runtime.
- **Sky clouds, stars** — procedural, horizontally-seamless fBm.

## Code layout

Single file: `app.py`. Rough sections:

| Section | Purpose |
| --- | --- |
| path curves | `curve_x`, `curve_y` — road centerline as layered sines |
| biome zoning | `biome_at`, `biome_weights_vec`, `is_plain`, `forest_weight_at` |
| terrain heights | `terrain_heights` — per-biome vectorised height profile |
| day/night model | `sun_dir_at`, `sky_colors_at`, `ambient_at`, `cloud_tint_at`, `night_factor_at` |
| textures | `make_road_texture`, `make_terrain_texture`, `make_cloud_texture`, `make_stars_texture`, `make_leaf_texture`, `load_texture_file` |
| sky dome | `build_sky_dome`, `compute_dome_colors`, `draw_sky`, `draw_celestial`, `sun_color` |
| terrain | `build_side_arrays`, `draw_terrain` |
| road & lamps | `draw_road`, `draw_lamps` |
| trees | `_emit_branch`, `build_tree_variant`, `build_tree_variants`, `draw_forest` |
| main loop | `main` — camera, timing, draw order |

## Tree generator — algorithm notes

Recursive fractal branching, chosen over L-systems and space colonization
because it bakes cleanly into display lists and instances cheaply. Each
template is a single display list compiled at startup:

1. Emit a tapered `gluCylinder` along +Y at the current transform.
2. If terminal (depth limit or length < 0.35m), emit 3–5 crossed
   textured quads with alpha test for the leaf cluster.
3. Otherwise, translate to the tip, choose 2–3 children with random yaw and
   pitch, scale length 0.62–0.80 and radius ~0.72, recurse.

Texture binds and `GL_ALPHA_TEST` state changes are recorded inside the
display list, so instancing is just `glPushMatrix + transforms +
glCallList + glPopMatrix`.

Placement is deterministic: for each 3.2m slot on each side of the road,
a per-slot hash decides density gate, perpendicular distance (1.2–47m),
variant (0–5), yaw, and scale. The same slot always produces the same
tree, so the forest is stable across frames without any storage.

## Controls

- **Esc** — quit.

## Status

Research/demo project — single-file, fixed-function OpenGL, no shaders.
