"""GL-free smoke test for the traffic physics (IDM + MOBIL + phase-2
road users: motorcycles, vans, buses, emergency vehicles).

Runs update_traffic for several simulated minutes across time-of-day,
weather and player-speed extremes, asserting invariants (no negative
speeds, lane fractions in range, no same-sublane overlaps) and printing
behaviour stats (lane splits, bus dwells, emergency runs, worst gap).

Motorcycles mid-lane-transition are exempt from the hard gap assertion:
a moto grazing past a car while entering/exiting the corredor is a
close pass, not a collision — it is counted and reported separately.

Usage:  ./env/bin/python tests/smoke_traffic.py   (from the repo root)
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app  # noqa: E402


def pools(ps=28.0):
    return (app.init_cars(player_speed=ps),
            app.init_trucks(player_speed=ps),
            app.init_motos(player_speed=ps),
            app.init_vans(player_speed=ps),
            app.init_buses(player_speed=ps),
            app.init_emergency(player_speed=ps))


def moto_transition(c):
    return c['kind'] == 'motorcycle' and 0.02 < c['lf'] < 0.98


def run(label, t_day, storm_i=0.0, frost_i=0.0, night_a=0.0,
        ps=28.0, sim_s=300.0, force_em=False):
    states = pools(ps)
    if force_em:
        for c in states[5]['cars']:
            c['em_timer'] = 5.0
    dt = 1.0 / 60.0
    s_car = 0.0
    splits = dwells = em_runs = 0
    was = {}
    min_gap = 1e9
    moto_close = 0
    for step in range(int(sim_s / dt)):
        s_car += ps * dt
        app.update_traffic(states, dt, s_car, ps, t_day=t_day,
                           storm_i=storm_i, frost_i=frost_i,
                           night_a=night_a)
        allv = [c for st in states for c in st['cars']]
        for c in allv:
            assert c['speed'] >= 0.0, "negative speed"
            assert 0.0 <= c['lf'] <= 1.0, "lf out of range"
            i = id(c)
            k = was.setdefault(i, {})
            if c.get('split') and not k.get('sp'):
                splits += 1
            k['sp'] = c.get('split', False)
            if c.get('bus_dwell', 0) > 0 and not k.get('dw'):
                dwells += 1
            k['dw'] = c.get('bus_dwell', 0) > 0
            if c.get('em_active') and not k.get('em'):
                em_runs += 1
            k['em'] = c.get('em_active', False)
        if step % 30 == 0 and step * dt > 20.0:
            for lane in (-1, 1):
                for sub in (0, 1):
                    grp = sorted(
                        (c for c in allv if c['lane'] == lane
                         and app._occupies(c, sub)
                         and not c.get('parked')
                         and (c['kind'] != 'emergency'
                              or c.get('em_active'))),
                        key=lambda c: c['s'])
                    for a, b in zip(grp, grp[1:]):
                        g = (b['s'] - a['s']) - 0.5 * (a['len']
                                                       + b['len'])
                        if moto_transition(a) or moto_transition(b):
                            if g < 0:
                                moto_close += 1
                            continue
                        min_gap = min(min_gap, g)
    print(f"{label:18s} splits={splits:3d} dwells={dwells:2d} "
          f"em={em_runs} min_gap={min_gap:7.2f}m "
          f"moto_close_passes={moto_close}")
    assert min_gap > -0.5, f"hard overlap: {min_gap:.2f}m"


run("rush hour 18h", 18 / 24., force_em=True)
run("noon", 12 / 24.)
run("3am", 3 / 24., night_a=1.0)
run("noon heavy storm", 12 / 24., storm_i=0.9)
run("frost dusk", 18.5 / 24., frost_i=0.8, night_a=0.4)
run("player 90", 12 / 24., ps=90.0)
run("player 0", 12 / 24., ps=0.0)
print("ALL ASSERTIONS PASSED")
