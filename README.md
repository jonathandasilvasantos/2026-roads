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
./env/bin/python setup_soundfonts.py    # one-time: fetch the piano SF2
./env/bin/python app.py
```

Press **Esc** to quit. Runs fullscreen at the native display resolution.

### Audio

- Continuous ambient **brown noise** (1/f² spectrum, FFT-synthesised) plays
  under the scene. Its playback speed tracks the camera speed so
  accelerating raises the rumble pitch; stopping drops it into a deep idle.
- A procedural **minimalist ensemble** (Rhodes + strings) plays the CC0
  *GeneralUser GS* SoundFont through FluidSynth. Requires the
  `fluidsynth` system library:
  - **macOS:** `brew install fluidsynth`
  - **Debian/Ubuntu:** `apt install fluidsynth`
  - **Windows:** `choco install fluidsynth` via Chocolatey. If you
    install fluidsynth by another method (MSYS2 / manual), pyfluidsynth
    still unconditionally calls `os.add_dll_directory(r'C:\tools\fluidsynth\bin')`
    at import time — the app pre-creates that directory (empty is fine)
    so the import succeeds, and the actual DLL is then looked up via the
    system loader. If fluidsynth isn't installed at all, the ensemble
    layer is silently skipped with a stderr warning and everything else
    runs.

  The SoundFont is 32 MB and downloaded on demand by
  `setup_soundfonts.py` — kept out of the repo via `.gitignore`.

## What's in the scene

- **Road** — procedural winding path with layered sine curves on X (wide
  sweeps + tight wiggles) and on Y (rolling hills + crests). Chase camera
  sits behind the "car" and pitches into climbs and descents by sampling the
  path ahead.
- **Biomes** — per-side, zone-based. Each ~240m zone on each side of the road
  independently rolls one of: plain, hill, mountain, river, forest, frost,
  city. 45m smoothstep transitions blend heights and colors between zones.
- **Frost zones** — real ambientCG Snow001 ground texture overlaid on the
  terrain mesh (second alpha-blended pass), drifts piled against the road
  edge, snow-covered trees (separate tree template set built with a snow-
  bark and snowy-leaf texture), and snow shoulders along the pavement.
  Mountain peaks above ~11m also pick up the snow overlay as altitude
  snowcaps regardless of biome. Point-sprite snowfall drifts down with
  horizontal swirl, gated by the frost weight at the camera so it only
  appears in frost biomes and fades at transitions. Fog density ramps up
  to +10% gradually as the camera enters frost zones.
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
- **Storms** — product of three detuned sines (~4 min primary) with a
  soft threshold so storms are rare events, roughly 8% of wall-clock
  time. Sky zenith/horizon darken toward neutral gray, cloud tint shifts
  to storm gray, ambient drops up to 55%, fog thickens +30% at full
  storm.
- **Rain** — 1,400 streak particles drawn as `GL_LINES`. Each drop's tail
  is `pos - velocity * streak_dt`, so streak direction and length match
  motion (motion-blur approximation without textures). Fall 17–25 m/s
  with wind X drift. Alpha tied to storm intensity.
- **Reflective puddles** — during rain, scattered water ponds appear on
  the ground along non-river/non-frost biomes. Procedural seamless
  ripple/caustic RGBA texture with radial alpha falloff for round
  puddles. Surface color is the current sky-horizon tint modulated by
  ambient — at noon they reflect bright sky, at night they look like wet
  asphalt. Texture matrix drifts UVs for animated ripples. Fades in with
  storm intensity and out shortly after the storm passes.
- **Lightning** — rare procedural bolts via 7-level recursive midpoint
  displacement (129 main-line vertices + 1–2 forks from the upper trunk
  only). Horizontal displacement is larger than vertical and decays
  geometrically per subdivision, so the bolt stays visibly vertical with
  zigzag layered on. Strikes are deliberately sparse: rolled once per
  second only when storm > 0.45, and a 5–12 s mandatory gap between
  consecutive strikes. Life 0.22 s with a 35 Hz sinusoidal flicker
  modulating both the bolt brightness and the scene flash — reads as
  multiple return strokes like a real strike.
- **City skyline** — rectangular-prism buildings tiled with a procedural
  facade texture (8-wide × 16-tall window grid on a concrete base). 12
  variants baked into display lists at startup with varied widths, depths,
  and heights (22–78m). UV repetition is baked per-variant so real-world
  window size stays consistent across building sizes. A second additive
  emission pass at night uses a matching emission texture where only ~45%
  of windows are lit with warm-jitter colors, producing a glowing urban
  skyline. Buildings are placed at 92–175m perpendicular distance so they
  read as a skyline beyond the terrain mesh and fog hides edges.

## Single-object viewer (`view.py`)

A companion CLI for isolating one procedural asset and iterating on it
without running the whole driving sim. Used extensively on this branch
as the inspection loop for the enhancements listed below.

```bash
python view.py --object house                       # interactive windowed stage
python view.py --object building --time 21 --weather clear --auto-rotate 20
python view.py --object mountain --seed 3 --time 7 --weather snow --windowed
python view.py --object asphalt --time 18.5 --weather rain \
               --yaw 0 --pitch 4 --zoom 2 --no-ground
python view.py --object car --seed 5 --angles 0,90,180,270 \
               --screenshot out/car.png --exit-after 0.0   # headless 4-angle burst
```

Objects: `house`, `building`, `mountain`, `flower`, `tree`, `car`,
`truck`, `asphalt`. Live keybindings: arrows orbit, `+`/`-` zoom,
`space` reset, `r`/`R`/`s` toggle rain/storm/snow, `p` screenshot,
`Esc`/`q` quit. Automation flags: `--auto-rotate DEG_PER_SEC`,
`--angles "0,90,180,270"` (screenshot burst), `--screenshot PATH`,
`--exit-after SECONDS`, `--time`, `--weather`, `--wind`, `--seed`.

## Enhancements (city-enhancements branch)

Each system below was iterated via `view.py` screenshot loops with
research as the design criterion. Per-cycle snapshots live under
`iter/<system>_{baseline,c2,c3,...}/`.

### Buildings
- 5 facade material palettes (concrete / limestone / brick / glass /
  sandstone), one per variant — Wonka 2003 / Müller 2006 split grammar
  layered bands baked as texture adornment.
- Parapet wall + rooftop mechanical box + antenna mast + blinking red
  aviation beacon (Schwarz & Müller 2015 tower-crown motifs).
- Night emission: two-pass GL_ONE/GL_ONE additive with `GL_LEQUAL`
  depth fix, ~45 % warm upper-floor windows + ~80 % cool fluorescent
  storefronts + halo bloom.
- Rooftop snow accumulation pass, wet-facade darkening under storm.

### Houses
- Ridge-height gable (previous apex peaked at `wall_h + 0.01`, leaving
  a visible gap under the roof).
- Plinth / foundation band, 0.45 m eave overhang, brick chimney with
  concrete cap and flue opening (Musialski 2013 survey cues).
- Window surrounds: painted trim frame + horizontal muntin + sill
  projection at layered protrusion depths (Lipp 2008 — frame depth is
  the #1 realism gain; no true recess since walls aren't hole-cut).
- Door surround: lintel + jambs + threshold step + bright handle.
- Per-variant window-lit mask (~60 %) so different houses glow
  differently at night; porch light always on.

### Sky + weather
- Added `make_overcast_texture()` dense storm cloud deck with
  mottled alpha (0.55–0.97 coverage). Rendered as an additional dome
  pass with smoothstep-ramped alpha when `storm > 0.04`. Fair-weather
  cumulus fades out as overcast fades in.
- CIE overcast model (Moon & Spencer 1942): under storm the zenith /
  horizon gradient flattens to ~1.2× ratio (instead of Preetham's 10×);
  horizon slightly brighter than zenith (dome-light effect); tint
  shifted to green-gray (Nishita 1996 multi-scatter loses Rayleigh
  blue).
- Cloud drift speed scales with storm intensity; a second overcast
  pass at `storm > 0.55` piles extra churn on heavy storms.

### Asphalt
- Two-pass BRDF decomposition per Pharr/Humphreys PBRT §8:
  1. Diffuse pass — textured, `GL_MODULATE`, 0.50× wet-albedo drop
     (Gu 2006 measured value), small blue lift.
  2. Specular pass — untextured, `GL_ONE,GL_ONE` additive, per-vertex
     `horizon_rgb × Schlick_Fresnel(depth_norm) × storm_i`. Survives
     the dark asphalt's MODULATE factor, producing the signature
     "mirror strip leading to the horizon" under rain.
- Dry-heat branch (Matusik 2003): noon high-brightness scenes blend
  0-25 % toward luminance-mean with small warm shift.

### Mountains
- Rebuilt: ridged multifractal with **per-octave rotation** (previous
  version had all octaves aligned in one axis → "fabric folds"
  artefact). Radial Gaussian falloff, peak height 34 % of base.
- **Per-vertex normals** from central-differenced height gradient
  (previous version shipped all normals as `(0, 1, 0)` so mountains
  showed zero sun-shading).
- Slope-based material (Musgrave 1993): cliff_frac picks between dark
  granite (steep) and warm talus (moderate).
- Snow mask combines elevation (above 0.72 × peak) **and** slope
  (ny > 0.62) — snow doesn't stick on cliffs.
- Draw-time weather response: wet-darkening, storm desaturation + cool
  shift, aerial perspective proportional to elevation × storm haze
  (Bruneton-Neyret 2008).
- Ambient + Lambertian diffuse with diffuse collapsing under storm
  (Nishita 1996 — overcast is diffuse-dominant / shadowless).

### Trees & flora
- Denser canopy: `_draw_leaf_cluster` now emits 6–9 jittered quads
  (up from 3–5) with 3D positional offset (Reeves-Blau 1985 particle
  density).
- Interior-branch leaf tufts on non-terminal branches past mid-depth
  — trees no longer read as bare-boned sticks mid-canopy.
- Looser alpha-test (0.25) + bigger `leaf_size`.
- Draw-time `_flora_weather_tint`: wet saturation boost (Nayar 1991),
  drought desaturation + yellow shift (Gitelson 2002), backlit amber
  translucency at low sun elevation (Premoze-Ashikhmin 2002 BTDF).
- Three-frequency wind (CryEngine 3 / Stam 2007): slow trunk lean +
  medium branch flex + existing per-leaf randomness.
- Wet-leaf silvery sheen via additive second pass under rain.

### Vehicles
- Procedural variety pool: 96 cars (was 18) + 40 trucks (was 10) with
  a 42-colour palette organised by hue family and weighted toward real
  automotive popularity (PPG annual colour reports).
- Ground FX (`_draw_vehicle_ground_fx`) — no more floating cars:
  - Contact shadow elliptical patch (Heckbert-Herf 1997), softened
    under overcast (diffuse kills hard shadows).
  - Headlight ground pool — twin warm 3200 K halogen ellipses per
    lamp with inner hotspot + outer spill, additively blended
    (GL_ONE, GL_ONE). Beam length compressed by `(1 - 0.45 × storm)`
    per Narasimhan-Nayar 2003 atmospheric extinction.
  - Taillight rear glow (red ellipses, 0.25 × night_a intensity).
  - Wet-road body reflection under the vehicle when `storm > 0.15`.

### São Paulo traffic model
- `TRAFFIC_DENSITY_SP` — 24-hour density table [0..1] calibrated from
  CET-SP bulletins, Metrô 2017 O-D survey, Waze for Cities aggregates.
  Double peak **08-09h (~0.95)** and **18-19h (~1.00)**, noon trough
  ~0.60, madrugada ~0.05-0.10.
- Each pool vehicle holds a persistent `vis ∈ [0, 1]` threshold;
  `draw_cars` skips when `vis > density` — at 3 AM only a handful of
  cars render, at 18h every slot fills (Marginais rush-hour density).
- Trucks use a softer curve `0.3 + 0.7 × density` reflecting CET
  cargo-flow studies (freight partially off-peak to avoid rodízio).

### Engine / stage plumbing
- Screenshot capture reads `GL_FRONT` after `glFinish()` so the saved
  frame matches the on-screen view (previously captured one frame
  behind).
- `draw_sky`, `draw_road`, `draw_houses`, `draw_city` signatures
  extended to take `horizon_rgb`, `storm_i`, `frost_i`, `night_a`,
  `snow_tex`, `t_time` — weather is now a proper render-graph input.

## Textures

- **Bark** — [ambientCG Bark001](https://ambientcg.com/view?id=Bark001), CC0.
- **Snow ground** — [ambientCG Snow001](https://ambientcg.com/view?id=Snow001), CC0.
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

| Key | Action |
| --- | --- |
| **Up** | accelerate |
| **Down** | decelerate / brake |
| **Left** | rotate camera left (up to −90°) |
| **Right** | rotate camera right (up to +90°) |
| **Space** | re-center the camera to the forward view |
| **T** | trigger a lightning strike (and thunder clap) |
| **Esc** | quit |

## Status

Research/demo project — single-file, fixed-function OpenGL, no shaders.
