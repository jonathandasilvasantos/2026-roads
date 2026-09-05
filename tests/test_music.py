import unittest
from new_wave.music import AdaptiveMusic,music_profile


class FakeSynth:
    def __init__(self):self.gains=[];self.controls=[]
    def set_gain(self,value):self.gains.append(value)
    def cc(self,*args):self.controls.append(args)


class FakePlayer:
    CH_MELODY=0;CH_CP=1;CH_BASS=2
    def __init__(self):self.fs=FakeSynth();self.started=self.stopped=False
    def start(self):self.started=True
    def stop(self):self.stopped=True


class MusicTests(unittest.TestCase):
    def test_profile_responds_to_world_without_clipping(self):
        calm=music_profile(0,1,0,'meadow')
        drive=music_profile(45,.1,1,'woodland')
        self.assertGreater(drive.gain,calm.gain)
        self.assertGreater(drive.reverb,calm.reverb)
        for profile in (calm,drive,music_profile(1e9,-1,8,'drylands')):
            self.assertTrue(0<profile.gain<1)
            self.assertTrue(all(0<=v<=127 for v in (profile.brightness,profile.reverb,profile.chorus)))

    def test_updates_are_smoothed_throttled_and_stoppable(self):
        player=FakePlayer();music=AdaptiveMusic(player)
        music.start()
        for _ in range(20):music.update(.05,40,.2,.8,'woodland')
        self.assertTrue(player.started)
        self.assertGreaterEqual(len(player.fs.gains),4)
        self.assertEqual(len(player.fs.controls),len(player.fs.gains)*3*3)
        self.assertLess(player.fs.gains[0],music_profile(40,.2,.8,'woodland').gain)
        music.stop();self.assertTrue(player.stopped)


if __name__=='__main__':unittest.main()
