from pathlib import Path
import shutil

root = Path(__file__).resolve().parent.parent
for path in (root / "forge", root / "tests", root / "scripts"):
    for cache in path.rglob("__pycache__"):
        shutil.rmtree(cache)
print("Removed Python caches. Run artifacts and patches were preserved.")
