"""Launch prepared paper dashboard, initially paused, with a durable local database."""

import os
import subprocess
from pathlib import Path

from local_postgres import start_database

root = Path(__file__).resolve().parents[1]
env = start_database()
os.chdir(root)
subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
os.execve(
    str(root / ".venv/bin/python"),
    ["python", "-m", "hedge.cli", "serve", "--config", "config/local.practice.yaml", "--port", "8765"],
    env,
)
