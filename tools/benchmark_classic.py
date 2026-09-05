"""Repeatable legacy baseline; excludes initialization and first 20 frames."""
import json
import pathlib
import sys
import time
import resource

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import app

samples = []
flip = app.pygame.display.flip
previous = None

def measured_flip():
    global previous
    flip()
    now = time.perf_counter()
    if previous is not None:
        samples.append((now - previous) * 1000)
    previous = now

app.pygame.display.flip = measured_flip
try:
    app.main(sys.argv[1:])
finally:
    a = app.np.array(samples[20:])
    if len(a):
        result = dict(frames=len(a), average_fps=1000 / float(a.mean()),
                      p99_ms=float(app.np.percentile(a, 99)), peak_ms=float(a.max()),
                      max_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576,
                      note="Legacy OpenGL; wall frame intervals including presentation; excludes first 20 frames")
        print(json.dumps(result, indent=2))
