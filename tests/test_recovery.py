import math
from types import SimpleNamespace
import unittest
from new_wave.recovery import clear_position


class RecoveryTests(unittest.TestCase):
    def test_recovery_finds_clear_place_even_far_from_roads(self):
        obstacle=SimpleNamespace(x=0,z=0,radius=4)
        world=SimpleNamespace(surface_height=lambda x,z:0,
            nearby_colliders=lambda x,z,r:[obstacle],road_distance=lambda x,z:100)
        x,z=clear_position(world,0,0)
        self.assertGreater(math.hypot(x,z),5.7)

    def test_valid_spawn_preserves_requested_position(self):
        world=SimpleNamespace(surface_height=lambda x,z:0,
            nearby_colliders=lambda x,z,r:[],road_distance=lambda x,z:100)
        self.assertEqual(clear_position(world,-20,-30,prefer_road=False),(-20,-30))
