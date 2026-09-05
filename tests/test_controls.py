import unittest
import glfw
from new_wave.game import meta_key_actions, parser


class ControlTests(unittest.TestCase):
    def test_escape_always_exits(self):
        self.assertEqual(meta_key_actions({glfw.KEY_ESCAPE},False),(False,True))
        self.assertEqual(meta_key_actions({glfw.KEY_ESCAPE},True),(True,True))

    def test_p_owns_pause(self):
        self.assertEqual(meta_key_actions({glfw.KEY_P},False),(True,False))
        self.assertEqual(meta_key_actions({glfw.KEY_P},True),(False,False))

    def test_display_flags(self):
        self.assertFalse(parser().parse_args([]).fullscreen)
        self.assertTrue(parser().parse_args(['--fullscreen']).fullscreen)
        self.assertFalse(parser().parse_args(['--windowed']).fullscreen)


if __name__=='__main__':
    unittest.main()
