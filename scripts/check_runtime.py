import shutil
import sys

if sys.version_info < (3, 9):
    raise SystemExit("Forge requires Python 3.9 or later.")
if not shutil.which("git"):
    raise SystemExit("Forge requires Git on PATH.")
print("Python and Git ready. Forge has no third-party runtime dependencies.")
