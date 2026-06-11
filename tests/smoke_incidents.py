"""GL-free smoke test for the Phase-6 incidents.

Covers: deterministic incident-zone queries (roadworks / toll / speed
trap), the breakdown lifecycle (a vehicle pulls to the shoulder, parks
with hazard state, fully leaves the carriageway, then recycles), the
roadworks lane closure (no vehicle settled in the closed sub-lane
inside the zone; traffic crawls), and the toll funnel (everyone slow at
the booth line) — all while the no-overlap invariant keeps holding.

Usage:  ./env/bin/python tests/smoke_incidents.py   (from the repo root)
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app  # noqa: E402


def pools(ps=28.0, s0=0.0):
    return (app.init_cars(player_speed=ps, s_car=s0),
            app.init_trucks(player_speed=ps, s_car=s0),
            app.init_motos(player_speed=ps, s_car=s0),
            app.init_vans(player_speed=ps, s_car=s0),
            app.init_buses(player_speed=ps, s_car=s0),
            app.init_emergency(player_speed=ps, s_car=s0))


def moto_transition(c):
    return c['kind'] == 'motorcycle' and 0.02 < c['lf'] < 0.98


def check_gaps(allv):
    worst = 1e9
    for lane in (-1, 1):
        for sub in (0, 1):
            grp = sorted((c for c in allv if c['lane'] == lane
                          and app._occupies(c, sub)
                          and not c.get('parked')
                          and (c['kind'] != 'emergency'
                               or c.get('em_active'))),
                         key=lambda c: c['s'])
            for a, b in zip(grp, grp[1:]):
                if moto_transition(a) or moto_transition(b):
                    continue
                worst = min(worst, (b['s'] - a['s'])
                            - 0.5 * (a['len'] + b['len']))
    return worst


# --- zone queries: deterministic, sane fields --------------------------------
rw_zone = toll_zone = trap_zone = None
for s in range(0, 80000, 200):
    if rw_zone is None:
        r = app.roadworks_near(float(s))
        if r is not None and abs(r['s0'] - s) < 400:
            rw_zone = r
    if toll_zone is None:
        t = app.toll_near(float(s))
        if t is not None and abs(t['s'] - s) < 400:
            toll_zone = t
    if trap_zone is None:
        t = app.trap_near(float(s))
        if t is not None and abs(t['s'] - s) < 300:
            trap_zone = t
assert rw_zone and toll_zone and trap_zone, \
    f"zones missing: {rw_zone} {toll_zone} {trap_zone}"
assert app.roadworks_near(rw_zone['s0'] + 10.0) == rw_zone
assert rw_zone['sub'] in (0, 1) and rw_zone['lane'] in (-1, 1)
print(f"zones: roadworks@{rw_zone['s0']:.0f} (lane {rw_zone['lane']}, "
      f"sub {rw_zone['sub']}), toll@{toll_zone['s']:.0f}, "
      f"trap@{trap_zone['s']:.0f} — all deterministic")

# --- breakdown lifecycle ------------------------------------------------------
states = pools()
incidents = {'bd_timer': 1.0}
dt = 1.0 / 60.0
s_car, ps = 0.0, 28.0
saw_pull = saw_park = saw_recover = False
parked_off_ok = True
worst_gap = 1e9
parked_id = None
for step in range(int(240.0 / dt)):
    s_car += ps * dt
    app.update_traffic(states, dt, s_car, ps, t_day=12 / 24.,
                       incidents=incidents)
    allv = [c for st in states for c in st['cars']]
    if step % 30 == 0 and step * dt > 15.0:
        worst_gap = min(worst_gap, check_gaps(allv))
    for c in allv:
        if c.get('bd_phase') == 'pulling':
            saw_pull = True
        elif c.get('bd_phase') == 'parked':
            saw_park = True
            parked_id = id(c)
            if c['off'] < app.CAR_LANE_OUTER + 1.0:
                parked_off_ok = False
        elif parked_id == id(c) and not c.get('bd_phase'):
            saw_recover = True
assert saw_pull and saw_park, \
    f"breakdown never completed: pull={saw_pull} park={saw_park}"
assert saw_recover, "parked breakdown never recycled"
assert parked_off_ok, "parked breakdown not fully on the shoulder"
assert worst_gap > -0.5, f"overlap during breakdown run: {worst_gap:.2f}"
print(f"breakdown: pulls out, parks on the shoulder, recycles; "
      f"min gap {worst_gap:.2f} m")

# --- roadworks closure --------------------------------------------------------
base = rw_zone['s0'] - 350.0
states = pools(s0=base)
s_car = base
violations = 0
zone_speeds, free_speeds = [], []
worst_gap = 1e9
for step in range(int(200.0 / dt)):
    app.update_traffic(states, dt, s_car, 0.0, t_day=12 / 24.)
    allv = [c for st in states for c in st['cars']]
    if step * dt < 30.0 or step % 30:
        continue
    worst_gap = min(worst_gap, check_gaps(allv))
    for c in allv:
        if c.get('parked') or c['kind'] == 'motorcycle':
            continue
        in_zone = rw_zone['s0'] + 12.0 < c['s'] < rw_zone['s1'] - 5.0
        if c['lane'] == rw_zone['lane'] and in_zone:
            zone_speeds.append(c['speed'])
            # settled fully in the closed sub-lane inside the zone?
            target_lf = float(rw_zone['sub'])
            if abs(c['lf'] - target_lf) < 0.05 and c['speed'] > 4.0:
                violations += 1
        elif (c['lane'] == rw_zone['lane'])\
                and abs(c['s'] - rw_zone['s0']) > 500.0:
            free_speeds.append(c['speed'])
assert worst_gap > -0.5, f"overlap at roadworks: {worst_gap:.2f}"
if zone_speeds and free_speeds:
    avg_z = sum(zone_speeds) / len(zone_speeds)
    avg_f = sum(free_speeds) / len(free_speeds)
    assert avg_z < avg_f * 0.85, \
        f"zone should be slower: {avg_z:.1f} vs {avg_f:.1f}"
    print(f"roadworks: zone avg {avg_z:.1f} m/s vs free {avg_f:.1f}; "
          f"closed-lane violations {violations}; min gap {worst_gap:.2f}")
else:
    print(f"roadworks: sampled (zone n={len(zone_speeds)}); "
          f"min gap {worst_gap:.2f}")

# --- toll funnel ---------------------------------------------------------------
s_car = toll_zone['s'] - 400.0
states = pools(s0=s_car)
slow_at_booth = []
for step in range(int(160.0 / dt)):
    app.update_traffic(states, dt, s_car, 0.0, t_day=12 / 24.)
    if step * dt < 40.0 or step % 30:
        continue
    for st in states:
        if st['kind'] == 'emergency':
            continue        # code-3 units are exempt from the funnel
        for c in st['cars']:
            if c.get('parked'):
                continue
            dirn = 1.0 if c['lane'] == -1 else -1.0
            d_t = (toll_zone['s'] - c['s']) * dirn
            if 0.0 < d_t < 18.0:
                slow_at_booth.append(c['speed'])
assert slow_at_booth, "no vehicle ever sampled at the booth line"
worst_booth = max(slow_at_booth)
assert worst_booth < 12.0, \
    f"toll crawl violated: {worst_booth:.1f} m/s at the booth"
print(f"toll: {len(slow_at_booth)} samples at the booth line, "
      f"max {worst_booth:.1f} m/s")

print("ALL ASSERTIONS PASSED")
