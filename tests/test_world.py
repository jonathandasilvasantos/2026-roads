"""CPU-only repeatable acceptance checks for the free-roam world."""
import unittest
import time
import numpy as np
from new_wave.world import World, WorldConfig


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.world = World(WorldConfig(radius=1))

    def tearDown(self):
        self.world.close()

    def test_deterministic_positive_negative(self):
        for key in [(0, 0), (-1, -2), (5, -7)]:
            a, b = self.world.generate_chunk(key), self.world.generate_chunk(key)
            np.testing.assert_array_equal(a.vertices, b.vertices)
            self.assertEqual(a.props, b.props)

    def test_shared_terrain_edges(self):
        n = self.world.config.resolution
        for key in [(0, 0), (-2, -1), (100000, -100000)]:
            a = self.world.generate_chunk(key).vertices[:n*n*6]
            b = self.world.generate_chunk((key[0]+1, key[1])).vertices[:n*n*6]
            edge_a = np.unique(a[a[:, 0] == 128][:, 1:], axis=0)
            edge_b = np.unique(b[b[:, 0] == 0][:, 1:], axis=0)
            np.testing.assert_allclose(edge_a, edge_b, atol=1e-6)

    def test_scalar_vector_height_and_valid_spawn(self):
        for x, z in [(0, 0), (-128.1, 500), (1e8, -1e8)]:
            self.assertAlmostEqual(self.world.height(x, z), float(self.world._height(np.array(x), np.array(z))), places=8)
            self.assertTrue(np.isfinite(self.world.height(x, z)))
        self.assertEqual(self.world.height(0, 0), 0)
        self.assertEqual(self.world.road_distance(0, 0), 0)

    def test_surface_height_matches_rendered_triangles(self):
        rng=np.random.default_rng(19)
        n=self.world.config.resolution
        for key in [(0,0),(-2,-3),(200,-200)]:
            chunk=self.world.generate_chunk(key)
            triangles=chunk.vertices[:n*n*6,:3].reshape(-1,3,3)
            for _ in range(100):
                tri=triangles[rng.integers(len(triangles))].astype(np.float64)
                weights=rng.dirichlet([1,1,1])
                point=weights@tri
                x,z=point[0]+chunk.origin[0],point[2]+chunk.origin[1]
                if self.world.road_distance(x,z)>8:
                    self.assertAlmostEqual(self.world.surface_height(x,z),point[1],delta=1e-5)
        self.assertAlmostEqual(self.world.surface_height(0,0),.045)

    def test_road_network_flat_and_connected(self):
        for z in range(-3000, 3000, 13):
            x = 384 + 28 * np.sin(z * .003)
            self.assertLess(self.world.road_distance(x, z), 1e-9)
            self.assertLess(abs(self.world.height(x+.5, z) - self.world.height(x-.5, z)), .003)

    def test_local_coordinates_at_long_distance(self):
        chunk = self.world.generate_chunk((1000000, -1000000))
        self.assertGreaterEqual(float(chunk.vertices[:, 0].min()), 0)
        self.assertLessEqual(float(chunk.vertices[:, 0].max()), 128)
        self.assertTrue(np.isfinite(chunk.vertices).all())

    def test_props_stay_clear_of_roads(self):
        for key in [(0,0), (-2,3), (4,-5)]:
            for p in self.world.generate_chunk(key).props:
                self.assertGreater(self.world.road_distance(p.x, p.z) - p.radius, 8)

    def test_diversity_landmarks_and_colliders(self):
        kinds=set()
        for key in [(i,j) for i in range(-4,5) for j in range(-4,5)]:
            chunk=self.world.generate_chunk(key)
            kinds.update(p.kind for p in chunk.props)
            solids=[p for p in chunk.props if p.radius > 0]
            for i,p in enumerate(solids):
                for q in solids[i+1:]:
                    self.assertGreaterEqual((p.x-q.x)**2+(p.z-q.z)**2,(p.radius+q.radius)**2)
        self.assertTrue({"tower","pine","broadleaf","grass","building","rock","post"}.issubset(kinds), kinds)
        chunk=self.world.generate_chunk((0,0))
        self.world.chunks[(0,0)]=chunk
        self.assertTrue(all(p.kind != "grass" for p in self.world.nearby_colliders(64,64,100)))

    def test_landforms_remain_bounded_and_roads_flat(self):
        x,z=np.meshgrid(np.arange(-2000,2000,25),np.arange(-2000,2000,25))
        heights=self.world._height(x,z)
        self.assertGreater(heights.max(),40)
        self.assertLess(heights.max(),65)
        self.assertGreater(heights.min(),-5)

    def test_bounded_lifecycle_after_teleports(self):
        for x, z in [(0,0), (500,-500), (-100000,100000)]:
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                self.world.update(x,z)
                self.world.completed()
                self.assertLessEqual(len(self.world.chunks), 9)
                self.assertLessEqual(self.world.pending_count, 4)
                if len(self.world.chunks) == 9:
                    break
                time.sleep(.005)
            self.assertEqual(len(self.world.chunks), 9)
            self.assertTrue(set(self.world.chunks).issubset(self.world._wanted))
        self.assertTrue(self.world.drain_evicted())


if __name__ == '__main__':
    unittest.main()
