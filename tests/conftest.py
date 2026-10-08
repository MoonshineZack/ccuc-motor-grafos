import sys
from pathlib import Path

for candidate in (Path(__file__).resolve().parent.parent / "src", Path("/app")):
    if (candidate / "motor_reglas").exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
