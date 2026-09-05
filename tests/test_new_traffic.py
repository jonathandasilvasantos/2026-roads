import math
import unittest
from types import SimpleNamespace

from new_wave.traffic import Traffic


class WorldStub:
    config=SimpleNamespace(seed=42,chunk_size=128,radius=4,road_spacing=384)
    def height(self,x,z):
        return math.sin(x*.001)*math.sin(z*.001)


class TrafficTests(unittest.TestCase):
    def snapshot(self,t):
        return [(v.x,v.z,v.speed,v.slot) for v in t.vehicles]

    def test_determinism_and_frame_rate(self):
        a,b=Traffic(WorldStub()),Traffic(WorldStub())
        for _ in range(300): a.update(1/30,0,0,0)
        for _ in range(1200): b.update(1/120,0,0,0)
        self.assertEqual(self.snapshot(a),self.snapshot(b))

    def test_bounded_negative_and_long_travel(self):
        traffic=Traffic(WorldStub())
        for coordinate in (0,-1000,1000,-1e8,1e8):
            for _ in range(60): traffic.update(1/30,coordinate,coordinate,20)
            self.assertLessEqual(len(traffic.vehicles),12)
            self.assertGreater(len(traffic.vehicles),0)
            for car in traffic.vehicles:
                self.assertTrue(all(math.isfinite(v) for v in (car.x,car.y,car.z,car.yaw)))
                self.assertGreater(math.hypot(car.x-coordinate,car.z-coordinate),50)
                for other in traffic.vehicles:
                    if car is not other:
                        self.assertGreater(math.hypot(car.x-other.x,car.z-other.z),4.9)

    def test_road_alignment_and_player_yield(self):
        t=Traffic(WorldStub(),count=1)
        t.update(1/30,0,0)
        car=t.vehicles[0]
        center=car.lane*384+28*math.sin(car.along*.003)
        self.assertAlmostEqual(abs((car.x if car.axis==0 else car.z)-center),2.8)
        px,pz=car.x-12*math.sin(car.yaw),car.z-12*math.cos(car.yaw)
        for _ in range(120): t.update(1/30,px,pz)
        self.assertLess(car.speed,.1)
        self.assertGreaterEqual(math.hypot(car.x-px,car.z-pz),5)
        self.assertEqual(len(t.colliders(px,pz)),1)

    def test_disabled(self):
        t=Traffic(WorldStub(),0)
        t.update(.1,0,0)
        self.assertEqual(t.vehicles,[])


if __name__ == "__main__": unittest.main()
