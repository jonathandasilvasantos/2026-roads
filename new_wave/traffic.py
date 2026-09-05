"""Bounded ambient road traffic, advanced on a deterministic 30 Hz clock.

This intentionally modest road-following model yields to nearby obstacles and
the player. The original Classic mode retains its richer race traffic behavior.
"""
from dataclasses import dataclass
import math
import random


@dataclass
class TrafficVehicle:
    x: float = 0
    z: float = 0
    y: float = 0
    yaw: float = 0
    speed: float = 0
    color: tuple = (.43, .49, .43)
    braking: bool = False
    axis: int = 0
    lane: int = 0
    along: float = 0
    direction: int = 1
    cruise: float = 12
    slot: int = 0


class Traffic:
    def __init__(self, world, count=12, fog_distance=None):
        self.world = world
        self.count = max(0, min(64, int(count)))
        self.vehicles = []
        self.fog_distance = float(fog_distance or world.config.chunk_size * world.config.radius * .85)
        self.recycle_distance = max(160., self.fog_distance * 1.3)
        self._accumulator = 0.
        self._rng = random.Random(world.config.seed ^ 0x54524146)

    def populate(self, px, pz):
        """Fill visible roads during loading, before the first gameplay frame.

        Later recycling still happens outside fog, so cars never appear nearby.
        """
        old_fog=self.fog_distance
        old_recycle=self.recycle_distance
        self.fog_distance=min(old_fog,85.)
        self.recycle_distance=min(old_recycle,300.)
        try:
            for slot in range(self.count):
                car=self._spawn(slot,px,pz)
                if car:self.vehicles.append(car)
        finally:
            self.fog_distance=old_fog
            self.recycle_distance=old_recycle

    def _place(self, car):
        center = car.lane*self.world.config.road_spacing + 28*math.sin(car.along*.003)
        # Right-hand lanes, in both horizontal road families.
        offset = -car.direction*2.8 if car.axis == 0 else car.direction*2.8
        cross = center + offset
        car.x, car.z = (cross, car.along) if car.axis == 0 else (car.along, cross)
        tangent = .084*math.cos(car.along*.003)
        dx, dz = (tangent, 1.) if car.axis == 0 else (1., tangent)
        car.yaw = math.atan2(-dx*car.direction, -dz*car.direction)
        height = getattr(self.world, "surface_height", self.world.height)
        car.y = height(car.x, car.z) + .055

    def _spawn(self, slot, px, pz):
        palette = ((.39,.47,.43),(.72,.62,.40),(.42,.51,.58),(.55,.30,.22),(.73,.72,.64))
        for _ in range(48):
            axis = self._rng.randrange(2)
            cross, along = (px,pz) if axis == 0 else (pz,px)
            lane = round((cross-28*math.sin(along*.003))/self.world.config.road_spacing)
            radius = self._rng.uniform(max(65., self.fog_distance*1.02), self.recycle_distance*.95)
            cross_distance = lane*self.world.config.road_spacing-cross
            offset = math.sqrt(max(65.**2,radius**2-cross_distance**2))
            car = TrafficVehicle(axis=axis,lane=lane,along=along+self._rng.choice((-1,1))*offset,
                                 direction=self._rng.choice((-1,1)),cruise=self._rng.uniform(10,16),
                                 slot=slot,color=palette[slot%len(palette)])
            self._place(car)
            if math.hypot(car.x-px,car.z-pz) < 55:
                continue
            if all(math.hypot(car.x-other.x,car.z-other.z)>24 for other in self.vehicles):
                car.speed=car.cruise
                return car
        return None

    def update(self, dt, player_x, player_z, player_speed=0):
        if not all(math.isfinite(v) for v in (dt,player_x,player_z,player_speed)):
            return
        self._accumulator += max(0.,min(.25,dt))
        while self._accumulator+1e-10 >= 1/30:
            self._accumulator -= 1/30
            self._step(player_x,player_z)

    def _step(self, px, pz):
        self.vehicles[:] = [v for v in self.vehicles
                            if math.hypot(v.x-px,v.z-pz)<=self.recycle_distance]
        occupied={v.slot for v in self.vehicles}
        for slot in range(self.count):
            if slot not in occupied:
                car=self._spawn(slot,px,pz)
                if car is not None:
                    self.vehicles.append(car)
        for car in self.vehicles:
            fx,fz=-math.sin(car.yaw),-math.cos(car.yaw)
            target=car.cruise
            obstacles=[(px,pz)] + [(v.x,v.z) for v in self.vehicles if v is not car]
            for ox,oz in obstacles:
                dx,dz=ox-car.x,oz-car.z
                ahead=dx*fx+dz*fz
                sideways=abs(dx*fz-dz*fx)
                if -3 < ahead < 24 and sideways < 3.2:
                    target=min(target,max(0.,(ahead-9)*.65))
            car.braking=target < car.speed-.1
            car.speed += max(-7/30,min(2.5/30,target-car.speed))
            next_along=car.along+car.direction*car.speed/30
            old_along=car.along
            car.along=next_along
            self._place(car)
            # Hard final guard prevents overlap when a player teleports in front
            # of traffic or intersecting streams meet within one fixed tick.
            if any(math.hypot(car.x-ox,car.z-oz)<5.0 for ox,oz in obstacles):
                car.along=old_along
                car.speed=0
                car.braking=True
                self._place(car)

    def colliders(self, player_x, player_z):
        return [(v.x,v.z,2.15) for v in self.vehicles
                if (v.x-player_x)**2+(v.z-player_z)**2 < 45**2]
