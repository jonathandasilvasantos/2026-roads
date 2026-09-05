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

Initial decision: preserve the original app/viewer while building the slice; add shared
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

## Final evidence

OpenGL equivalent 90-second drive: 59.944 FPS average, p99 16.683 ms,
peak 29.798 ms, GPU scene mean 1.177 ms, process CPU 21.5% of one core,
RSS 236.2 MiB. No dropped physics time.

Metal 180-second night/rain drive: 59.994 FPS average, p99 16.671 ms,
peak 17.339 ms, GPU scene mean .645 ms, process CPU 20.6% of one core,
RSS 248.9 MiB. It held 81 chunks, four outstanding jobs and 92 GPU meshes.
RSS reached its plateau early and stayed flat. Startup shader preparation caused
.534 s of bounded dropped simulation before measurement; measured dropped time
was zero. Metal/Quality at explicit 1920x1080 sustained 59.903 FPS over 20
seconds; GPU scene mean 1.626 ms, p99 2.445 ms. One 48.171 ms frame occurred.

GPU results use the `gpu_scene` field and include shadows; Metal reads samples
every 120 frames, while OpenGL uses asynchronous elapsed queries. The scope
does not include identical presentation work on both APIs, so cross-API GPU
figures are indicative. Dedicated GPU utilization and memory were unavailable.

The final independent evaluator inspected seven representative screenshots and
all sustained reports: no obvious terrain/road seam, intersection break or
long-coordinate failure. The test captures include 128-million-metre coordinates.
The scenery remains stylized and below the generated concepts in fine detail.
Static screenshots cannot substantiate subjective driving feel.

The 26.7-second keyboard replay exercised the actual mapped controls:
accelerated from 0 to 26.97 m/s, steered and handbraked, reversed to -9.83 m/s,
paused with exact position/speed preservation for 2.2 seconds, recovered onto
clear ground with speed zero, then drove again. Frame p99 was 16.669 ms, peak
19.679 ms. This is a synthetic input replay; GLFW found no connected gamepad.

The last refinement locks procedural surface-grain coordinates across renderer
origin changes, preventing texture motion at chunk rebases. Invalid zero-valued
resolution/radius overrides now fail instead of silently selecting defaults.
Selected captures are reproducible with `tools/capture_scenes.py`.

Launcher refinement: `run.sh` now requests fullscreen on the primary display;
`--windowed` is an explicit development override. Esc exits immediately and P
owns pause/resume. Both behaviors were verified on Metal and OpenGL.

## Canonical integration and music restoration

Area: entry point, launch behavior and procedural music. Acceptance: `app.py`
and `run.sh` launch New Wave; launcher is fullscreen by default; Escape exits;
the existing score plays without terminal errors; OpenGL remains functional.

Implementation: New Wave became the sole public entry point, and the former
implementation is retained only as an internal provider for mature audio code.
The original Rhodes/strings generator now runs through an adaptive layer driven
by speed, daylight, rain and biome. FluidSynth polyphony is capped at 64 and one
effects group is used. Platform-invalid device resets and an unsupported limiter
setting were removed. `--no-music` retains ambience; `--no-audio` silences both.

Evidence: the complete validator passed 11/11 groups in 34.68s. A real 300-frame
Metal run identified Apple M4/Metal, reported both audio and music enabled, and
held 59.997 FPS with .801ms sampled GPU scene time; there were no FluidSynth
errors. A separate OpenGL 4.1 context rendered successfully. The launcher report
confirmed `fullscreen: true` at a 3840×2160 display with explicit 1280×720 render
resolution. Short startup smoke reports include compilation/pipeline warmup and
are compatibility evidence, not replacements for the sustained benchmarks.

Final verification: passed. Reports are `music-metal-smoke.json`,
`opengl-integration-smoke.json`, `fullscreen-launch-smoke.json`, and
`validation-integration.json` under `development/evidence`.
