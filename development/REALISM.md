# Realism / inhabited world pass

The canonical game remains `./run.sh`: fullscreen, Escape exits, Metal by
default on macOS, `--backend opengl` for the alternate backend. Music remains
enabled. This pass adds inhabited roadside hamlets, visible initial traffic,
walking pedestrians, detailed foliage, ground texture and denser verge plants.

## Visual reference workflow

`reference/realism-before.png` is the actual baseline. The built-in Codex image
generator edited it into `reference/realism-concept.png`. The prompt requested
a photorealistic achievable driving-game target preserving the exact chase
camera, orange vintage coupe, roads and hills, with realistic asphalt, lush
irregular foliage, plaster/tile houses, sidewalks, fences, pedestrians and NPC
traffic under natural afternoon light. It explicitly excluded changed camera,
cinematic depth of field and oversized city geometry. The concept is not gameplay.

A second built-in generation produced `assets/new_wave/meadow-albedo.png`.
Prompt: square photorealistic seamless top-down roadside meadow albedo covering
roughly two metres, densely interwoven olive grass, clover, straw and small soil
flecks; neutral diffuse illumination; no shadows, perspective, objects, text or
borders. Both backends load the same texture, repeat it in world coordinates and
use mipmaps. It is an AI-generated material, not a measured PBR scan.

The five initial gaps were coarse tree silhouettes, empty ground, isolated
buildings, invisible traffic at startup and missing pedestrians. Native changes
address all five. Independent evaluation then identified oversized leaf planes,
repeated houses, disconnected paths, simple people and flat materials. The next
iteration reduced leaf size, added foliage LOD, lengthened pavements, varied
building/person tints and integrated textured ground. Realism still falls short
in character anatomy, architectural variety, surface maps and contact shading.

## Assets and populations

`tools/build_environment.py` builds a Blender library with ground pivots, metre
scale, named objects, UVs and vertex colors. Editable source is
`assets/new_wave/roadside.blend`; GLB and runtime NPZ share that stem. The game
loads the exported NPZ. Blender uses Z up; runtime and GLB use Y up. Objects
overlap at the library origin intentionally; each is a separately instanced
asset, not a scene to render as one assembly. Rebuild with:

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background --python tools/build_environment.py
```

Blender MCP was not exposed among the session tools. Installed Blender 5.2.1
was used directly in background mode. The GLB was round-trip imported to check
named mesh objects, UVs and vertex colors. No MCP operation is claimed.

Roadside hamlets use deterministic 28-metre global slots, avoiding intersections.
Buildings have conservative circular collision proxies. Decorative fences,
lamps, pavements and pedestrians are non-solid. People walk back and forth on
short pavement segments, with eight baked shared pose meshes. They are ambient
characters, not navigation/interaction agents. Their height follows the flat
roadside base at their home slot. They are culled at 140 metres.

Traffic defaults to 24 vehicles. Visible roads are populated during loading;
later replacements retain the existing fog-hidden spawn rule. NPCs still use
the existing car asset and road-following/yield model. This is not a restoration
of the former incident and overtaking systems.

Foliage uses high-detail leaves within 85 metres and a shared low-detail crown
beyond that. Grass is culled at 90 metres. Placement uses vectorized terrain
triangle sampling; chunk rebases shift cached arrays instead of rebuilding
every instance. Resident chunks and workers retain their existing bounds.

## Repeatable checks

`tests/test_population.py` checks deterministic hamlet ownership, pedestrian
road clearance, visible safe traffic startup and vectorized collision heights at
negative/large coordinates. Existing world, physics, traffic and control tests
also run. Reports and final captures are recorded in the progress log.

For a matching default scene use `--windowed --frames 180 --no-audio --screenshot
development/reference/realism-after.png`. Captures are actual renderer output.
Performance reports explicitly record resolution, preset, backend and warmup.
