"""Deterministic, bounded CPU world generation; all GPU ownership stays on the main thread."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import math
import time
import numpy as np


@dataclass(frozen=True)
class WorldConfig:
    seed: int = 2026
    chunk_size: float = 128.0
    radius: int = 4
    resolution: int = 24
    density: float = 1.0
    workers: int = 2
    road_spacing: float = 384.0

    def __post_init__(self):
        if self.chunk_size <= 0 or self.radius < 1 or self.resolution < 4:
            raise ValueError("Positive chunk size, radius >= 1 and resolution >= 4 required")
        if self.density < 0 or self.workers < 1 or self.road_spacing < 64:
            raise ValueError("Invalid density, worker count or road spacing")


@dataclass(frozen=True)
class Prop:
    kind: str
    x: float
    y: float
    z: float
    scale: float
    yaw: float
    color: tuple[float, float, float]
    radius: float


@dataclass
class Chunk:
    key: tuple[int, int]
    origin: tuple[float, float]
    vertices: np.ndarray
    props: list[Prop] = field(default_factory=list)
    generation_ms: float = 0.0


class World:
    """Stable global double coordinates and small, chunk-local float32 mesh coordinates.

    update schedules at most workers*2 jobs; completed publishes only currently wanted
    chunks, so teleporting cannot leak obsolete jobs or chunks. Call both every frame.
    """
    def __init__(self, config: WorldConfig | None = None):
        self.config = config or WorldConfig()
        self.chunks: dict[tuple[int, int], Chunk] = {}
        self._executor = ThreadPoolExecutor(max_workers=self.config.workers, thread_name_prefix="terrain")
        self._pending = {}
        self._wanted = set()
        self._evicted = []
        self.generation_times_ms = []
        self._phase = (self.config.seed % 10007) * .013

    def _distance(self, x, z):
        spacing = self.config.road_spacing
        a = x - 28.0 * np.sin(z * .003)
        b = z - 28.0 * np.sin(x * .003)
        return np.minimum(np.abs((a + spacing / 2) % spacing - spacing / 2),
                          np.abs((b + spacing / 2) % spacing - spacing / 2))

    def road_distance(self, x: float, z: float) -> float:
        s = self.config.road_spacing
        return min(abs((x - 28 * math.sin(z * .003) + s / 2) % s - s / 2),
                   abs((z - 28 * math.sin(x * .003) + s / 2) % s - s / 2))

    def _height(self, x, z):
        d = self._distance(x, z)
        # Flatten beyond the shoulder by a full mesh-cell diagonal. This prevents
        # coarse terrain triangles protruding through the narrow road ribbons.
        apron = 7. + self.config.chunk_size / self.config.resolution * math.sqrt(2.)
        blend = np.clip((d - apron) / 46., 0., 1.)
        blend = blend * blend * (3. - 2. * blend)
        p = self._phase
        hills = 8 + 7 * np.sin(x * .009 + p) * np.cos(z * .011 - p)
        hills += 3 * np.sin(x * .027 + z * .019 + p)
        ridge_blend = np.clip((d-apron)/150.,0.,1.)
        ridge_blend = ridge_blend*ridge_blend*(3-2*ridge_blend)
        ridge = 42*(.5+.5*np.sin(x*.0027+z*.0011+p))**2
        return 1.3 * np.sin(x * .0018) * np.sin(z * .0018) + blend * hills + ridge_blend*ridge

    def height(self, x: float, z: float) -> float:
        d = self.road_distance(x, z)
        apron = 7. + self.config.chunk_size / self.config.resolution * math.sqrt(2.)
        b = max(0., min(1., (d - apron) / 46.))
        b = b * b * (3. - 2. * b)
        p = self._phase
        hills = 8 + 7 * math.sin(x * .009 + p) * math.cos(z * .011 - p)
        hills += 3 * math.sin(x * .027 + z * .019 + p)
        rb=max(0.,min(1.,(d-apron)/150.))
        rb=rb*rb*(3-2*rb)
        ridge=42*(.5+.5*math.sin(x*.0027+z*.0011+p))**2
        return 1.3 * math.sin(x * .0018) * math.sin(z * .0018) + b * hills + rb*ridge

    def biome(self, x: float, z: float) -> str:
        t = math.sin(x * .0017 + self._phase) + math.cos(z * .0021 - self._phase)
        return "meadow" if t > .6 else "woodland" if t > -.8 else "drylands"

    def surface_height(self, x: float, z: float) -> float:
        """Collision height of rendered triangles, not the underlying smooth field.

        Four analytic samples locate the same cell and diagonal as generate_chunk.
        This remains available before a streamed mesh exists, including at negative
        and rebased coordinates. Road ribbons use the smooth, almost planar base;
        their four-metre tessellation differs by less than 0.02 mm on road slopes.
        """
        c=self.config
        ox=math.floor(x/c.chunk_size)*c.chunk_size
        oz=math.floor(z/c.chunk_size)*c.chunk_size
        step=c.chunk_size/c.resolution
        ix=min(c.resolution-1,math.floor((x-ox)/step))
        iz=min(c.resolution-1,math.floor((z-oz)/step))
        x0,z0=ox+ix*step,oz+iz*step
        fx,fz=(x-x0)/step,(z-z0)/step
        h10,h01=self.height(x0+step,z0),self.height(x0,z0+step)
        if fx+fz <= 1:
            terrain=self.height(x0,z0)*(1-fx-fz)+h10*fx+h01*fz
        else:
            terrain=self.height(x0+step,z0+step)*(fx+fz-1)+h10*(1-fz)+h01*(1-fx)
        distance=self.road_distance(x,z)
        if distance < 7:
            return max(terrain,self.height(x,z)+(.045 if distance < 5.8 else .025))
        return terrain

    def _colors(self, x, z):
        t = np.sin(x * .0017 + self._phase) + np.cos(z * .0021 - self._phase)
        meadow = np.array([.47, .43, .24])
        forest = np.array([.28, .34, .19])
        dry = np.array([.60, .49, .29])
        a = np.clip((t + 1.1) / .7, 0, 1)[..., None]
        b = np.clip((t - .3) / .6, 0, 1)[..., None]
        color = dry * (1 - a) + forest * a
        color = color * (1 - b) + meadow * b
        variation = .90 + .07 * np.sin(x * .16 + np.cos(z * .13))
        variation += .07 * np.sin(x * .047 + self._phase) * np.cos(z * .061)
        return color * variation[..., None]

    def generate_chunk(self, key: tuple[int, int]) -> Chunk:
        started = time.perf_counter()
        c = self.config
        ox, oz = key[0] * c.chunk_size, key[1] * c.chunk_size
        n = c.resolution
        lx, lz = np.meshgrid(np.linspace(0, c.chunk_size, n + 1), np.linspace(0, c.chunk_size, n + 1))
        x, z = lx + ox, lz + oz
        y = self._height(x, z)
        # Analytic-world finite differences give identical normals at shared borders.
        dx = (self._height(x + .2, z) - self._height(x - .2, z)) / .4
        dz = (self._height(x, z + .2) - self._height(x, z - .2)) / .4
        normals = np.stack((-dx, np.ones_like(y), -dz), axis=-1)
        normals /= np.linalg.norm(normals, axis=-1)[..., None]
        grid = np.concatenate((np.stack((lx, y, lz), axis=-1), normals, self._colors(x, z)), axis=-1)
        # CCW when viewed from above.
        terrain = np.stack((grid[:-1, :-1], grid[1:, :-1], grid[:-1, 1:],
                            grid[:-1, 1:], grid[1:, :-1], grid[1:, 1:]), axis=2).reshape(-1, 9)
        pieces = [terrain]
        # Exact clipped ribbons: no staircase road edges, matching subdivisions on borders.
        for axis in (0, 1):
            start, cross = (oz, ox) if axis == 0 else (ox, oz)
            steps = max(8, int(c.chunk_size / 4))
            along = np.linspace(start, start + c.chunk_size, steps + 1)
            low = math.floor((cross - 38) / c.road_spacing)
            high = math.ceil((cross + c.chunk_size + 38) / c.road_spacing)
            for lane in range(low, high + 1):
                centers = lane * c.road_spacing + 28 * np.sin(along * .003)
                if np.max(centers) < cross - 7 or np.min(centers) > cross + c.chunk_size + 7:
                    continue
                for left, right, color, lift in ((-7., 7., (.39, .36, .29), .025),
                                                (-5.8, 5.8, (.105, .12, .13), .045),
                                                (-5.35, -5.20, (.77, .76, .63), .065),
                                                (5.20, 5.35, (.77, .76, .63), .065),
                                                (-.08, .08, (.84, .68, .32), .075)):
                    aa = np.clip(centers + left, cross, cross + c.chunk_size)
                    bb = np.clip(centers + right, cross, cross + c.chunk_size)
                    px = np.stack((aa, bb), axis=-1) if axis == 0 else np.repeat(along[:, None], 2, axis=1)
                    pz = np.repeat(along[:, None], 2, axis=1) if axis == 0 else np.stack((aa, bb), axis=-1)
                    py = self._height(px, pz) + lift
                    verts = np.zeros((steps + 1, 2, 9))
                    verts[..., :3] = np.stack((px - ox, py, pz - oz), axis=-1)
                    verts[..., 4] = 1
                    verts[..., 6:] = color
                    valid = (bb[:-1] > aa[:-1]) | (bb[1:] > aa[1:])
                    if left == -.08:
                        valid &= (np.floor((along[:-1] + along[1:]) / 2 / 7) % 2 == 0)
                    # Omit markings where the other road intersects.
                    if abs(left) < 5.5 and right - left < 1:
                        mid = (along[:-1] + along[1:]) / 2
                        other = mid - 28 * np.sin(centers[:-1] * .003)
                        valid &= abs((other + c.road_spacing / 2) % c.road_spacing - c.road_spacing / 2) > 8
                    if axis == 0:
                        tris = np.stack((verts[:-1, 0], verts[1:, 0], verts[:-1, 1], verts[:-1, 1], verts[1:, 0], verts[1:, 1]), axis=1)
                    else:
                        tris = np.stack((verts[:-1, 0], verts[:-1, 1], verts[1:, 0], verts[:-1, 1], verts[1:, 1], verts[1:, 0]), axis=1)
                    pieces.append(tris[valid].reshape(-1, 9))
        # SeedSequence avoids platform-dependent Python hashes and handles negative keys.
        rng = np.random.default_rng(np.random.SeedSequence([c.seed & 0xffffffff, key[0] & 0xffffffff, key[1] & 0xffffffff]))
        props = []
        # Reflectors establish scale and road rhythm. Globally spaced and assigned
        # by position, they reproduce exactly across positive/negative chunks.
        for axis in (0,1):
            start,cross=(oz,ox) if axis==0 else (ox,oz)
            for index in range(math.floor(start/32), math.ceil((start+c.chunk_size)/32)):
                along=index*32.
                for lane in range(math.floor((cross-38)/c.road_spacing),math.ceil((cross+c.chunk_size+38)/c.road_spacing)+1):
                    center=lane*c.road_spacing+28*math.sin(along*.003)
                    for side in (-1,1):
                        px,pz=(center+side*8.8,along) if axis==0 else (along,center+side*8.8)
                        if ox<=px<ox+c.chunk_size and oz<=pz<oz+c.chunk_size and self.road_distance(px,pz)>8:
                            props.append(Prop("post",px,self.surface_height(px,pz),pz,1.,0. if axis==0 else math.pi/2,(.8,.8,.7),0.))
        # A recognisable landmark every two road intervals. Placement is a pure
        # global-coordinate function, assigned to precisely one owning chunk.
        landmark_spacing = c.road_spacing * 2
        for region_x in range(math.floor(ox / landmark_spacing)-1, math.floor((ox+c.chunk_size) / landmark_spacing)+1):
            for region_z in range(math.floor(oz / landmark_spacing)-1, math.floor((oz+c.chunk_size) / landmark_spacing)+1):
                pz = region_z * landmark_spacing + 64
                px = region_x * landmark_spacing + 28 * math.sin(pz*.003) + 29
                if ox <= px < ox+c.chunk_size and oz <= pz < oz+c.chunk_size:
                    props.append(Prop("tower", px, self.surface_height(px,pz), pz, 1.3, 0., (.6,.6,.5), 3.5))
        for _ in range(int(44 * c.density)):
            px, pz = ox + rng.uniform(4, c.chunk_size - 4), oz + rng.uniform(4, c.chunk_size - 4)
            road = self.road_distance(px, pz)
            if road < 15:
                continue
            biome = self.biome(px, pz)
            choice = rng.random()
            # Smooth global clusters preserve clear meadows and denser groves,
            # without any change of distribution at chunk boundaries.
            grove = .5 + .25*math.sin(px*.039+self._phase) + .25*math.cos(pz*.032)
            if choice > .08 and rng.random() > .18 + .82*grove:
                continue
            kind = "building" if choice < .035 and 18 < road < 55 else "rock" if biome == "drylands" or choice < .13 else "pine" if biome == "woodland" or rng.random() < .18 else "broadleaf"
            scale = float(rng.uniform(.8, 1.7))
            radius = (4.0 if kind == "building" else 1.4 if kind == "rock" else .42) * scale
            # Keep solid footprints inside ownership bounds, so independently
            # generated neighbours cannot create overlapping collision shapes.
            if min(px-ox, ox+c.chunk_size-px, pz-oz, oz+c.chunk_size-pz) < radius+1:
                continue
            rz=round((pz-64)/landmark_spacing)
            tz=rz*landmark_spacing+64
            rx=round((px-29-28*math.sin(tz*.003))/landmark_spacing)
            tx=rx*landmark_spacing+28*math.sin(tz*.003)+29
            if (px-tx)**2+(pz-tz)**2 < (radius+5.5)**2:
                continue
            if any((p.x - px)**2 + (p.z - pz)**2 < (p.radius + radius + 2)**2 for p in props):
                continue
            color = (.65, .58, .43) if kind == "building" else (.39, .41, .36) if kind == "rock" else (.15, .29, .16)
            props.append(Prop(kind, float(px), self.surface_height(px, pz), float(pz), scale, float(rng.uniform(0, math.tau)), color, radius))
        # Sparse bunches break up the smooth verge. No colliders; shared mesh has
        # only 12 triangles and can be culled independently by the renderer.
        for _ in range(int(64*c.density)):
            px,pz=ox+rng.uniform(1,c.chunk_size-1),oz+rng.uniform(1,c.chunk_size-1)
            distance=self.road_distance(px,pz)
            if distance < 9 or distance > 48 or self.biome(px,pz) == "woodland":
                continue
            if any((p.x-px)**2+(p.z-pz)**2 < (p.radius+1)**2 for p in props if p.radius > 0):
                continue
            props.append(Prop("grass",float(px),self.surface_height(px,pz),float(pz),float(rng.uniform(.8,1.4)),float(rng.uniform(0,math.tau)),(.48,.46,.25),0.))
        return Chunk(key, (ox, oz), np.concatenate(pieces).astype(np.float32), props, (time.perf_counter() - started) * 1000)

    def update(self, x: float, z: float):
        c = self.config
        center = (math.floor(x / c.chunk_size), math.floor(z / c.chunk_size))
        self._wanted = {(center[0] + dx, center[1] + dz) for dx in range(-c.radius, c.radius + 1) for dz in range(-c.radius, c.radius + 1)}
        for key in list(self.chunks):
            if key not in self._wanted:
                del self.chunks[key]
                self._evicted.append(key)
        for key, future in list(self._pending.items()):
            if key not in self._wanted and future.cancel():
                del self._pending[key]
        missing = self._wanted - self.chunks.keys() - self._pending.keys()
        nearest = sorted(missing, key=lambda k: ((k[0] - center[0])**2 + (k[1] - center[1])**2, k))
        for key in nearest[:max(0, c.workers * 2 - len(self._pending))]:
            self._pending[key] = self._executor.submit(self.generate_chunk, key)

    def completed(self) -> list[Chunk]:
        ready = []
        for key, future in list(self._pending.items()):
            if future.done():
                del self._pending[key]
                chunk = future.result()
                self.generation_times_ms.append(chunk.generation_ms)
                self.generation_times_ms = self.generation_times_ms[-256:]
                if key in self._wanted:
                    self.chunks[key] = chunk
                    ready.append(chunk)
        return ready

    def drain_evicted(self):
        evicted, self._evicted = self._evicted, []
        return evicted

    @property
    def pending_count(self):
        return len(self._pending)

    def nearby_colliders(self, x, z, radius=16):
        size = self.config.chunk_size
        for cx in range(math.floor((x - radius) / size), math.floor((x + radius) / size) + 1):
            for cz in range(math.floor((z - radius) / size), math.floor((z + radius) / size) + 1):
                chunk = self.chunks.get((cx, cz))
                if chunk:
                    for prop in chunk.props:
                        if prop.radius > 0 and (prop.x - x)**2 + (prop.z - z)**2 < (radius + prop.radius)**2:
                            yield prop

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
