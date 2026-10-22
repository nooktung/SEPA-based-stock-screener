"""Windowless Windows scheduled updates, with logs and serialized publication."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager

from src import pipeline as pl
from src.database import PROJECT_ROOT


def run(args: list[str]) -> int:
    print(f"\nRunning: {' '.join(args)}", flush=True)
    return subprocess.run(
        args, cwd=PROJECT_ROOT, stdout=sys.stdout, stderr=sys.stderr,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).returncode


def command(*args: str) -> int:
    # pythonw has no console; CLI commands use python.exe with CREATE_NO_WINDOW.
    python = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    if not os.path.isfile(python):
        python = sys.executable.replace("pythonw.exe", "python.exe")
    return run([python, "-u", "-m", "src.main", *args])


def wait_pipeline(wait: bool) -> bool:
    deadline = time.monotonic() + (90 * 60 if wait else 0)
    while True:
        holder = pl.acquire_lock(pl.vn_now(), pl.settings())
        if holder is None:
            return True
        if time.monotonic() >= deadline:
            print(f"Pipeline busy: {holder}; leaving publication unchanged.", flush=True)
            return False
        time.sleep(10)


@contextmanager
def scheduled_lock(wait: bool):
    """OS releases this lock even when Task Scheduler terminates a worker."""
    import msvcrt

    path = PROJECT_ROOT / "data" / "scheduled.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    deadline = time.monotonic() + (90 * 60 if wait else 0)
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        try:
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(10)
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def workflow(mode: str) -> int:
    daily = mode == "daily"
    with scheduled_lock(wait=daily) as ready:
        if not ready:
            print("Another scheduled update is running; skipped.", flush=True)
            return 0 if not daily else 1
        if not wait_pipeline(wait=daily):
            return 0 if not daily else 1
        try:
            # Preserve web-list synchronization while collecting news every run.
            sync = command("mylist-sync", *([] if daily else ["--refresh"]))
            if sync not in (0, 3, 10):
                print(f"Web list sync failed (exit={sync}); collecting with the existing list.", flush=True)
            if not daily:
                if command("news") != 0:
                    return 1
                return publish()
        finally:
            pl.release_lock()
        if daily:
            # start owns its pipeline lock. Retry if a manual update won the gap.
            deadline = time.monotonic() + 90 * 60
            while command("start", "--update-only", "--refresh-news") != 0:
                if time.monotonic() >= deadline or not pl.LOCK.exists():
                    return 1
                time.sleep(10)
        if not wait_pipeline(wait=daily):
            return 1
        try:
            # Keep the optional SSI audit from the previous daily job.
            if command("ssi", "--days", "3") == 0:
                command("crosscheck", "--days", "30")
            return publish()
        finally:
            pl.release_lock()


def publish() -> int:
    if command("export-site", "--update") != 0:
        return 1
    return run([
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden", "-File",
        str(PROJECT_ROOT / "scripts" / "deploy_site.ps1"),
    ])


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode not in ("daily", "news"):
        return 2
    os.chdir(PROJECT_ROOT)
    os.environ["PYTHONIOENCODING"] = "utf-8"
    log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"scheduled_{mode}_{pl.vn_now():%Y-%m-%d}.log"
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            print(f"\n[{pl.vn_now().isoformat()}] Starting {mode}", flush=True)
            result = workflow(mode)
            print(f"[{pl.vn_now().isoformat()}] Finished: exit={result}", flush=True)
            return result
        except Exception:
            import traceback
            traceback.print_exc()
            return 1
        finally:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__


if __name__ == "__main__":
    raise SystemExit(main())
