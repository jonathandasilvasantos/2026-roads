import unittest
import math
from new_wave.world import World,WorldConfig
from new_wave.traffic import Traffic


class PopulationTests(unittest.TestCase):
    def test_vectorized_ground_matches_collision_surface(self):
        import numpy as np
        world=World()
        try:
            x=np.array([-128000023.,-189.3,57.1,230.])
            z=np.array([128000078.,72.1,-63.2,-170.])
            for px,py,pz in zip(x,world.ground_heights(x,z),z):
                if world.road_distance(px,pz)>7:
                    self.assertAlmostEqual(py,world.surface_height(px,pz),places=6)
        finally:world.close()

    def test_hamlet_ownership_and_walkway_clearance(self):
        world=World(WorldConfig(density=0))
        try:
            points=[]
            for key in ((-1,-1),(0,-1),(-1,0),(0,0)):
                a=world.generate_chunk(key);b=world.generate_chunk(key)
                self.assertEqual(a.props,b.props)
                for prop in a.props:
                    self.assertGreaterEqual(prop.x,a.origin[0])
                    self.assertLess(prop.x,a.origin[0]+128)
                    if prop.kind=='walker0':
                        for travel in (-6,0,6):
                            self.assertGreater(world.road_distance(prop.x,prop.z+travel),8)
                        points.append((prop.x,prop.z))
            self.assertTrue(points)
            self.assertEqual(len(points),len(set(points)))
        finally:world.close()

    def test_initial_traffic_is_visible_and_clear(self):
        world=World()
        try:
            a,b=Traffic(world,count=24),Traffic(world,count=24)
            a.populate(0,-35);b.populate(0,-35)
            self.assertEqual(a.vehicles,b.vehicles)
            self.assertGreater(len(a.vehicles),8)
            self.assertTrue(any(55<math.hypot(v.x,v.z+35)<180 for v in a.vehicles))
            for car in a.vehicles:
                self.assertGreater(math.hypot(car.x,car.z+35),55)
            a.update(.25,0,-35)
            self.assertLessEqual(len(a.vehicles),24)
        finally:world.close()
