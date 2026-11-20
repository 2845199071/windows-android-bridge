"""压测用：密集调用 adb。参数 legacy = 只用 CREATE_NO_WINDOW，new = 三重保险。"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from config import find_adb  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "new"
adb_path = find_adb()

if mode == "legacy":
    kwargs: dict = {"creationflags": 0x08000000}
else:
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    kwargs = {"creationflags": 0x08000000 | 0x00000008, "startupinfo": si}

for _ in range(25):
    try:
        subprocess.run(
            [adb_path, "shell", "echo probe"],
            capture_output=True,
            timeout=20,
            **kwargs,
        )
    except Exception:
        pass
    time.sleep(0.08)
