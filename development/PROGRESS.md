# New wave development evidence

Initial state: clean `light` branch, commit recorded by Git ancestry. No AGENTS.md
found in repository or parent directories. Python 3.12, pygame 2.6.1, legacy
fixed-function PyOpenGL renderer; 13,293-line app.py plus asset viewer and eight
GL-free smoke suites. Existing screenshots and textures retained.

Hardware: Mac mini Mac16,10, Apple M4 10 CPU / 10 GPU cores, 16 GB unified memory,
macOS 26.5.1 (25F80), attached 3840x2160 60 Hz display.

## Acceptance matrix

| Area | Acceptance / evidence required |
|---|---|
| Build / compatibility | Legacy tests pass; Metal and OpenGL free-drive launch without errors |
| Vehicle / controls | Acceleration, brake, reverse, steering, grip, recovery; deterministic fixed-step tests across frame schedules |
| Camera | Smooth chase, collision-safe height, alternate inspection viewpoints |
| Terrain / roads | Shared-coordinate height / normal seams, continuous road intersections, positive and negative coordinates |
| Streaming / memory | Bounded chunks and jobs, removal of CPU/GPU resources, near-first worker generation, long travel test |
| Assets / collision | Editable Blender coupe, correct scale/normals, bounded polygon count, nearby obstacle response |
| Materials / lighting | Screenshot comparisons, coherent palette, directional light, shadows, sky and fog; independent visual evaluation |
| UI / accessibility | Readable controls, speed, heading, seed/quality, pause, configurable bindings, reduced motion |
| Performance | Default Balanced explicit 1280x720 render, 60 FPS target; average/p99/peak, CPU and GPU evidence where available |

## Baseline / architecture

`reference/before-driving.png`: actual 1280x720 legacy capture at 16h, s=0.
Shows gray geometry obscuring sky. Source confirms CPU framebuffer readback and
50 MB color LUT on every frame. Legacy road coordinates constrain travel to a
single corridor and arrows rotate the camera rather than steering a vehicle.

Decision: preserve original app/viewer and all content as Classic; add shared
free-drive simulation with Metal and OpenGL renderers. GPU-resident geometry,
GPU color grading, deterministic 2D chunks, fixed 120 Hz vehicle physics. This
avoids rewriting legacy weather/traffic functionality or breaking its tests.

Metal capability verified through wgpu adapter: Apple M4 / IntegratedGPU / Metal;
hardware timestamp queries supported. Blender 5.2.1 installed from official
download.blender.org via Homebrew; no configured Blender MCP tool or listener
on port 9876 was available. Background Blender Python is the asset workflow.

`reference/concept-road.png`: built-in image generation from baseline screenshot.
Direction: muted ochre grass / olive trees, warm directional light, blue distant
haze, fine asphalt and restrained road paint. Concept is a target, not gameplay.

## Cycle 1 — stable vertical slice

Vehicle: ten tests verify acceleration/braking/reverse, progressive steering,
handbrake slip, 30/60/144 Hz rendering consistency, swept contacts, recovery and
stall bounds. Measured 0.016 ms per isolated physics step with 32 colliders.
World: deterministic 2D chunks, connected roads, negative coordinates, bounded
workers and GPU lifecycle. First 81 chunks: 11.3 MiB terrain, about 110k triangles.
Blender coupe: 4,288 assembled triangles; editable source, GLB and NPZ validated.
Both renderers built and launched on the M4; actual images saved.

Independent evaluator examined only completed source/tests/images/JSON, not
implementer reasoning. Five defects: analytic vs mesh contact differed up to
.85m; GL lacked roll and had shadow artifacts/cloud differences; repeated
conifers/lawn; stationary evidence and 57 FPS limiter; recovery could stay wedged.
First Metal GPU timestamp was invalid (unsigned subtraction of zero end marker).
Those first reports are retained locally as debug evidence but excluded from Git.

## Cycle 2 — contact, parity, visual composition, pacing

Implemented triangulated `surface_height` and random mesh-contact tests; same
pitch/roll in both renderers; shared sky/material equations and receiver bias;
clear-position recovery with off-road fallback tests; bounded ambient traffic;
monotonic 60 FPS pacing. Metal end-marker failure reproduced independently and
replaced by a next-pass beginning marker, with invalid-pair rejection.

Added clustered biome-specific trees, rolling ridges, ochre/olive palettes,
smaller verge grasses, reflectors and water towers. Warm chunk generation ~1.13
ms, p99 1.57 ms isolated; terrain memory unchanged. Car concept generated from
actual close view. Five remaining concept differences: fine grass density,
paint/glass reflections, asphalt microdetail, cloud shape, layered distant hills.
Prioritized glass Fresnel, finer cloud octaves, road grain and actual rolling
terrain; dense photoreal vegetation remains outside this vertical slice.

Camera now carries car translation while smoothing orientation so high-speed
chase distance remains readable. Night headlamps, emissive lamps, wet road and
GPU rain verified through actual Metal/OpenGL captures. Input/HUD supports pause,
keyboard rebinding, controller mapping, camera modes and reduced motion.

30s Metal driving: 59.994 FPS, p99 16.670 ms, peak 17.756 ms; four pending chunks,
RSS plateau 238.4 MB. The report predates separating startup dropped time and GPU
scope, so final measurements supersede it. New telemetry is bounded in memory.

## Cycle 3 — independent review and sustained verification

Second independent review: no new blocker to free driving; terrain contact,
GL roll, recovery, environment and night/rain were materially improved. All 26
CPU tests passed. Remaining limitations identified: conservative circular
collision proxies, recovery candidates omit traffic (contact resolves afterward),
ambient traffic may wait indefinitely at a mutually blocked intersection.
These do not obstruct player exploration; full rigid-body/traffic redesign was
not justified for this slice. Original Classic features are preserved intact.

90s Metal Balanced 1280×720, radius4, eight-second warmup: 59.995 FPS average,
59.990 p99-equivalent low, 17.817 ms peak, GPU scene including shadows mean .625 ms,
peak1.087 ms, process CPU23.1% of one core, max RSS239.9 MB. 81 chunks/four jobs,
zero dropped simulation time. Full CPU validation ran concurrently during part
of this run; performance remained stable. Detailed route and settings in JSON.

Validation runner: all eight original smoke scripts, new unittest suite,
compilation and vehicle geometry passed (11/11 groups, 35.31s). Fresh Blender
GLB import confirmed136 meshes,4288triangles, materials/UVs and correct scale.
