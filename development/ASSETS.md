# New Wave vehicle assets

The Enduro touring coupe is original procedural Blender geometry, with burnt orange paint, smoky glass, rally wheels, metal trim, lights, bumpers, mirrors, and fender lips. No downloaded third-party model or texture is required.

## Editable source and rebuild

- Generator: `tools/build_assets.py`.
- Editable scene: `assets/new_wave/roamer.blend`.
- Portable assembled export: `assets/new_wave/roamer.glb`.
- Runtime triangle arrays: `assets/new_wave/roamer.npz`.

Built and checked using Blender **5.2.1 LTS**, build `9e2066aef7ef` (2026-08-25). Blender's official distribution is available from <https://www.blender.org/download/>. The installed macOS application is `/Applications/Blender.app`.

From the repository root:

```sh
/Applications/Blender.app/Contents/MacOS/Blender --background --python tools/build_assets.py
env/bin/python tools/validate.py --report development/evidence/validation.json
```

Rebuilding deliberately replaces these three generated exports; preserve manual `.blend` edits separately before rebuilding. The generator leaves unrelated assets alone and suppresses redundant `.blend1` backups. It requires Blender's bundled Python, NumPy, and glTF exporter. Blender MCP was not used; headless Blender's Python API provided the asset workflow.

## Coordinates, materials, and integration

Dimensions are meters. Blender uses Z up; GLB and NPZ use Y up with vehicle forward along negative Z. The body origin projects onto the ground beneath the car center. The wheel template is centered at its hub, with axle on X, radius **0.36 m**, and width **0.276 m**. Place the four wheels at `(±0.86, 0.36, ±1.35)` before steering/spin transforms. Runtime vehicle placement adds the terrain height to the assembled ground origin.

NPZ arrays `body` and `wheel` are float32 triangle soups: each row is `[x,y,z,nx,ny,nz,r,g,b]`. Normals are flat per face with beveled silhouette edges; colors are linear RGB. GLB preserves named material properties, including enamel metallic/roughness values. NPZ intentionally carries base colors only; renderer-native lighting provides shading. Every authored mesh has UV coordinates. The simple planar UV projection is sufficient for these solid materials, but is not a production atlas for new painted textures.

Body dimensions including mirrors are **2.200 × 1.240 × 4.335 m** (width × body height × length); the roof is **1.594 m** above the ground origin. The lowest body/fender vertex is **0.354 m**. Assembled tires touch zero height. Mirrors account for the width beyond the approximately 1.85 m shell.

## Validation evidence

The generator validated finite float32 values, normalized normals, triangle counts, and bounds. The CPU validator repeats these checks, including RGB ranges, ground clearance, origin symmetry, and wheel radius.

| Asset | Triangles | Disk size |
| --- | ---: | ---: |
| Body | 2,848 | included below |
| Single wheel | 360 | included below |
| Assembled car | 4,288 | GLB approximately 292 KB |
| Runtime body + wheel template | 3,208 | NPZ approximately 41 KB |
| Editable Blender scene | 4,288 assembled | approximately 152 KB |

A fresh Blender factory scene imported the actual exported GLB successfully on 2026-09-05. It contained **136 mesh objects**, **4,288 triangles**, UV layers and materials on every mesh. Imported world bounds in Blender coordinates were `(-1.100, -2.180, 0.000)` to `(1.100, 2.155, 1.594)`, confirming orientation, scale, and tire ground contact. This check did not save or alter the source/export. The generator's Cycles preview was inspected; an initial fender-strip orientation defect was corrected and the exports regenerated. Runtime screenshots and GPU performance evidence are maintained separately under `development/reference/` and `development/evidence/` by the game integration workflow.

## Deliberate limits

The GLB is an editable/export artifact with separate component objects, not the runtime draw-call structure. Runtime uses the two batched NPZ meshes and shared instances. There is no separate authored LOD: 4,288 assembled triangles is already a modest player-vehicle budget, with distance culling used for traffic. There is no detailed triangle collision mesh; the vehicle controller uses a simplified hull/circle collision approximation. No complex suspension mechanics, animated interior, texture atlas, or emissive material channel is embedded in NPZ. These are limitations rather than claims of unsupported asset features.
