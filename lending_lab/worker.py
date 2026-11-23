"""Standalone local demo worker for durable submission steps and restart recovery."""

import argparse
import time

from lending_lab.config import get_settings
from lending_lab.dispatch import recover_interrupted_workflows, run_dispatch_worker_once


def main() -> None:
    get_settings().validate_startup()
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process at most one queued submission")
    parser.add_argument("--recover", action="store_true", help="recover evaluating and queued/expired submissions")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if args.recover:
        print(recover_interrupted_workflows())
        return
    if args.once:
        print(run_dispatch_worker_once())
        return
    while True:
        result = run_dispatch_worker_once()
        if result["status"] == "idle":
            time.sleep(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    main()
