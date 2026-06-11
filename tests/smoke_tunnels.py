"""GL-free smoke test for plan-v2 phase 2: tunnels.

Covers: deterministic placement on genuine mountain runs, the portal
depth ramp, spawn suppression inside the bore (tree slots), incident
zones never overlapping a tunnel, the in-bore lane-change veto, and
the no-overlap invariant for traffic driving through.

Usage:  ./env/bin/python tests/smoke_tunnels.py   (from the repo root)
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import app  # noqa: E402

# --- placement ---------------------------------------------------------------
tunnels = []
for k in range(60):
    t = app._tunnel_for_cell(k)
    if t:
        tunnels.append(t)
assert len(tunnels) >= 3, f"too few tunnels in 156 km: {len(tunnels)}"
for t in tunnels:
    L = t['s1'] - t['s0']
    assert app.TUNNEL_MIN_LEN - 1 <= L <= app.TUNNEL_MAX_LEN + 50, L
    for f in (0.0, 0.5, 1.0):
        assert app._mountain_run_w(t['s0'] + L * f) >= 0.50, \
            "tunnel not on a mountain run"
app._TUNNEL_CACHE.clear()
assert app._tunnel_for_cell(2) == app._tunnel_for_cell(2) \
    or app._tunnel_for_cell(2) is None
print(f"placement: {len(tunnels)} tunnels / 156 km, all on mountain runs")

t0 = tunnels[0]
mid = (t0['s0'] + t0['s1']) / 2

# --- depth ramp ----------------------------------------------------------------
assert app.tunnel_depth_at(t0['s0'] - 50.0) == 0.0
assert 0.0 < app.tunnel_depth_at(t0['s0']) < 1.0
assert app.tunnel_depth_at(mid) == 1.0
assert app.in_tunnel(mid) and not app.in_tunnel(t0['s0'] - 5.0)
print("depth: 0 outside, ramps through the portal, 1 mid-bore")

# --- spawn suppression -----------------------------------------------------------
app._TREE_SLOTS.clear()
inside_slots = range(int(t0['s0'] // app.TREE_SPACING) + 1,
                     int(t0['s1'] // app.TREE_SPACING))
trees_inside = sum(1 for sl in inside_slots for side in (-1, 1)
                   if app._tree_slot(sl, side, 6) is not None)
assert trees_inside == 0, f"{trees_inside} trees grew inside the bore"
print("suppression: zero tree slots inside the bore")

# --- incident zones never overlap a tunnel ----------------------------------------
for s in range(0, 150000, 150):
    rw = app.roadworks_near(float(s))
    if rw and (app.in_tunnel(rw['s0']) or app.in_tunnel(rw['s1'])):
        raise AssertionError("roadworks inside a tunnel")
    tl = app.toll_near(float(s))
    if tl and app.in_tunnel(tl['s']):
        raise AssertionError("toll booth inside a tunnel")
    tr = app.trap_near(float(s))
    if tr and app.in_tunnel(tr['s']):
        raise AssertionError("speed trap inside a tunnel")
print("incidents: roadworks/tolls/traps never inside a bore")

# --- traffic through the tunnel: no lane changes inside, no overlaps -------------
states = (app.init_cars(player_speed=0.0, s_car=t0['s0'] - 400.0),
          app.init_trucks(player_speed=0.0, s_car=t0['s0'] - 400.0))
dt = 1.0 / 60.0
flips_inside = 0
worst = 1e9
prev_sub = {}
for step in range(int(200.0 / dt)):
    app.update_traffic(states, dt, t0['s0'] - 400.0, 0.0, t_day=12 / 24.)
    allv = [c for st in states for c in st['cars']]
    for c in allv:
        i = id(c)
        if app.in_tunnel(c['s']) and i in prev_sub \
                and c['sub'] != prev_sub[i] and app.in_tunnel(c.get('_ps', c['s'])):
            flips_inside += 1
        prev_sub[i] = c['sub']
        c['_ps'] = c['s']
    if step % 30 == 0 and step * dt > 20.0:
        for lane in (-1, 1):
            for sub in (0, 1):
                grp = sorted((c for c in allv if c['lane'] == lane
                              and app._occupies(c, sub)
                              and not c.get('parked')),
                             key=lambda c: c['s'])
                for a, b in zip(grp, grp[1:]):
                    if a['kind'] == 'motorcycle' and 0.02 < a['lf'] < 0.98:
                        continue
                    if b['kind'] == 'motorcycle' and 0.02 < b['lf'] < 0.98:
                        continue
                    worst = min(worst, (b['s'] - a['s'])
                                - 0.5 * (a['len'] + b['len']))
assert flips_inside == 0, f"{flips_inside} lane changes inside the bore"
assert worst > -0.5, f"overlap near tunnel: {worst:.2f}"
print(f"traffic: zero in-bore lane changes; min gap {worst:.2f} m")

print("ALL ASSERTIONS PASSED")
