"""
run.py — keeps the dashboard up to date by re-fetching data every 60 seconds.

Run with: venv/bin/python3 run.py
Then open dashboard.html in your browser — it will auto-refresh to show the latest data.
Press Ctrl+C to stop.
"""

import time
import subprocess
import sys
import glob
import os
import shutil
from datetime import datetime

REFRESH_SECONDS = 60
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
TMP_DIR         = os.path.join(BASE_DIR, "tmp")

def sweep_tmp_files():
    tmp_files = glob.glob(os.path.join(BASE_DIR, "*.tmp*"))
    if tmp_files:
        os.makedirs(TMP_DIR, exist_ok=True)
        for f in tmp_files:
            shutil.move(f, os.path.join(TMP_DIR, os.path.basename(f)))
        print(f"  Moved {len(tmp_files)} .tmp file(s) to tmp/")

print("Signal Reader — live mode")
print(f"Fetching every {REFRESH_SECONDS} seconds. Open dashboard.html in your browser.")
print("Press Ctrl+C to stop.\n")

while True:
    sweep_tmp_files()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching data...", end=" ", flush=True)
    result = subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "fetch_data.py")],
        capture_output=True, text=True, cwd=BASE_DIR
    )
    if result.returncode == 0:
        print("Done.")
    else:
        print(f"Error: {result.stderr.strip()}")
    time.sleep(REFRESH_SECONDS)
