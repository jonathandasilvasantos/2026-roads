"""Adaptive control layer for the original procedural Rhodes/string score."""
from dataclasses import dataclass
import math


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class MusicProfile:
    gain: float
    brightness: int
    reverb: int
    chorus: int


def music_profile(speed_mps, daylight, rain, biome, base_gain=.22):
    """Map gameplay state to restrained GM controls without changing harmony."""
    motion=_clamp(abs(speed_mps)/45.0)
    day=_clamp(daylight)
    wet=_clamp(rain)
    biome_tone={"woodland":-7,"drylands":5,"meadow":0}.get(biome,0)
    # Keep the score present at rest, then open it gently while travelling.
    base_gain=_clamp(base_gain,0,.8)
    gain=base_gain*(.72+.28*motion)*(1-.10*wet)
    brightness=round(52+35*motion+8*day+biome_tone-8*wet)
    reverb=round(54+23*wet+12*(1-day)+(5 if biome=="woodland" else 0))
    chorus=round(28+12*(1-day)+8*wet)
    return MusicProfile(gain,int(round(_clamp(brightness,0,127))),
                        int(round(_clamp(reverb,0,127))),
                        int(round(_clamp(chorus,0,127))))


class AdaptiveMusic:
    """Smoothly adapts the existing composer through standard MIDI controls."""
    def __init__(self, player, base_gain=.22):
        self.player=player
        self.base_gain=_clamp(base_gain,0,.8)
        self.current=music_profile(0,1,0,"meadow",self.base_gain)
        self.elapsed=1.0
        self.running=False

    def start(self):
        self.player.start()
        self.running=True

    def update(self,dt,speed_mps,daylight,rain,biome):
        if not self.running:return
        self.elapsed+=max(0,min(float(dt),.25))
        target=music_profile(speed_mps,daylight,rain,biome,self.base_gain)
        a=1-math.exp(-max(0,float(dt))/2.5)
        gain=self.current.gain+(target.gain-self.current.gain)*a
        bright=round(self.current.brightness+(target.brightness-self.current.brightness)*a)
        reverb=round(self.current.reverb+(target.reverb-self.current.reverb)*a)
        chorus=round(self.current.chorus+(target.chorus-self.current.chorus)*a)
        self.current=MusicProfile(gain,bright,reverb,chorus)
        if self.elapsed<.20:return
        self.elapsed=0
        try:
            self.player.fs.set_gain(gain)
            for channel in (self.player.CH_MELODY,self.player.CH_CP,self.player.CH_BASS):
                self.player.fs.cc(channel,74,bright)   # timbral brightness
                self.player.fs.cc(channel,91,reverb)  # reverb send
                self.player.fs.cc(channel,93,chorus)  # chorus send
        except Exception:
            # Audio device loss must never stop simulation/rendering.
            pass

    def stop(self):
        if self.running:self.player.stop()
        self.running=False
