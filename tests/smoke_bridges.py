"""GL-free smoke test for plan-v2 phase 3: bridges & viaducts.

Covers: deterministic span detection (river bridges, ravine viaducts,
rare landmarks) with sane lengths and frequencies, the ravine actually
carving the terrain mesh below the road, no tunnel/bridge overlap, and
the on_bridge predicate driving the integration gates.

Usage:  ./env/bin/python tests/smoke_bridges.py   (from the repo root)
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import app  # noqa: E402

# --- span detection ----------------------------------------------------------
rivers, viaducts, landmarks = [], [], []
for k in range(60):
    for sp in app._bridge_spans_cell(k):
        L = sp['s1'] - sp['s0']
        assert L >= 60.0, f"sliver span: {L:.0f} m"
        if sp['kind'] == 'river':
            rivers.append(sp)
            if sp['landmark']:
                landmarks.append(sp)
        else:
            viaducts.append(sp)
            assert sp['depth'] >= 9.0
assert len(rivers) >= 20, f"too few river bridges: {len(rivers)}"
assert len(viaducts) >= 2, f"too few viaducts: {len(viaducts)}"
assert 1 <= len(landmarks) <= 20, f"landmark count off: {len(landmarks)}"
print(f"spans/120km: {len(rivers)} river bridges, {len(viaducts)} "
      f"viaducts, {len(landmarks)} landmarks")

# determinism
app._BRIDGE_SPANS_CACHE.clear()
again = app._bridge_spans_cell(3)
app._BRIDGE_SPANS_CACHE.clear()
assert app._bridge_spans_cell(3) == again, "spans not deterministic"

# --- no tunnel/bridge overlap ---------------------------------------------------
for k in range(60):
    t = app._tunnel_for_cell(k)
    if not t:
        continue
    for sv in (t['s0'], (t['s0'] + t['s1']) / 2, t['s1']):
        assert not app.on_bridge(sv), \
            f"bridge span under a tunnel at {sv:.0f}"
print("exclusion: no bridge span overlaps a tunnel bore")

# --- ravine carves the terrain ---------------------------------------------------
vd = viaducts[0]
mid = (vd['s0'] + vd['s1']) / 2
assert app.ravine_depth_at(mid) > vd['depth'] * 0.9
assert app.ravine_depth_at(vd['s0'] - 30.0) == 0.0
s_arr = np.array([mid - 250.0, mid], dtype=np.float32)
verts = app.build_side_arrays(s_arr, 0.0, +1, 0.0, (0.6, 0.6, 0.6))[0]
K = verts.shape[0] // 2
y_out = float(verts[0 * K + 2, 1]) - app.curve_y(float(s_arr[0]))
y_mid = float(verts[1 * K + 2, 1]) - app.curve_y(float(s_arr[1]))
assert y_mid < y_out - 7.0, \
    f"terrain not carved under viaduct: {y_mid:+.1f} vs {y_out:+.1f}"
print(f"ravine: terrain {y_mid:+.1f} m below road at centre "
      f"(vs {y_out:+.1f} m outside the span)")

# --- predicate + integration gates -----------------------------------------------
sp = rivers[0]
smid = (sp['s0'] + sp['s1']) / 2
assert app.on_bridge(smid)
assert not app.on_bridge(sp['s0'] - 60.0)
print("on_bridge: true mid-span, false off-span")

print("ALL ASSERTIONS PASSED")
