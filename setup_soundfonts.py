"""Download the GeneralUser-GS SoundFont for the procedural ensemble.

Run once after cloning to enable the generative music layer:

    ./env/bin/python setup_soundfonts.py

The 32 MB SF2 is published by S. Christian Collins on GitHub
(mrbumpy409/GeneralUser-GS, public-domain / CC0) and contains the two
programs we use:
  - Program 4  — Electric Piano 1 ("Tine" Rhodes) → melody voice
  - Program 48 — String Ensemble 1 → counterpoint + bass pedal

No extraction needed — the download is a plain .sf2 file.
"""
import os
import sys
import urllib.request

URL = (
    "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/"
    "main/GeneralUser-GS.sf2"
)
OUT_DIR = "soundfonts"
SF2_NAME = "GeneralUser-GS.sf2"


def main():
    dest = os.path.join(OUT_DIR, SF2_NAME)
    if os.path.exists(dest):
        print(f"{dest} already present — nothing to do.")
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Downloading {URL}")
    urllib.request.urlretrieve(URL, dest)
    size_mb = os.path.getsize(dest) / (1024 * 1024)
    print(f"Ready: {dest} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
