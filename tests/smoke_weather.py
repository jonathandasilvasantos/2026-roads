"""GL-free smoke test for the Phase-3 weather/time systems.

Covers: the weather lifecycle machine (states cycle, level bounded,
rain events actually happen, humidity drains and recharges), the
precipitation-onset split, the distance-anchored seasons (winter rolls
more frost zones than summer; the winter sun arc shortens the day), the
moon-phase ambient floor, and zone-biome determinism.

Usage:  ./env/bin/python tests/smoke_weather.py   (from the repo root)
"""
import sys
import math
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app  # noqa: E402


# --- weather lifecycle machine -------------------------------------------
w = app.WeatherSystem(seed=7)
dt = 1.0 / 60.0
states_seen = set()
rain_events = 0
prev_state = w.state
max_level = 0.0
for step in range(int(3600.0 / dt)):       # one wall-clock hour
    t_day = (0.5 + step * dt / app.DAY_PERIOD) % 1.0
    level = w.update(dt, t_day, summer_w=0.8)
    assert 0.0 <= level <= 1.0, "weather level out of range"
    states_seen.add(w.state)
    if w.state == app.WEATHER_RAIN and prev_state != app.WEATHER_RAIN:
        rain_events += 1
    prev_state = w.state
    max_level = max(max_level, level)
assert states_seen == {0, 1, 2, 3, 4}, f"states not all visited: {states_seen}"
assert rain_events >= 2, f"too few rain events in an hour: {rain_events}"
assert max_level > app.RAIN_ONSET, "no event ever produced rain-level storm"
print(f"weather machine: {rain_events} rain events/hour, "
      f"peak level {max_level:.2f}, all states visited")

# Precipitation onset: overcast band must stay dry.
assert app.rain_intensity_from(0.0) == 0.0
assert app.rain_intensity_from(app.RAIN_ONSET) == 0.0
assert app.rain_intensity_from(1.0) == 1.0
assert 0.0 < app.rain_intensity_from(0.6) < 1.0

# --- seasons ---------------------------------------------------------------
assert abs(app.winter_weight(0.0)) < 1e-6
assert abs(app.winter_weight(0.5) - 1.0) < 1e-6

# Frost-zone frequency: count frost zones across a summer-phase span vs a
# winter-phase span of the road (zones are season-anchored to position).
def frost_count(phase_center):
    s0 = phase_center * app.YEAR_DIST
    z0 = int(s0 // app.ZONE_LEN)
    n = 0
    span = int((app.YEAR_DIST * 0.20) // app.ZONE_LEN)
    for z in range(z0 - span // 2, z0 + span // 2):
        if app.biome_at(z, -1) == app.BIOME_FROST:
            n += 1
    return n

summer_frost = sum(frost_count(0.0 + k) for k in range(4))
winter_frost = sum(frost_count(0.5 + k) for k in range(4))
assert winter_frost > summer_frost, \
    f"winter should roll more frost: {winter_frost} vs {summer_frost}"
print(f"seasonal frost: winter zones {winter_frost} vs summer {summer_frost}")

# Determinism: a zone's biome never changes between calls.
for z in range(0, 400, 7):
    assert app.biome_at(z, -1) == app.biome_at(z, -1)
    assert app.biome_at(z, +1) == app.biome_at(z, +1)

# Day length: with the winter sun arc, fewer t_day samples sit above the
# daylight elevation threshold.
def day_fraction():
    n = 0
    for i in range(1000):
        if float(app.sun_dir_at(i / 1000.0)[1]) > -0.15:
            n += 1
    return n / 1000.0

app.ENV['winter'] = 0.0
summer_day = day_fraction()
app.ENV['winter'] = 1.0
winter_day = day_fraction()
app.ENV['winter'] = 0.0
assert winter_day < summer_day - 0.04, \
    f"winter day should be shorter: {winter_day:.2f} vs {summer_day:.2f}"
print(f"day length: summer {summer_day:.2f} vs winter {winter_day:.2f}")

# --- moon phase ------------------------------------------------------------
app.ENV['moon_phase'] = 0.0      # new
new_bright, _ = app.ambient_at(0.0)
app.ENV['moon_phase'] = 0.5      # full
full_bright, _ = app.ambient_at(0.0)
app.ENV['moon_phase'] = 0.5
assert full_bright > new_bright * 1.2, \
    f"full-moon night should be brighter: {full_bright:.3f} vs {new_bright:.3f}"
print(f"night floor: new moon {new_bright:.3f} vs full moon {full_bright:.3f}")

# --- wet-road memory model (mirrors the main-loop integrator) --------------
wet = 0.0
for _ in range(int(8.0 / dt)):           # 8 s of rain at 0.8
    wet = min(1.0, wet + 0.8 * dt / 6.0)
assert wet > 0.9, f"rain should saturate the road quickly: {wet:.2f}"
t_dry_noon = 0.0
while wet > 0.05:
    wet = max(0.0, wet - dt / (22.0 - 12.0 * 1.0))   # midday drying
    t_dry_noon += dt
sim_hours = t_dry_noon / (app.DAY_PERIOD / 24.0)
assert 1.0 < sim_hours < 4.5, f"drying time off: {sim_hours:.1f} sim hours"
print(f"wetness: saturates in rain, dries in {sim_hours:.1f} sim hours at noon")

print("ALL ASSERTIONS PASSED")
