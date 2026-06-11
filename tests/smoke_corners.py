"""GL-free smoke test for plan-v2 phase 1: corners & cornering.

Covers: corner-zone determinism and variety, path smoothness (no slope
kinks at bump edges), the curvature cap that protects the terrain
skirt from folding, scalar/numpy curve agreement, superelevation
bounds, and the behavioural core — traffic genuinely brakes through a
sharp bend while the no-overlap invariant holds.

Usage:  ./env/bin/python tests/smoke_corners.py   (from the repo root)
"""
import sys
import math
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import app  # noqa: E402

# --- geometry ----------------------------------------------------------------
s = np.arange(0.0, 120000.0, 1.0)
x = app.curve_x_np(s)
curv = x[2:] - 2 * x[1:-1] + x[:-2]
kmax = float(np.max(np.abs(curv)))
assert kmax < 0.0125, f"curvature exceeds terrain-fold limit: {kmax:.4f}"
sharp_m = int(np.sum(np.abs(curv) > 0.0075))
assert sharp_m > 800, f"too few sharp corners over 120 km: {sharp_m} m"
print(f"geometry: max kappa {kmax:.4f} (< fold limit), "
      f"{sharp_m} sharp metres / 120 km")

# scalar/np agreement + determinism
for sv in (123.4, 9314.2, 57777.0, 119000.5):
    assert abs(app.curve_x(sv)
               - float(app.curve_x_np(np.array([sv]))[0])) < 1e-6
app._CORNER_CACHE.clear()
for z in (3, 17, 41):
    a = app.corner_zone_params(z)
    app._CORNER_CACHE.clear()
    assert app.corner_zone_params(z) == a, "corner zone not deterministic"

# zone variety
kinds = {'plain': 0, 'wind': 0, 'hair': 0, 'straight': 0}
for z in range(200):
    p = app.corner_zone_params(z)
    if p['damp'] is not None:
        kinds['straight'] += 1
    elif len(p['bumps']) >= 2:
        kinds['wind'] += 1
    elif len(p['bumps']) == 1:
        kinds['hair'] += 1
    else:
        kinds['plain'] += 1
assert all(v > 5 for v in kinds.values()), f"zone mix degenerate: {kinds}"
print(f"zones: {kinds}")

# superelevation bounded and smooth
rr = app.road_roll_np(np.arange(0.0, 120000.0, 4.0))
assert float(np.max(np.abs(rr))) <= app.MAX_BANK + 1e-6
assert abs(app.road_y_at(1000.0, 0.0) - app.curve_y(1000.0)) < 1e-9
print(f"banking: roll within ±{app.MAX_BANK} (8°), centreline unchanged")

# --- behaviour: traffic brakes through a sharp bend ---------------------------
hp = None
for z in range(150):
    p = app.corner_zone_params(z)
    if len(p['bumps']) == 1:
        s0, L, A = p['bumps'][0]
        k = 3 * abs(A) * math.pi ** 2 / L ** 2
        if k > 0.0072:
            hp = (s0 + L / 2, k)
            break
assert hp, "no sharp hairpin in the first 150 zones"
apex, kap = hp
states = (app.init_cars(player_speed=0.0, s_car=apex - 500.0),)
dt = 1.0 / 60.0
apex_v, straight_v = [], []
worst_gap = 1e9
for step in range(int(220.0 / dt)):
    app.update_traffic(states, dt, apex - 500.0, 0.0, t_day=3 / 24.)
    if step * dt < 25.0 or step % 15:
        continue
    for sub in (0, 1):
        grp = sorted((c for c in states[0]['cars']
                      if c['lane'] == -1 and not c.get('parked')
                      and app._occupies(c, sub)),
                     key=lambda c: c['s'])
        for a, b in zip(grp, grp[1:]):
            worst_gap = min(worst_gap, (b['s'] - a['s'])
                            - 0.5 * (a['len'] + b['len']))
    for c in states[0]['cars']:
        if c['lane'] != -1 or c.get('parked'):
            continue
        if abs(c['s'] - apex) < 18.0:
            apex_v.append(c['speed'])
        elif abs(c['s'] - apex) > 320.0:
            straight_v.append(c['speed'])
av = sum(apex_v) / max(1, len(apex_v))
sv = sum(straight_v) / max(1, len(straight_v))
assert apex_v and av < sv * 0.80, \
    f"no corner braking: apex {av:.1f} vs straight {sv:.1f}"
assert worst_gap > -0.5, f"overlap under corner braking: {worst_gap:.2f}"
print(f"behaviour: apex {av:.1f} m/s vs straight {sv:.1f} m/s "
      f"(kappa {kap:.4f}); min gap {worst_gap:.2f} m")

print("ALL ASSERTIONS PASSED")
