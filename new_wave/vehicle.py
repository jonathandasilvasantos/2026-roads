"""Fixed-step, SI-unit arcade touring physics, independent of the renderer.

XZ is the horizontal plane. Yaw zero faces -Z and positive yaw turns left.
The tire model caps lateral acceleration; handbraking deliberately releases grip.
This is a grounded chassis model, not a rigid-body or airborne simulator.
"""
from dataclasses import dataclass, fields, replace
import math


def clamp(value, low, high):
    return max(low, min(high, value))


@dataclass
class VehicleConfig:
    mass: float = 1250.0
    center_of_gravity: float = 0.48
    engine_force: float = 7800.0
    braking_force: float = 14500.0
    max_speed: float = 52.0
    reverse_speed: float = 12.0
    steering_angle: float = 0.56
    steering_curve: float = 0.045
    steering_response: float = 5.5
    suspension_stiffness: float = 42000.0
    suspension_damping: float = 12500.0
    suspension_travel: float = 0.24
    tire_grip: float = 1.10
    drag: float = 0.44
    rolling_resistance: float = 165.0
    wheelbase: float = 2.65
    track_width: float = 1.58
    wheel_radius: float = 0.34
    ride_height: float = 0.64
    collision_radius: float = 1.05
    collision_restitution: float = 0.10
    fixed_dt: float = 1.0 / 120.0
    max_frame_dt: float = 0.25

    def __post_init__(self):
        positive = ('mass', 'max_speed', 'reverse_speed', 'wheelbase',
                    'track_width', 'wheel_radius', 'fixed_dt', 'max_frame_dt')
        for name in positive:
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f'{name} must be finite and positive')
        for item in fields(self):
            if not math.isfinite(getattr(self, item.name)) or getattr(self, item.name) < 0:
                raise ValueError(f'{item.name} must be finite and nonnegative')
        if self.fixed_dt > 1 / 30:
            raise ValueError('fixed_dt must be at most 1/30 second')


@dataclass
class Input:
    throttle: float = 0.0  # signed: forward +1, reverse -1
    brake: float = 0.0
    steer: float = 0.0  # left +1
    handbrake: bool = False
    recover: bool = False


@dataclass
class VehicleState:
    x: float = 0.0
    y: float = 0.64
    z: float = 0.0
    yaw: float = 0.0
    vx: float = 0.0
    vz: float = 0.0
    speed: float = 0.0  # signed longitudinal meters/second
    steer: float = 0.0  # actual front wheel angle, radians
    pitch: float = 0.0
    roll: float = 0.0
    wheel_spin: float = 0.0
    vy: float = 0.0
    yaw_rate: float = 0.0
    collision_impulse: float = 0.0
    distance: float = 0.0


class Vehicle:
    def __init__(self, config=None, x=0.0, z=0.0, yaw=0.0):
        self.config = config or VehicleConfig()
        self.state = VehicleState(x=x, z=z, yaw=yaw, y=self.config.ride_height)
        self.previous = replace(self.state)
        self.accumulator = 0.0
        self.simulation_time = 0.0
        self.dropped_time = 0.0
        self._last_safe = (x, z)
        self._initialized = False

    def recover(self, ground_height):
        s, c = self.state, self.config
        if not all(math.isfinite(v) for v in (s.x, s.z)):
            s.x, s.z = self._last_safe
        height = ground_height(s.x, s.z)
        if not math.isfinite(height):
            s.x, s.z = self._last_safe
            height = ground_height(s.x, s.z)
        s.y = (height if math.isfinite(height) else 0.0) + c.ride_height
        s.vx = s.vz = s.vy = s.speed = s.pitch = s.roll = s.yaw_rate = 0.0
        s.yaw = s.yaw if math.isfinite(s.yaw) else 0.0
        self.previous = replace(s)
        self._initialized = True

    def rebase(self, dx, dz):
        """Shift both interpolation endpoints when the world origin moves."""
        for state in (self.state, self.previous):
            state.x -= dx
            state.z -= dz
        self._last_safe = (self._last_safe[0] - dx, self._last_safe[1] - dz)

    def advance(self, frame_dt, control, ground_height, colliders=()):
        if not math.isfinite(frame_dt) or frame_dt < 0:
            raise ValueError('frame_dt must be finite and nonnegative')
        c = self.config
        self.dropped_time += max(0.0, frame_dt - c.max_frame_dt)
        self.accumulator += min(frame_dt, c.max_frame_dt)
        # Materialize a generator once; every fixed step needs the same obstacles.
        colliders = tuple(colliders)
        while self.accumulator + 1e-12 >= c.fixed_dt:
            self.step(c.fixed_dt, control, ground_height, colliders)
            self.accumulator = max(0.0, self.accumulator - c.fixed_dt)
        return self.accumulator / c.fixed_dt

    def interpolate(self, alpha):
        alpha = clamp(alpha, 0.0, 1.0)
        a, b = self.previous, self.state
        return VehicleState(**{f.name: getattr(a, f.name) * (1-alpha)
                               + getattr(b, f.name) * alpha for f in fields(a)})

    def step(self, dt, control, ground_height, colliders=()):
        if not math.isfinite(dt) or not 0 < dt <= 1 / 30 + 1e-12:
            raise ValueError('step requires 0 < dt <= 1/30; use advance for frames')
        s, c = self.state, self.config
        if control.recover or not self._initialized or not all(
                math.isfinite(getattr(s, f.name)) for f in fields(s)):
            self.recover(ground_height)
        self.previous = replace(s)
        s.collision_impulse *= math.exp(-8 * dt)
        fx, fz = -math.sin(s.yaw), -math.cos(s.yaw)
        rx, rz = math.cos(s.yaw), -math.sin(s.yaw)
        longitudinal = s.vx * fx + s.vz * fz
        lateral = s.vx * rx + s.vz * rz
        throttle = clamp(control.throttle, -1, 1)
        brake = clamp(control.brake, 0, 1)
        # Opposite pedal first brakes, then selects reverse after stopping.
        if throttle * longitudinal < -0.15:
            brake = max(brake, abs(throttle))
            throttle = 0.0
        limit = c.max_speed if throttle >= 0 else c.reverse_speed
        engine = throttle * c.engine_force * max(0, 1 - (abs(longitudinal) / limit) ** 2)
        resist = c.rolling_resistance + c.drag * longitudinal * longitudinal
        acceleration = engine / c.mass
        new_long = longitudinal + acceleration * dt
        deceleration = (brake * c.braking_force + resist) / c.mass * dt
        if control.handbrake:
            deceleration += c.braking_force * 0.28 / c.mass * dt
        new_long = math.copysign(max(0, abs(new_long) - deceleration), new_long)
        new_long = clamp(new_long, -c.reverse_speed, c.max_speed)

        desired_steer = clamp(control.steer, -1, 1) * c.steering_angle / (
            1 + c.steering_curve * abs(longitudinal))
        s.steer += (desired_steer - s.steer) * (1 - math.exp(-c.steering_response * dt))
        grip = c.tire_grip * (0.32 if control.handbrake else 1.0)
        wanted_yaw = new_long / c.wheelbase * math.tan(s.steer)
        max_yaw = c.tire_grip * 9.81 / max(abs(new_long), 2.0)
        wanted_yaw = clamp(wanted_yaw, -max_yaw, max_yaw)
        s.yaw_rate += (wanted_yaw - s.yaw_rate) * (1 - math.exp(-7.0 * dt))
        yaw_delta = s.yaw_rate * dt
        s.yaw += yaw_delta
        # Velocity retains inertia as the body rotates. Tires progressively
        # remove side slip, bounded by their available lateral acceleration.
        lateral, new_long = (lateral * math.cos(yaw_delta) + new_long * math.sin(yaw_delta),
                             new_long * math.cos(yaw_delta) - lateral * math.sin(yaw_delta))
        correction = abs(lateral) * (1 - math.exp(-(2.0 if control.handbrake else 12.0) * dt))
        lateral = math.copysign(max(0, abs(lateral) - min(correction, grip * 9.81 * dt)), lateral)
        fx, fz = -math.sin(s.yaw), -math.cos(s.yaw)
        rx, rz = math.cos(s.yaw), -math.sin(s.yaw)
        s.vx, s.vz = fx * new_long + rx * lateral, fz * new_long + rz * lateral
        next_x, next_z = s.x + s.vx * dt, s.z + s.vz * dt
        next_x, next_z = self._collide(next_x, next_z, colliders)
        s.distance += math.hypot(next_x - s.x, next_z - s.z)
        s.x, s.z = next_x, next_z
        center = ground_height(s.x, s.z)
        if not math.isfinite(center):
            self.recover(ground_height)
            return
        self._last_safe = (s.x, s.z)
        front = ground_height(s.x + fx * c.wheelbase / 2, s.z + fz * c.wheelbase / 2)
        rear = ground_height(s.x - fx * c.wheelbase / 2, s.z - fz * c.wheelbase / 2)
        right = ground_height(s.x + rx * c.track_width / 2, s.z + rz * c.track_width / 2)
        left = ground_height(s.x - rx * c.track_width / 2, s.z - rz * c.track_width / 2)
        if not all(math.isfinite(h) for h in (front, rear, right, left)):
            front = rear = right = left = center
        slope = math.atan2(front - rear, c.wheelbase)
        slope_accel = -9.81 * math.sin(slope)
        if abs(new_long) > 0.15 or abs(slope_accel) > 0.8:
            s.vx += fx * slope_accel * dt
            s.vz += fz * slope_accel * dt
        target_y = center + c.ride_height
        s.vy += ((target_y - s.y) * c.suspension_stiffness - s.vy * c.suspension_damping) / c.mass * dt
        s.y += s.vy * dt
        lower, upper = target_y - c.suspension_travel, target_y + c.suspension_travel
        if not lower <= s.y <= upper:
            s.y = clamp(s.y, lower, upper)
            s.vy = 0.0
        longitudinal_accel = (new_long - longitudinal) / dt
        pitch_target = slope - clamp(longitudinal_accel * c.center_of_gravity / 100, -0.10, 0.10)
        roll_target = math.atan2(right - left, c.track_width) + clamp(
            new_long * s.yaw_rate * c.center_of_gravity / 70, -0.13, 0.13)
        blend = 1 - math.exp(-7 * dt)
        s.pitch += (pitch_target - s.pitch) * blend
        s.roll += (roll_target - s.roll) * blend
        s.speed = s.vx * fx + s.vz * fz
        s.wheel_spin += s.speed / c.wheel_radius * dt
        self.simulation_time += dt

    def _collide(self, nx, nz, colliders):
        """Swept circles prevent tunneling; inexpensive proxies stay local."""
        s, c = self.state, self.config
        for cx, cz, radius in colliders:
            radius += c.collision_radius
            dx, dz = nx - s.x, nz - s.z
            ox, oz = s.x - cx, s.z - cz
            a = dx * dx + dz * dz
            b = 2 * (ox * dx + oz * dz)
            q = ox * ox + oz * oz - radius * radius
            hit = False
            if q >= 0 and a > 1e-16:
                disc = b * b - 4 * a * q
                if disc >= 0:
                    t = (-b - math.sqrt(disc)) / (2 * a)
                    if 0 <= t <= 1:
                        nx, nz = s.x + dx * max(0, t - 1e-5), s.z + dz * max(0, t - 1e-5)
                        hit = True
            distance = math.hypot(nx - cx, nz - cz)
            if distance < radius or hit:
                if distance > 1e-9:
                    normal_x, normal_z = (nx - cx) / distance, (nz - cz) / distance
                else:
                    normal_x, normal_z = 1.0, 0.0
                nx, nz = cx + normal_x * (radius + 0.001), cz + normal_z * (radius + 0.001)
                inward = s.vx * normal_x + s.vz * normal_z
                if inward < 0:
                    impulse = -(1 + c.collision_restitution) * inward
                    s.vx += normal_x * impulse
                    s.vz += normal_z * impulse
                    s.collision_impulse = max(s.collision_impulse, abs(inward))
        return nx, nz
