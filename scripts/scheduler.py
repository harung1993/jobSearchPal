"""Runs scripts/daily.sh on a schedule inside Docker, where there's no crontab.

  DAILY_AT=06:30   local time (set TZ, e.g. America/New_York)
  DAILY_DAYS=1-5   ISO weekdays, Monday=1: "1-5", "1,3,5" or "1-7"

Each run's output goes to data/cron.log, like the crontab setup.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


def parse_days(spec: str) -> set[int]:
    days: set[int] = set()
    for part in (spec or "1-5").split(","):
        a, _, b = part.strip().partition("-")
        days.update(range(int(a), int(b or a) + 1))
    return {d for d in days if 1 <= d <= 7}


def next_run(now: datetime, at: str, days: set[int]) -> datetime:
    h, m = (int(x) for x in at.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    while t.isoweekday() not in days:
        t += timedelta(days=1)
    return t


def main():
    root = Path(__file__).resolve().parent.parent
    at, days = os.environ.get("DAILY_AT", "06:30"), parse_days(os.environ.get("DAILY_DAYS", "1-5"))
    while True:
        t = next_run(datetime.now(), at, days)
        print(f"next daily run {t:%a %b %-d %H:%M}", flush=True)
        while (left := (t - datetime.now()).total_seconds()) > 0:
            time.sleep(min(left, 300))  # wake up now and then so clock changes don't drift us
        with open(root / "data" / "cron.log", "a") as log:
            subprocess.run(["bash", str(root / "scripts" / "daily.sh")], cwd=root, stdout=log, stderr=subprocess.STDOUT)


if __name__ == "__main__":
    sys.exit(main())
