"""Capture reproducible gameplay viewpoints; concepts are stored separately."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
SCENES={
    'realism-after': ['--camera','chase'],
    'realism-road': ['--camera','road'],
    'realism-village': ['--camera','wide','--x','0','--z','-90'],
    'realism-night': ['--camera','chase','--time','22','--weather','rain'],
    'driving-after': ['--camera','chase'],
    'car-close-after': ['--camera','close'],
    'road-level-after': ['--camera','road'],
    'wide-meadow-after': ['--camera','wide','--x','128','--z','-128'],
    'dense-woodland-after': ['--camera','wide','--x','-1400','--z','600'],
    'sparse-drylands-after': ['--camera','wide','--x','-900','--z','-1300'],
    'intersection-after': ['--camera','wide','--x','3','--z','20','--yaw','180'],
    'chunk-border-after': ['--camera','chase','--x','-7','--z','-128'],
    'long-coordinate-after': ['--camera','wide','--x','128000003','--z','-128000035'],
}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--backend',choices=('metal','opengl'),default='metal')
    p.add_argument('--scene',choices=SCENES)
    args=p.parse_args()
    for name,options in SCENES.items():
        if args.scene and name!=args.scene:continue
        target=ROOT/'development/reference'/f'{name}.png'
        command=[sys.executable,str(ROOT/'drive.py'),'--backend',args.backend,
                 '--frames','90','--warmup','1','--no-audio','--screenshot',str(target),*options]
        result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
        if result.returncode:
            print(result.stdout+result.stderr,file=sys.stderr)
            raise SystemExit(result.returncode)
        print(f'Captured {target.relative_to(ROOT)}',flush=True)


if __name__=='__main__':main()
