"""Download the CC0 Upright Piano KW SoundFont from the FreePats project.

Run once after cloning to enable the generative piano layer:

    ./env/bin/python setup_soundfonts.py

Requires the `7z` extractor on PATH (e.g. `brew install p7zip`).
"""
import os
import shutil
import subprocess
import sys
import urllib.request

URL = (
    "https://freepats.zenvoid.org/Piano/UprightPianoKW/"
    "UprightPianoKW-SF2-20220221.7z"
)
OUT_DIR = "soundfonts"
SF2_NAME = "UprightPianoKW.sf2"


def main():
    dest = os.path.join(OUT_DIR, SF2_NAME)
    if os.path.exists(dest):
        print(f"{dest} already present — nothing to do.")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    archive = os.path.join(OUT_DIR, "UprightPianoKW.7z")

    print(f"Downloading {URL}")
    urllib.request.urlretrieve(URL, archive)
    size_mb = os.path.getsize(archive) / (1024 * 1024)
    print(f"  downloaded {size_mb:.1f} MB")

    print("Extracting ...")
    try:
        subprocess.check_call(["7z", "x", "-y", f"-o{OUT_DIR}", archive])
    except FileNotFoundError:
        print(
            "ERROR: `7z` not on PATH. Install with `brew install p7zip` "
            "(macOS) or `apt install p7zip-full` (Debian/Ubuntu).",
            file=sys.stderr,
        )
        return 1

    # Find the extracted .sf2 anywhere under OUT_DIR and move it next to us
    for root, _dirs, files in os.walk(OUT_DIR):
        for fn in files:
            if fn.lower().endswith(".sf2"):
                src = os.path.join(root, fn)
                if os.path.abspath(src) != os.path.abspath(dest):
                    os.replace(src, dest)
                break

    # Clean up archive and any extracted sub-folders
    try:
        os.remove(archive)
    except OSError:
        pass
    for entry in os.listdir(OUT_DIR):
        full = os.path.join(OUT_DIR, entry)
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)

    if os.path.exists(dest):
        print(f"Ready: {dest}")
        return 0
    print("ERROR: no .sf2 ended up in the output directory", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
