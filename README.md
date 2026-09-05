# 2026-roads

## New Wave — free driving on Metal and OpenGL

New Wave is the canonical game: a playable car and an endless world in every
horizontal direction. On macOS, the default renderer uses **Metal on the GPU**.
The same game also runs through **OpenGL 4.1** with the same physics, world,
assets and adaptive procedural score.

Roadside hamlets now include walking people, fences, lamps and more detailed
houses. Traffic is present from startup; open leaf crowns, dense verge grass
and a generated meadow texture enrich the landscape. See the
[visual development notes](development/REALISM.md) for concepts, assets,
performance measurements and the remaining gap to photorealism.

```bash
./env/bin/python -m pip install -r requirements.txt
./run.sh                          # fullscreen, Metal on macOS
./run.sh --backend opengl          # same game through OpenGL
./run.sh --windowed                # development window
./run.sh --quality Quality         # more terrain and foliage, same resolution
./run.sh --no-music                # ambience only
```

WASD or arrows drive; S/Down brakes then reverses. Space: handbrake. Left Ctrl:
brake. R: recovery. C: camera. H: controls. T: change time. P: pause. Esc exits.
F12 saves a screenshot. Standard GLFW gamepads use the left stick,
right/left triggers, A for handbrake and Y for recovery.

Configuration is in [new_wave/config.json](new_wave/config.json); see
[New Wave guide](development/NEW_WAVE.md) for physics, world architecture,
validation, performance evidence, references and limitations. The legacy
implementation below remains internal reference material; it is no longer a
separate public game mode.

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

New Wave restores the existing procedural Rhodes-and-strings music and adapts
its brightness, ambience and volume smoothly to speed, daylight, rain and biome.
Use `--no-music` to keep environmental ambience only, or `--no-audio` for silence.

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

### Traffic behaviour (light branch — plan.txt Phase 1)
- **IDM car-following** (Treiber-Hennecke-Helbing 2000): every vehicle
  accelerates/brakes toward a personal desired speed while holding a
  speed-dependent safe gap to its leader. Cars and trucks share one
  physics pass (`update_traffic`) so leader search spans both pools —
  platooning and slowdown waves emerge at rush-hour density.
- **Driver personalities**: 20% aggressive / 60% normal / 20% calm,
  rolled per spawn with jitter — scales desired speed, time headway,
  acceleration, braking comfort and lane-change eagerness.
- **Two sub-lanes per direction + MOBIL lane changes**: an outer
  cruising lane and inner overtaking lane; changes need an incentive
  (gain vs. eagerness threshold) and a safety check (new follower never
  forced past 2.5 m/s²), animated over ~2.8 s with a steering tilt.
  Keep-right discipline returns vehicles to the outer lane when free.
- **Mixed-pace same-direction traffic**: desired speeds straddle the
  player's cruise speed — faster drivers spawn behind and overtake,
  slower ones spawn ahead and get reeled in (no more "every car outruns
  the camera" treadmill).
- **Condition coupling**: desired speed scales with the metropolitan
  congestion curve and drops in rain (−22%), frost (−32%) and at night
  (−7%); headways stretch +40% on a wet road.
- **Brake lights**: IDM deceleration past 0.5 m/s² lights and swells the
  rear lenses — daylight too, so slowing platoons cascade red.
- **Headlight discipline**: per-driver dusk threshold (lights pop on
  progressively across the fleet, not all at once) plus a storm
  threshold for lights-on in heavy rain at midday.

### Road-user & roadside diversity (light branch — plan.txt Phase 2)
- **Motorcycles** (24 variants: bike + leaning rider, single head/tail
  lamp): roll into curves by `atan(v²·κ/g)`, and lane-split ("corredor")
  down the divider when the flow crawls below 15 m/s — falling back
  into a lane only when a front+rear clear slot exists. Their density
  over-indexes at the peaks (motofrete hours).
- **Delivery vans** (24 variants, white-fleet palette) on a commerce-
  hours density curve (10-16h plateau, near-zero madrugada).
- **City buses** (12 variants: transit-style white shell + coloured
  waist stripe, window band, roof AC, lit route board). Buses serve
  hashed bus-stop slots inside city zones: pull to the curb, dwell 5-10s
  with a gentle ~0.9 m/s² approach, then merge back out — followers
  brake or overtake around them. Warm window-strip glow at night.
- **Emergency unit** (ambulance/police, roof lightbar): dormant most of
  the time, then launches behind the camera every 1.5-4 minutes and
  runs the inner lane hot with alternating red/blue strobes while
  same-direction traffic ahead pulls to the outer lane and slows.
- **Truck cargo & condition**: new `tanker` style (chrome fuel /
  painted chemical barrel + hatches), ~60% of semis haul coloured
  corrugated shipping containers, ~65% of dump trucks run loaded with
  an earth mound, and every truck carries per-variant grime (cars get a
  light wash-state version).
- **Pedestrians**: procedural billboard people (10 variants × skin/
  shirt/pants/hair palettes) on hashed sidewalk slots in city zones,
  driven by a street-life hourly curve that peaks 18-22h. The whole
  crowd raises umbrellas when storm intensity passes 0.3. Idle sway +
  occasional pairs/trios; night shop-light lift so they never go black.
- **Wildlife**: bird flocks circle over plain/forest/river biomes with
  three-axis drift and wing-flap line rendering — lightning startles
  every flock into a fast burst climb; they roost at night. Deer (at
  forest edges) and capybara (at the water line, terrain-anchored)
  appear only in crepuscular windows around dawn and dusk. Moths circle
  the street-lamp heads at night.
- **Traffic-physics hardening** (found by the expanded smoke test):
  spawn clearance and forced merges (yield, bus pull-out, corredor
  exit) are now closing-speed aware (braking distance, not flat
  margins); a mid-change vehicle respects the leaders of *both*
  sub-lanes it straddles; lane-change targets count as occupied from
  decision time; cut-ins keep an absolute 4 m bumper floor.

### Weather & time realism (light branch — plan.txt Phase 3)
- **Weather lifecycle machine**: live weather is now a state machine —
  CLEAR → CLOUDING → OVERCAST → RAIN → CLEARING — with humidity carried
  between events (a rain dumps it, clear air recharges it) and the
  trigger probability peaking on summer afternoons (tropical
  convective pattern). Storms are events you watch build; precipitation only
  starts once the level passes the rain onset, so the overcast band
  darkens the sky without raining. ~20% of fronts pass dry.
- **Wet-road memory**: rain soaks the pavement in seconds; drying takes
  2-4 simulated hours (faster under the midday sun). Road darkening,
  puddles, bridge concrete and traffic caution all read the wetness
  memory — "sun's out but the road is still wet and the flow is still
  slow" is now a real state. `--wetness` pins it for screenshots.
- **Radiation dawn fog**: clear mornings over river/plain zones pull
  the fog bank in close around sunrise, burning off by mid-morning.
- **Distance-anchored seasons**: one full year every 24 km of road —
  the zone you reach was rolled in the season you reach it, so biomes
  never pop under the camera. Winter lowers/flattens the sun arc
  (shorter days), multiplies frost zones (rare in summer, common in
  winter, plains upgrade to frost in deep winter), drops the mountain
  snow line from ~17 m to ~6 m, desaturates flora toward dry-season
  straw and thins the flowers. `--season summer|autumn|winter|spring`
  (or a 0-1 phase) offsets the cycle.
- **Astronomy**: the moon waxes/wanes over 4 day-cycles with a real
  crescent (occluder-disc mask) and scales the ambient night floor —
  full-moon nights are ~50% brighter than new-moon nights. The star
  field wheels slowly over the night; rare shooting stars streak
  across clear night skies. Thunder is distance-correct: the clap
  arrives bolt-distance ÷ 343 m/s after the flash, quieter when far.
- **Unified wind**: a single wind state (wandering heading, calm base +
  triple-LFO gusts + storm forcing) now drives rain streak slant, snow
  drift, tree/flower sway, cloud scroll speed (integrated, so clouds
  accelerate with a front instead of jumping), the ambient wind audio
  layer, and lateral buffeting on trucks/buses/vans/motorcycles.

### World & roadside diversity (light branch — plan.txt Phase 4)
- **Road surface zones**: the pavement varies on its own 560 m grid —
  fresh asphalt, sun-bleached old asphalt (lighter per-vertex tint plus
  pothole and tar-patch decals) and concrete sections (much lighter,
  dark transverse expansion joints every 12 m) with smoothstep
  transitions. (Lane-count variation was deferred: it would re-architect
  the sub-lane traffic geometry for mostly-visual payoff.)
- **Roadside furniture** (hash-slot deterministic, like the trees):
  steel guardrails exactly where the terrain drops away (mountain
  ledges, river banks); traffic signs that *mean something* — curve
  warnings placed by sampling the actual path curvature 80-120 m ahead
  (left/right arrow matches the bend), sparse speed limits, city-name
  boards at city-zone entries — all retroreflective, flaring up at
  night as the camera closes; km marker posts; wooden power poles with
  sagging catenary wires along rural stretches; floodlit billboards
  with procedural ads; bus shelters at the phase-2 bus-stop slots; and
  rare concrete overpasses spanning the road — the big highway
  silhouette.
- **Four new biomes** (zone system extended 7 → 11, with a properly
  avalanche-mixed zone hash — the old multiplicative key was nearly
  sequential mod small divisors, which locked biome ordering): farmland
  (crop-row ripple, barns, hay bales), wetland marsh (reed beds, joins
  the dawn-fog system), cerrado (red laterite soil, scrub, termite
  mounds) and industrial outskirts (warehouses, smokestacks, tank
  farms; clusters near city). Wind turbines spin on open hill/mountain
  ridges.
- **Living structures**: house windows follow a daily schedule (lit
  through the evening, porch-only in the small hours, an early-riser
  glow before dawn — jittered per house); chimneys smoke on cold
  mornings and in frost zones, drifting with the unified wind; rare
  high-altitude aircraft cross the sky with a fading contrail and a
  blinking anti-collision strobe at night.
- Perf: zone-biome rolls are now memoised (`_BIOME_CACHE`) — the
  terrain/flora/scenery stack issues >1M biome lookups per frame.

### Traffic audio & event soundscape (light branch — plan.txt Phase 5)
- **Stereo mixer upgrade**: the ambient mixer now outputs two channels
  with constant-power panned one-shots and registrable ambience loops
  (with a varispeed mechanism; thunder rides the same event path as
  before).
- **Vehicle pass-bys**: every rendered automobile that crosses the
  camera plane fires a class-specific whoosh — Doppler baked into the
  clip (bright→dark spectral glide + ~18% pitch drop on the tonal
  layer), trucks/buses with a soft sub-harmonic rumble. Volume scales
  with true closing speed, pans to the vehicle's side, and sizzles
  ~70% louder on a wet road (the phase-3 wetness memory).
- **Biome ambience**: dawn birdsong over forest/plain zones, silent in
  storms, crossfaded by the live biome weights at the camera; the wind
  layer amplifies into a whistle through mountain zones.
- **Deliberately serene mix** (user preference): mechanical sounds sit
  at 4% of natural level (`ARTIFICIAL_SOUND_GAIN`), and the noisier
  phase-5 prototypes — horns, sirens, engine brakes, the cricket/frog
  night chorus, the city hum and the motorcycle buzz — were removed
  outright. The soundscape is automobile whooshes + nature (birds,
  wind, rain, thunder) + the original engine rumble and ensemble.

### Incidents & emergent events (light branch — plan.txt Phase 6)
- **Breakdowns**: on a slow clock, a same-direction car or van pulls
  onto the shoulder, eases fully off the carriageway (it stops
  counting as a lane occupant only once it's genuinely clear), sits
  with alternating amber hazard flashers for half a minute or more,
  then recycles. Passing traffic merges away from it while it still
  straddles the lane — pure MOBIL. `--event breakdown` forces one.
- **Roadworks zones** (deterministic hash grid, ~45% of 3.1 km cells):
  an orange cone taper closes one sub-lane, a blinking-chevron arrow
  board stands at the entrance, the whole zone crawls at ~55% speed,
  and lane changes INTO the closure are vetoed. The bottleneck and the
  upstream brake-light wave emerge from the IDM physics — verified:
  in-zone average 7.6 m/s vs 13.5 m/s free flow, no overlaps.
- **Speed traps**: a patrol car parked on the shoulder (the phase-2
  police sedan, lights off — it's a trap); same-direction drivers lift
  off ~20% through the radar window and resume after, rippling a
  brake-light flicker through the flow.
- **Toll plazas** (rare, flat-rural zones only): a free-flow gantry
  spanning the road with booth islands on both shoulders; every
  vehicle funnels to a crawl through the booth line and pulls away
  after. Emergency code-3 runs are exempt.
- All incidents are silent, per the serene-mix preference.

### Road corners & cornering dynamics (light branch — plan v2 phase 1)
- **Real bends**: deterministic corner zones layer smoothly-windowed
  sin³ lateral bumps directly inside `curve_x` (the single source of
  truth), so the road mesh, terrain, traffic, furniture and camera all
  inherit them. ~31% of zones wind through chained S-bends, ~12% carry
  a single sharp bend (curvature budgeted against the local base path
  and capped at the terrain-skirt fold limit, R ≥ ~95 m), and ~16%
  damp the global wiggle into genuine straightaways.
- **Superelevation**: the cross-section rolls into bends (up to 8°,
  from design-speed × curvature). The banked edge carries through the
  terrain skirt, lane snow, decals, guardrails, lamps, signs and cones
  via a shared `road_y_at(s, lateral)`; vehicles sit ON the banked
  surface and roll with it; the camera's up-vector leans into sweeps,
  damped and time-smoothed.
- **Traffic corners like drivers**: each driver rolls a lateral-g
  budget (aggressive ~3.2 m/s², calm ~2.0; trucks/buses ~60%,
  motorcycles ~145%), reduced on wet or frosty roads. A curvature
  look-ahead caps desired speed at `v = sqrt(a_lat/κ)` feeding the
  IDM — braking into corners (brake lights on entry) and accelerating
  out of the apex emerge naturally. Verified: 17.2 m/s through a
  κ=0.0076 hairpin vs 26.3 m/s on the straights, no overlaps.
  Emergency runs respect physics through bends too.
- **Corner furniture**: yellow chevron boards stand on the OUTSIDE of
  sharp bends pointing through them (retroreflective at night);
  guardrails extend along corner outsides wherever centripetal math
  says a design-speed vehicle would leave the road; skid-mark decals
  drift toward the outside through the hardest apexes.

### Bridges & viaducts (light branch — plan v2 phase 3)
- **River bridges**: the old parapet strips over river zones are now a
  true structure — deck fascia with an upstand, underside slab, twin
  edge girders, piers every 18 m down to the water with splash
  collars, and a see-through railing (posts + double rail) replacing
  the guardrail across the span. Baked per span into world-space VBOs
  split by shade group, so daylight modulates with three glColor
  calls.
- **Ravine viaducts**: the base terrain never drops far below the
  road, so valleys are created — deterministic transverse ravines
  (9-15 m deep, 75-135 m long) carved through hill/mountain zones,
  applied after the road-edge blend so the ground genuinely falls away
  under the deck (verified: −12 to −15 m at centre span). The viaduct
  then spans the gap it created, piers reaching the ravine floor —
  the same "structure earns itself" logic as the tunnels.
- **Landmark crossings** (~1 per 15 km): the longest river spans roll
  cable-stay twin towers at the deck edges (H-frame + crossbeam, the
  carriageway stays clear) with live-drawn cable fans to both edges.
- **Integration**: bridge spans never overlap a tunnel bore (the rock
  wins); guardrails, snow shoulders and ponds are suppressed on decks;
  tall vehicles catch ~50% more crosswind buffeting mid-span; small
  birds perch along the railings at dawn and dusk.

### Tunnels (light branch — plan v2 phase 2)
- **Placement that earns itself**: one candidate per ~2.6 km hash
  cell, and the cell is *scanned* for a genuine mountain run (high
  mountain weight across the whole 150-290 m span) — tunnels only
  exist where the terrain justifies boring through. Deterministic,
  cached, ~1 per 15-20 km.
- **Structure**: half-cylinder bore shell with per-vertex sodium light
  pooling baked into a world-space VBO (drawn behind one
  camera-relative translate, like the far-forest chunks), portal arch
  faces, and an exterior rock ridge that carries the mountain over the
  road. Ceiling lamps every 12 m with feathered warm pools on the
  pavement; a daylight glow disc marks the exit portal from inside.
- **Exposure feel**: ambient blends to a dim sodium-warm interior over
  15 m portal skirts — at night the bore is BRIGHTER than outside, the
  classic reversal. Fog shortens to enclosed-air range; sky, sun, moon,
  shooting stars and aircraft are culled when fully inside.
- **Weather shielding**: rain, snow, lens drops and the wetness GAIN
  gate off through the portals (wet pavement near the mouths persists
  via the phase-3 wetness memory and dries on its own clock).
- **Behaviour**: every vehicle forces its headlights on inside; lane
  changes are vetoed in the bore (drivers hold their lane); signs,
  chevrons, km markers, guardrails, trees, turbines and incident zones
  (roadworks/tolls/traps) are all suppressed inside via one shared
  `in_tunnel(s)` predicate.
- **Audio**: the outside world (birds/wind/rain) ducks to ~20% through
  the portals and the car's own rumble lifts a touch — no new
  mechanical sounds, per the serene-mix preference.

### Metropolitan traffic model
- `TRAFFIC_DENSITY_SP` — 24-hour density table [0..1] calibrated
  against public big-city traffic bulletins, origin-destination
  surveys and aggregate flow data.
  Double peak **08-09h (~0.95)** and **18-19h (~1.00)**, noon trough
  ~0.60, madrugada ~0.05-0.10.
- Each pool vehicle holds a persistent `vis ∈ [0, 1]` threshold;
  `draw_cars` skips when `vis > density` — at 3 AM only a handful of
  cars render, at 18h every slot fills (rush-hour density).
- Trucks use a softer curve `0.3 + 0.7 × density` reflecting urban
  cargo-flow patterns (freight partially off-peak).

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

## Performance notes

The GL context is hardware-accelerated (verify with the renderer
string — e.g. "Apple M4 / 2.1 Metal"); the frame budget is dominated
by CPU-side work, attacked without touching visual quality:

- **Trees as static VBOs** — display-list replay of immediate-mode
  trees is CPU-bound in the GL-on-Metal driver (~0.3 ms per tree). The
  recursive generator's exact rng walk is re-captured into triangle
  arrays: near trees (<180 m) draw from per-variant VBOs with live
  wind sway; far trees bake into world-space chunk VBOs (sway eased to
  zero at the hand-off, where it is sub-pixel anyway). Forest pass:
  62.8 → 3.3 ms/frame, identical geometry.
- **Cinematic grade via 256³ LUT** — the colour chain is a pure
  function of the 8-bit input pixel, so a full-resolution lookup table
  reproduces it bit-exactly (verified max diff 0) at one gather per
  pixel; the vignette applies in 16-bit fixed point (±1 LSB at the
  darkened edges); writeback uses a streaming texture + quad instead
  of the slow glDrawPixels path. The LUT builds in ~1 s and caches to
  disk (`.grade_lut_*.npy`, gitignored).
- **PyOpenGL per-call error checking disabled** (millions of redundant
  glGetError round-trips per second; the test suites and GL harnesses
  gate correctness instead).
- **Memoised world queries** — zone biomes, single-sample biome
  weights at fixed hash-slot positions, per-slot tree placement
  decisions, road curvature bins.

Net effect at 720p in a forest zone: ~8 → ~33 fps with the full
cinematic grade, ~20 → ~100 fps without it.

## Status

Research/demo project — single-file, fixed-function OpenGL, no shaders.
