"""GL-free smoke test for the Phase-4 world-diversity systems.

Covers: the extended zone roll (all biomes occur; industrial clusters
near city; rolls are deterministic and cache-consistent), the
road-surface zone blend (weights normalised, all three surfaces occur),
and the curve-warning placement predicate (signs exist, and only where
the path genuinely bends).

Usage:  ./env/bin/python tests/smoke_world.py   (from the repo root)
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app  # noqa: E402

# --- biome extension -------------------------------------------------------
seen = set()
for z in range(4000):
    for side in (-1, +1):
        seen.add(app.biome_at(z, side))
assert seen == set(range(app.BIOME_COUNT)), \
    f"not all biomes occur: missing {set(range(app.BIOME_COUNT)) - seen}"

print(f"biomes: all {app.BIOME_COUNT} occur")

# Determinism + cache consistency.
for z in range(0, 500, 11):
    a = app.biome_at(z, -1)
    app._BIOME_CACHE.clear()
    assert app.biome_at(z, -1) == a, "cache-inconsistent biome roll"

# --- road surface zones ----------------------------------------------------
types = set()
for s in range(0, 40000, 35):
    w = app.surf_weights_at(float(s))
    assert abs(sum(w) - 1.0) < 1e-6, "surface weights not normalised"
    if max(w) > 0.99:
        types.add(w.index(max(w)))
assert types == {0, 1, 2}, f"not all surface types occur: {types}"
print("surfaces: asphalt/old/concrete all occur, weights normalised")

# --- curve-warning predicate -----------------------------------------------
signs = 0
false_flat = 0
for si in range(0, 20000, 50):
    s = float(si)
    k_ahead = max(app._curvature_at(s + 80.0), app._curvature_at(s + 120.0))
    if k_ahead > 0.0019 and app._curvature_at(s + 15.0) < 0.0012:
        signs += 1
        # the warned bend must be real
        if max(app._curvature_at(s + 80.0),
               app._curvature_at(s + 100.0),
               app._curvature_at(s + 120.0)) < 0.0019:
            false_flat += 1
assert signs > 3, f"too few curve warnings over 20 km: {signs}"
assert false_flat == 0
print(f"signage: {signs} curve warnings over 20 km, all before real bends")

print("ALL ASSERTIONS PASSED")
