"""Run CPU regression checks without opening a game window or GPU context.

Usage: env/bin/python tools/validate.py --report development/evidence/validation.json
"""
from pathlib import Path
import argparse
import datetime
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def validate_asset():
    import numpy as np
    path = ROOT / "assets/new_wave/roamer.npz"
    results = {}
    with np.load(path, allow_pickle=False) as asset:
        if set(asset.files) != {"body", "wheel"}:
            raise ValueError("Expected body and wheel arrays")
        for name in ("body", "wheel"):
            vertices = asset[name]
            if vertices.dtype != np.float32 or vertices.ndim != 2 or vertices.shape[1] != 9:
                raise ValueError(f"{name}: expected Nx9 float32")
            if not len(vertices) or len(vertices) % 3 or not np.isfinite(vertices).all():
                raise ValueError(f"{name}: invalid triangle soup")
            normals = np.linalg.norm(vertices[:, 3:6], axis=1)
            if not np.allclose(normals, 1, atol=1e-4, rtol=0):
                raise ValueError(f"{name}: non-unit normals")
            if not ((vertices[:, 6:] >= 0) & (vertices[:, 6:] <= 1)).all():
                raise ValueError(f"{name}: color outside linear RGB range")
            lo, hi = vertices[:, :3].min(axis=0), vertices[:, :3].max(axis=0)
            dims = hi-lo
            if name == "body":
                if not (1.8 < dims[0] < 2.3 and 1.1 < dims[1] < 1.4 and 4.1 < dims[2] < 4.5):
                    raise ValueError(f"Vehicle body dimensions invalid: {dims}")
                if not (.3 < lo[1] < .5 and 1.5 < hi[1] < 1.7):
                    raise ValueError("Vehicle ground clearance/roof height invalid")
            else:
                radius = np.linalg.norm(vertices[:, 1:3], axis=1)
                if not np.isclose(radius.max(), .36, atol=1e-5):
                    raise ValueError("Wheel radius must be .36 meters")
                if not np.allclose(lo, -hi, atol=1e-5) or not dims[0] < .3:
                    raise ValueError("Wheel must be centered at origin, axle X")
            results[name] = {"triangles": len(vertices)//3, "bounds_min": lo.tolist(),
                             "bounds_max": hi.tolist(), "dimensions": dims.tolist()}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    checks = []
    commands = [(p.name, [sys.executable, str(p)]) for p in sorted((ROOT/"tests").glob("smoke_*.py"))]
    commands += [("new-system unit tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"]),
                 ("compile", [sys.executable, "-m", "compileall", "-q", "new_wave", "drive.py"])]
    for name, command in commands:
        before = time.perf_counter()
        print(f"RUN  {name}", flush=True)
        try:
            run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=180)
            result = {"name": name, "passed": run.returncode == 0, "returncode": run.returncode,
                      "seconds": time.perf_counter()-before, "stdout": run.stdout, "stderr": run.stderr}
        except subprocess.TimeoutExpired as exc:
            result = {"name": name, "passed": False, "seconds": time.perf_counter()-before,
                      "error": "Timed out after 180 seconds",
                      "stdout": str(exc.stdout or ""), "stderr": str(exc.stderr or "")}
        checks.append(result)
        print(f"{'PASS' if result['passed'] else 'FAIL'} {name} ({result['seconds']:.2f}s)", flush=True)
        if not result["passed"]:
            print(result.get("stdout", "")+result.get("stderr", ""), flush=True)
    before = time.perf_counter()
    try:
        asset = validate_asset()
        checks.append({"name": "vehicle asset", "passed": True, "measurements": asset,
                       "seconds": time.perf_counter()-before})
        print("PASS vehicle asset", flush=True)
    except Exception as exc:
        checks.append({"name": "vehicle asset", "passed": False, "error": str(exc)})
        print(f"FAIL vehicle asset: {exc}", flush=True)
    passed = all(c["passed"] for c in checks)
    report = {"timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "python": sys.version, "executable": sys.executable, "passed": passed,
              "seconds": time.perf_counter()-started, "checks": checks}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+"\n")
    print(f"{sum(c['passed'] for c in checks)}/{len(checks)} checks passed in {report['seconds']:.2f}s")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
