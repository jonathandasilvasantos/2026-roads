"""Hardware-free smoke test for the traffic soundscape.

After user-preference trims, the phase-5 soundscape is deliberately
minimal: automobile pass-by whooshes (cars/vans/trucks/buses, NOT
motorcycles) at 4% mechanical gain, plus the dawn birdsong ambience
loop. Horns, sirens, engine brakes, the cricket chorus and the city
hum were removed as annoying.

Covers: clip generators (finite float32, sane peaks), the stereo mixer
callback (panned one-shots land on the correct channel, loops fade in,
the varispeed loop path plays), and the pass-by driver (fires exactly
on a camera-plane crossing, pans to the correct side, louder when wet,
quiet in absolute terms, and motorcycles stay silent).

Usage:  ./env/bin/python tests/smoke_audio.py   (from the repo root)
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import app  # noqa: E402

SR = app._AUDIO_SAMPLE_RATE

# --- generators ------------------------------------------------------------
for kind in ('car', 'van', 'truck', 'bus', 'emergency'):
    clip = app.generate_passby_clip(kind, seed=1)
    assert clip.dtype == np.float32 and np.all(np.isfinite(clip))
    assert 0.5 < np.max(np.abs(clip)) <= 1.0
    assert SR < len(clip) < 3 * SR
birds = app.generate_birdsong_loop()
assert birds.dtype == np.float32 and np.all(np.isfinite(birds))
print("generators: pass-by clips + birdsong finite and normalised")

# --- mixer callback (no stream) ---------------------------------------------
mx = app.AmbientAudioMixer()
mx.add_loop('birds', birds)
mx.add_loop('vari', app.generate_birdsong_loop(seed=99), varispeed=True)
mx.set_volumes(rain=0.0, wind=0.0, brown=0.0)
mx.brown_vol = mx.rain_vol = mx.wind_vol = 0.0

# hard-left one-shot
mx.trigger_clip(app.generate_passby_clip('car'), volume=0.5, pan=-1.0)
out = np.zeros((app._AUDIO_BLOCK_SIZE, 2), dtype=np.float32)
eL = eR = 0.0
for _ in range(16):
    mx._callback(out, app._AUDIO_BLOCK_SIZE, None, None)
    assert np.all(np.isfinite(out))
    eL += float(np.sum(out[:, 0] ** 2))
    eR += float(np.sum(out[:, 1] ** 2))
assert eL > 100.0 * max(eR, 1e-12), \
    f"hard-left pan leaked right: L={eL:.4f} R={eR:.4f}"

# loops fade in (plain + varispeed paths)
mx.set_loop('birds', 0.3)
mx.set_loop('vari', 0.3, speed=1.1)
e = 0.0
for _ in range(600):
    mx._callback(out, app._AUDIO_BLOCK_SIZE, None, None)
    e += float(np.sum(out ** 2))
assert e > 1.0, "ambience loops produced no energy"
print("mixer: stereo pan correct, loops fade in, varispeed runs")


# --- pass-by driver ----------------------------------------------------------
class FakeMixer:
    def __init__(self):
        self.calls = []

    def trigger_clip(self, clip, volume=0.4, pan=0.0):
        self.calls.append((len(clip), volume, pan))


pools = (app.init_cars(player_speed=28.0),
         app.init_motos(player_speed=28.0))
dens = (1.0, 1.0)
pc = {k: [app.generate_passby_clip(k, seed=0)]
      for k in ('car', 'van', 'truck', 'bus', 'emergency')}
rng = np.random.default_rng(3)

# Force one oncoming car to cross the camera plane this frame.
fm = FakeMixer()
c = pools[0]['cars'][-1]
assert c['lane'] == +1
c['_rel_prev'] = 1.5
c['s'] = -1.5          # s_car = 0 -> rel = -1.5: crossed
c['vis'] = 0.0
c['speed'] = 10.0      # slow pass so the volume cap doesn't saturate
for o in (x for st in pools for x in st['cars'] if x is not c):
    o['_rel_prev'] = o['s']        # no other crossings
app.update_traffic_audio(fm, pools, dens, 1 / 60., 0.0, 8.0,
                         0.0, pc, rng)
passbys = [call for call in fm.calls if call[1] > 0.004]
assert len(passbys) == 1, f"expected exactly 1 pass-by, got {fm.calls}"
assert passbys[0][2] > 0.3, "oncoming (right side) should pan right"
# artificial-sound reduction: pass-bys sit at 4% of natural level
assert passbys[0][1] < 0.025, \
    f"pass-by should be very quiet: {passbys[0][1]:.4f}"

# Wet road louder than dry for the same pass.
fm2 = FakeMixer()
c['_rel_prev'] = 1.5
c['s'] = -1.5
app.update_traffic_audio(fm2, pools, dens, 1 / 60., 0.0, 8.0,
                         1.0, pc, rng)
assert fm2.calls[0][1] > passbys[0][1] * 1.3, "wet pass-by should be louder"
print("pass-bys: fire on camera-plane crossing, pan correctly, "
      "louder when wet, quiet overall")

# Motorcycles are silent: force a moto crossing, expect NO call.
fm3 = FakeMixer()
m = pools[1]['cars'][-1]
m['_rel_prev'] = 1.5
m['s'] = -1.5
m['vis'] = 0.0
m['speed'] = 30.0
for o in (x for st in pools for x in st['cars'] if x is not m):
    o['_rel_prev'] = o['s']
app.update_traffic_audio(fm3, pools, dens, 1 / 60., 0.0, 8.0,
                         0.0, pc, rng)
assert fm3.calls == [], f"motorcycles must be silent, got {fm3.calls}"
print("motorcycles: silent by design")

print("ALL ASSERTIONS PASSED")
