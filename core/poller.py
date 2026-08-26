"""
Continuous polling loop: reads PUMPGURU over Modbus every N seconds
and logs each snapshot to SQLite. Run this in the background (or as a
scheduled/systemd/Task Scheduler job) to build up history for reports.

Usage:
    python core/poller.py --interval 5
"""

import sys
import os
import time
import argparse
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.modbus_client import PumpGuruClient
from core.data_logger import DataLogger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("pumpguru.poller")


def run(interval: float = 5.0, reconnect_delay: float = 10.0):
    client = PumpGuruClient()
    db = DataLogger()
    previous_faults = None

    while True:
        if not client.connected:
            if not client.connect():
                logger.warning(f"Connection failed. Retrying in {reconnect_delay}s...")
                time.sleep(reconnect_delay)
                continue

        try:
            snapshot = client.read_all()
            db.log_snapshot(snapshot, previous_faults=previous_faults)
            previous_faults = snapshot["faults"]

            active_faults = [k for k, v in snapshot["faults"].items() if v]
            if active_faults:
                logger.warning(f"ACTIVE FAULTS: {active_faults}")
            else:
                logger.info(f"OK | {snapshot['measurements']}")

        except Exception as e:
            logger.error(f"Poll failed: {e}. Will attempt reconnect.")
            client.close()

        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    args = parser.parse_args()
    run(interval=args.interval)
