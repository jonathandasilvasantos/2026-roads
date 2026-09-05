import math
import numpy as np


def normalize(v):
    v = np.asarray(v, dtype=np.float64)
    return v / max(1e-9, np.linalg.norm(v))


def look_at(eye, target):
    f = normalize(np.asarray(target) - eye)
    r = normalize(np.cross(f, (0, 1, 0)))
    u = np.cross(r, f)
    m = np.eye(4)
    m[:3, :3] = np.array([r, u, -f])
    m[:3, 3] = -m[:3, :3] @ eye
    return m, r, u, f


def perspective(aspect, near=.15, far=1100):
    f = 1 / math.tan(math.radians(60) / 2)
    return np.array([[f/aspect, 0, 0, 0], [0, f, 0, 0],
                     [0, 0, far/(near-far), far*near/(near-far)], [0, 0, -1, 0]])


def light_matrix(target, sun, extent=100):
    eye = np.asarray(target) + np.asarray(sun) * 220
    view, *_ = look_at(eye, target)
    proj = np.diag([1/extent, 1/extent, -1/450, 1.])
    return proj @ view
