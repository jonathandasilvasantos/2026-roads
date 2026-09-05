# Roads / New Wave

Free exploration is a new native GPU game mode. Classic remains runnable unchanged
with `python app.py` or `./run.sh --classic`, including its richer weather,
traffic incidents, bridges, tunnels, soundscape and asset viewer.

## Run

```sh
python3.12 -m venv env
./env/bin/python -m pip install -r requirements.txt
./run.sh
./run.sh --backend opengl
./run.sh --windowed
./run.sh --quality Performance
./run.sh --quality Quality --width 1920 --height 1080
./run.sh --time 22 --weather rain
./run.sh --cycle --seed 42
```

Metal is the default on macOS; OpenGL is the explicit fallback and the default
elsewhere. A failed Metal initialization is reported rather than silently
changing backends. Both use the same game loop, simulation, geometry, materials,
camera, input and HUD. OpenGL requires a hardware OpenGL 4.1 core context.
On this Mac the OpenGL driver itself identifies as `4.1 Metal - 90.5`; that is
still an **OpenGL API context**, distinct from wgpu's explicit Metal backend.

`run.sh` opens the primary display in fullscreen. `--windowed` is provided for
development and capture workflows. The render resolution is fixed and explicit,
independent of the display or window's backing scale. Fullscreen scales that image
to the display; it does not silently change render quality. All presets default
to 1280×720 and are capped at 60 FPS.
`--uncapped` disables the limiter and requests no vsync. No automatic resolution
or quality reductions occur.

| Preset | Chunk radius | Terrain divisions/chunk | Foliage multiplier | Shadow map |
|---|---:|---:|---:|---:|
| Performance | 3 | 16 | .65 | 1024² |
| Balanced (default) | 4 | 24 | 1.0 | 2048² |
| Quality | 5 | 32 | 1.35 | 2048² |

## Controls and accessibility

W/Up accelerates; S/Down progressively brakes forward motion then reverses.
A/D or Left/Right steer. Left Ctrl brakes without selecting reverse. Space
releases rear grip for a controlled slide. R searches for a nearby clear road
position, or clear terrain when far from roads, and settles the chassis. C cycles
chase, close, wide and road cameras. T cycles lighting in six-hour steps. H hides
the control legend. P pauses or resumes. Esc exits immediately; window close also
exits. F12 saves `development/reference/capture.png`.

Standard GLFW gamepads use left-stick steering, right trigger forward, left
trigger brake/reverse, A handbrake and Y recovery. Keyboard remains available.
No physical gamepad was connected for hardware validation; the mapping uses
GLFW's standard gamepad layout. Edit `controls` in `new_wave/config.json` to bind
GLFW key names, e.g. `W`, `LEFT_CONTROL`, `SPACE`. Controller deadzone is configurable.
`--reduced-motion` stabilizes chase distance and reduces camera lag. The high
contrast HUD includes written speed/gear feedback; no action depends on color.

## Architecture and physics

`drive.py` delegates to `new_wave/game.py`. The simulation has no graphics API
dependency. `vehicle.py` advances at 120 Hz with render interpolation; traffic
advances at 30 Hz. Rendering fluctuations do not alter input forces or steering
rates. Stalls over .25 seconds are bounded and dropped time is reported.

The vehicle is a grounded touring model: longitudinal engine force divided by
mass, drag and rolling resistance; progressive brakes; bounded lateral tire
acceleration and velocity inertia; a bicycle yaw model with speed-sensitive
steering; reduced grip under the handbrake; spring/damper chassis height; terrain
pitch and roll; and acceleration/cornering weight transfer. Four visible wheels
sample the actual triangulated terrain surface, spin and steer. Swept circular
collision tests prevent tunneling through nearby proxies. The model deliberately
does not simulate airborne rigid-body rolls or mechanical suspension linkages.

All physical parameters are configurable under `vehicle`: mass, center of
gravity, engine/braking forces, maximum/reverse speed, steering angle/curve/
response, suspension stiffness/damping/travel, tire grip, drag, rolling
resistance, wheelbase, track width, wheel radius, ride height, collision radius
and restitution. Defaults live in `VehicleConfig`.

## World and renderer

World coordinates remain CPU float64; the renderer subtracts a chunk-aligned
origin before producing any float32 positions. Both camera and objects use that
same origin. Tests cover positive/negative coordinates and positions beyond
100 million metres. Terrain meshes retain small chunk-local XZ coordinates.

Global coordinate functions create rolling terrain and a connected network of
two gently winding road families. Roads flatten the surrounding terrain before
mesh generation. Shared-coordinate heights, normals and colors keep terrain
borders consistent. `surface_height` samples the same triangle diagonal used by
the terrain mesh so wheels and props do not float on an unrelated analytic curve.
Road ribbons, shoulders, markings and reflectors are assigned to chunk bounds.

Seeded chunks contain meadow, woodland and dryland terrain; clustered pines and
broadleaf trees; rocks, houses, small verge grasses and water-tower landmarks.
Traffic occupies both road families. Seed affects terrain and placement; the
road network itself is coordinate-deterministic and seed-independent.

`World.update` keeps a square radius around the car and schedules nearest missing
chunks on two workers, at most four outstanding jobs. Finished CPU arrays are
uploaded on the main thread. Evicted chunks release CPU references and GPU buffers;
stale jobs are cancelled or discarded. Shared prop meshes use GPU instancing.
Distance/view culling bounds visible props; fog hides the outer streaming edge.
The default world has at most 81 resident chunks. Telemetry and caches are bounded.

Metal uses wgpu-native, WGSL, GPU buffers, a depth shadow map, procedural surface
grain, sky/cloud shaders, directional/hemisphere lighting, atmospheric fog,
glass reflections, headlamps, emissive lamps and a rain overlay. OpenGL implements
equivalent shaders and instancing through GLSL. No gameplay framebuffer readback
or CPU grading is performed. Readback is reserved for explicit screenshots and
infrequent GPU timestamps. The HUD is cached and uploaded at 10 Hz.

Configuration: `new_wave/config.json` exposes seed, chunk size, density, road
spacing, traffic count, quality, resolution, bindings and vehicle parameters.
`--radius` overrides the preset generation radius. `--x`, `--z`, `--yaw`, `--time`,
`--weather` and `--camera` select repeatable inspection scenes.

## Validation and evidence

```sh
./env/bin/python tools/validate.py --report development/evidence/validation.json
./env/bin/python drive.py --backend metal --benchmark 90 --warmup 8 --no-audio \
  --report development/evidence/metal-balanced-90s.json
./env/bin/python drive.py --backend opengl --benchmark 90 --warmup 8 --no-audio \
  --report development/evidence/opengl-balanced-90s.json
./env/bin/python drive.py --replay development/replay-controls.json --frames 1600 \
  --no-audio --report development/evidence/controls-replay.json
```

The benchmark uses actual vehicle forces at 72% throttle and changes desired
heading every 22 seconds through north, west, south and east. It crosses roads,
terrain, positive/negative coordinates and chunk boundaries. Warmup excludes
first-use pipeline compilation. Reports include frame mean/p99/maximum, CPU
physics/stream/upload time, GPU timestamps, process memory high-water mark,
active chunks/jobs/buffers, route positions, settings and dropped physics time.
The 1% low is `1000 / p99(frame_ms)`, not the average of the slowest one percent.

GPU timers bracket shadow/scene/rain; Metal excludes HUD/presentation, OpenGL
includes HUD but excludes final blit/presentation. Metal uses the next pass's
beginning timestamp because end-of-pass queries on the available driver returned
zero. Invalid timestamp pairs are rejected. These are GPU elapsed measurements,
not full hardware utilization percentages. Process RSS includes unified-memory
allocations visible to the process; dedicated GPU memory/utilization and whole
system CPU utilization were not available from these APIs.

Measured on Mac mini Mac16,10, Apple M4 (10 CPU / 10 GPU cores), 16 GB unified
memory, macOS 26.5.1 (25F80), attached 3840×2160 60 Hz display. Both principal runs
used Balanced, explicit1280×720, radius4, 12 traffic slots, clear16h lighting,
vsync and60 FPS limiter, 90 seconds after eight seconds of warmup:

| API | Average FPS | p99-equivalent 1% low | Peak frame | GPU scene mean | Process peak RSS |
|---|---:|---:|---:|---:|---:|
| Metal | 59.995 | 59.990 | 17.817 ms | .625 ms | 239.9 MiB |
| OpenGL | 59.944 | 59.942 | 29.798 ms | 1.177 ms | 236.2 MiB |

Both maintained at most81 chunks/four jobs and dropped zero measured physics
time. OpenGL had an isolated peak above budget; its p99 remained16.683ms.
Metal process CPU averaged23.1% of one core. JSON files include the corresponding
OpenGL CPU/subsystem figures and sampled route. CPU regression tests ran during
part of the Metal run, so that result includes some additional system load.
Classic's different calibration scene measured29.53 FPS with CPU color grading;
its baseline JSON is diagnostic, not an identical-scene comparison.

Selected real screenshots and generated concepts are in `development/reference`:
`before-driving.png` (Classic baseline), `concept-road.png`, `concept-car.png`
(generated targets), `car-close-after.png`, and backend-specific night/rain shots.
Concepts were generated using Codex's built-in image tool from actual screenshots;
they are explicitly **not** gameplay results. Prompts targeted the same camera,
orange coupe/road identity, warmer coherent terrain, atmospheric depth, improved
glass and paint, grounded objects and economically renderable geometry.

See `PROGRESS.md` for evaluation cycles and `ASSETS.md` for Blender source,
exports, scale, normals, UVs, rebuild commands and round-trip validation.

## Known limits / next improvements

The visual target is a polished stylized vertical slice, not photorealism. The
generated concepts still exceed the runtime scene in vegetation density, fine
surface detail, soft shadows and distant architectural variety. Higher-value
next work is more authored landmark families and textured foliage/ground cover.

Collision proxies are deliberately simple; buildings use conservative circular
footprints, and vegetation canopies are non-solid. Traffic is a bounded ambient
road-following model rather than the full Classic IDM/MOBIL and incident system.
The chassis remains supported by terrain rather than becoming an airborne rigid
body. Recovery handles invalid/wedged states; rollover dynamics are not simulated.
World seed reproduces geometry, not the travel-history-dependent ambient traffic
population. Very large coordinates remain limited by floating-point precision.
There is no save game, multiplayer, or destination mission system.

Blender MCP was not available as a callable integration or listening service.
Official Blender 5.2.1 was installed and validated through background Python.
No manual integration is needed to run/rebuild the game. To use MCP interactively,
the user would need to install/configure a compatible Blender MCP add-on and
connect its server; this project does not silently install third-party add-ons.
