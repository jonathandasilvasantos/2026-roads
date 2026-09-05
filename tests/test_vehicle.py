import math
import unittest

from new_wave.vehicle import Input, Vehicle, VehicleConfig


def flat(x, z):
    return 0.0


class VehicleTests(unittest.TestCase):
    def drive(self, car, seconds, control, fps=60, terrain=flat, colliders=()):
        for _ in range(round(seconds * fps)):
            car.advance(1 / fps, control, terrain, colliders)

    def test_render_rate_independence(self):
        states = []
        for fps in (30, 60, 144):
            car = Vehicle()
            self.drive(car, 12, Input(throttle=1, steer=0.3), fps)
            states.append(car.state)
        for state in states[1:]:
            for field in ('x', 'z', 'vx', 'vz', 'yaw', 'y'):
                self.assertAlmostEqual(getattr(state, field), getattr(states[0], field), places=8)

    def test_braking_reverse_and_negative_coordinates(self):
        car = Vehicle(x=-100, z=-100)
        self.drive(car, 5, Input(throttle=1))
        self.assertGreater(car.state.speed, 20)
        self.assertLess(car.state.z, -100)
        self.drive(car, 4, Input(brake=1))
        self.assertAlmostEqual(car.state.speed, 0, places=6)
        self.drive(car, 4, Input(throttle=-1))
        self.assertLess(car.state.speed, -8)
        self.assertGreaterEqual(car.state.speed, -car.config.reverse_speed)

    def test_steering_is_progressive_and_speed_sensitive(self):
        slow, fast = Vehicle(), Vehicle()
        self.drive(fast, 8, Input(throttle=1))
        slow.step(1/120, Input(steer=1), flat)
        fast.step(1/120, Input(steer=1), flat)
        self.assertGreater(slow.state.steer, fast.state.steer)
        self.assertLess(slow.state.steer, slow.config.steering_angle / 5)

    def test_swept_collision_does_not_tunnel(self):
        car = Vehicle(config=VehicleConfig(max_speed=200))
        car.recover(flat)
        car.state.vz = -180
        car.step(1/30, Input(), flat, [(0, -3, 0.1)])
        self.assertGreater(car.state.z, -3)
        self.assertGreaterEqual(car.state.vz, 0)
        self.assertGreater(car.state.collision_impulse, 100)

    def test_spawn_overlap_resolved(self):
        car = Vehicle()
        car.step(1/120, Input(), flat, [(0, 0, 2)])
        self.assertGreaterEqual(math.hypot(car.state.x, car.state.z), 3.05)

    def test_handbrake_releases_grip_without_unbounded_speed(self):
        slip = []
        for handbrake in (False, True):
            car = Vehicle()
            self.drive(car, 5, Input(throttle=1))
            self.drive(car, 1, Input(throttle=1, steer=0.8, handbrake=handbrake))
            s = car.state
            slip.append(abs(s.vx * math.cos(s.yaw) - s.vz * math.sin(s.yaw)))
            self.assertLess(math.hypot(s.vx, s.vz), car.config.max_speed)
        self.assertGreater(slip[1], slip[0] * 3)

    def test_terrain_and_recovery_remain_finite(self):
        def terrain(x, z):
            return 2 * math.sin(z / 25) + math.cos(x / 18)
        car = Vehicle()
        self.drive(car, 30, Input(throttle=1, steer=0.5), terrain=terrain)
        self.assertLessEqual(abs(car.state.y - terrain(car.state.x, car.state.z)
                                 - car.config.ride_height), car.config.suspension_travel + 1e-9)
        car.state.x = float('nan')
        car.state.roll = 4
        car.step(1/120, Input(recover=True), terrain)
        self.assertTrue(math.isfinite(car.state.x))
        self.assertLess(abs(car.state.roll), 0.1)
        self.assertLess(abs(car.state.speed), 0.01)

    def test_interpolation_and_rebase(self):
        car = Vehicle(x=1e6, z=-1e6)
        self.drive(car, 1, Input(throttle=1))
        midpoint = car.interpolate(0.5)
        self.assertAlmostEqual(midpoint.z, (car.previous.z + car.state.z) / 2)
        car.rebase(1e6, -1e6)
        self.assertLess(abs(car.state.z), 10)
        self.assertLess(abs(car.previous.z), 10)

    def test_stall_is_bounded_and_reported(self):
        car = Vehicle()
        car.advance(10, Input(throttle=1), flat)
        self.assertAlmostEqual(car.simulation_time, 0.25)
        self.assertAlmostEqual(car.dropped_time, 9.75)
        self.assertLess(car.accumulator, car.config.fixed_dt)

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            VehicleConfig(mass=0)
        with self.assertRaises(ValueError):
            VehicleConfig(fixed_dt=1)


if __name__ == '__main__':
    unittest.main()
