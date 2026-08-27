"""
PUMPGURU Web App — Flask application factory + background Modbus poller.

The poller runs in a daemon thread inside the Flask process so the web app
is a single process you can start with `python run.py` — no separate
service needed. It polls the PUMPGURU device on an interval, logs every
snapshot to SQLite, and keeps the latest snapshot in memory so API/websocket
requests are instant (no live Modbus round-trip per browser refresh).
"""

import sys
import os
import threading
import time
import logging
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_cors import CORS

from core.modbus_client import PumpGuruClient
from core.data_logger import DataLogger

logger = logging.getLogger("pumpguru.web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class PollerState:
    """Shared in-memory state between the background poller thread and Flask routes."""
    def __init__(self):
        self.lock = threading.Lock()
        self.latest_snapshot = None
        self.connected = False
        self.last_error = None
        self.poll_count = 0
        self.previous_faults = None
        self.simulate = False  # set True to run without real hardware (demo mode)
        self.force_reconnect = False
        self.pending_writes = []  # list of (address, value) tuples to write
        self.write_lock = threading.Lock()   # <-- ADD

        self.simulated_registers = {
            3052: 102,  # AUTO by default
            3053: 0,    # STOP by default
            3054: 0,    # 1ST PUMP by default
            3056: 65,   # Set Current 1 (6.5 A)
            3057: 70,   # Set Current 2 (7.0 A)
            3058: 500,  # Set Dry Current (50.0 %)
        }
    def queue_write(self, address, value):    # <-- ADD
        with self.write_lock:
            # Drop duplicate if same address+value already queued (debounce at server level too)
            if self.pending_writes and self.pending_writes[-1] == {"address": address, "value": value}:
                return
            self.pending_writes.append({"address": address, "value": value})

    def pop_all_writes(self):                  # <-- ADD
        with self.write_lock:
            writes, self.pending_writes = self.pending_writes, []
            return writes

    def set_snapshot(self, snapshot):
        with self.lock:
            self.latest_snapshot = snapshot
            self.poll_count += 1

    def get_snapshot(self):
        with self.lock:
            return self.latest_snapshot


state = PollerState()


def _simulated_snapshot():
    """Generates a plausible snapshot when no physical device is connected —
    lets the web app be fully demoed/tested before hardware is connected.
    Driven entirely by config/register_map.py so simulated data always
    matches whatever registers are actually configured."""
    import random
    from config.register_map import MEASUREMENT_REGISTERS, FAULT_REGISTERS, SETPOINT_REGISTERS

    # Cohesive states for tanks: 0 = EMPTY, 1 = HALF, 2 = FULL
    bottom_state = random.choice([0, 1, 2])
    top_state = random.choice([0, 1, 2])

    measurements = {}
    for name, meta in MEASUREMENT_REGISTERS.items():
        if name == "tank_bottom_low":
            measurements[name] = 1 if bottom_state >= 1 else 0
        elif name == "tank_bottom_high":
            measurements[name] = 1 if bottom_state >= 2 else 0
        elif name == "tank_top_low":
            measurements[name] = 1 if top_state >= 1 else 0
        elif name == "tank_top_high":
            measurements[name] = 1 if top_state >= 2 else 0
        elif name == "control_auto_manual":
            measurements[name] = state.simulated_registers[3052]
        elif name == "control_run_stop":
            measurements[name] = state.simulated_registers[3053]
        elif name == "control_pump_selection":
            measurements[name] = state.simulated_registers[3054]
        else:
            unit = meta.get("unit", "")
            if unit == "V":
                measurements[name] = round(random.uniform(408, 422), 1)
            elif unit == "A":
                if "dry" in name:
                    measurements[name] = round(random.uniform(2.0, 3.5), 1)
                elif "set" in name:
                    measurements[name] = round(random.uniform(6.0, 8.5), 1)
                else:
                    measurements[name] = round(random.uniform(4.5, 5.7), 2)
            elif unit == "min":
                if "total" in name:
                    measurements[name] = round(random.uniform(120, 600), 1)
                else:
                    measurements[name] = round(random.uniform(5, 45), 1)
            elif unit == "s":
                measurements[name] = round(random.uniform(10, 60), 1)
            elif unit == "°C":
                measurements[name] = round(random.uniform(40, 65), 1)
            elif unit == "Hz":
                measurements[name] = 50.0
            else:
                measurements[name] = round(random.uniform(0, 100), 2)

    # Simulate motor status: running if bottom tank is not empty and top tank is not full
    # (Just a simple simulation heuristic to make it look alive)
    # The fault status code: 0 = OK, 1 = Dry Run, etc.
    # If bottom tank is empty, trigger Dry Run (code 1) sometimes
    fault_code = 0
    if bottom_state == 0 and random.random() < 0.4:
        fault_code = 1  # Dry Run

    # Map the simulated fault code to active faults
    from config.register_map import FAULT_CODE_MAP
    faults = {key: False for key, _label in FAULT_CODE_MAP.values()}
    if fault_code != 0 and fault_code in FAULT_CODE_MAP:
        faults[FAULT_CODE_MAP[fault_code][0]] = True

    setpoints = {name: 0 for name in SETPOINT_REGISTERS.keys()}

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "measurements": measurements,
        "faults": faults,
        "setpoints": setpoints,
    }


def _poll_loop(interval: float):
    db = DataLogger()
    client = None

    if not state.simulate:
        client = PumpGuruClient()

    while True:
        try:
            if state.simulate:
                # --- Process simulated writes ---
                for write_task in state.pop_all_writes():
                    addr = write_task['address']
                    val = write_task['value']
                    logger.info(f"[SIMULATE] Modbus WRITE: Address {addr} = {val}")
                    state.simulated_registers[addr] = val

                snapshot = _simulated_snapshot()
                state.connected = True
                state.last_error = None
            else:
                if state.force_reconnect and client:
                    logger.info("Reconnecting client with new config...")
                    client.close()
                    client = PumpGuruClient()
                    state.force_reconnect = False

                # --- Connect if not already connected ---
                if not client.connected:
                    if not client.connect():
                        state.connected = False
                        state.last_error = f"Could not open serial port {client.cfg['port']}"
                        time.sleep(interval)
                        continue

                # --- Process pending writes ---
                for write_task in state.pop_all_writes():
                    # write_task = state.pending_writes.pop(0)
                    addr = write_task['address']
                    val = write_task['value']
                    logger.info(f"Modbus WRITE: Address {addr} = {val}")
                    success = client.write_holding_register(addr, val)
                    if not success:
                        logger.error(f"Failed to write Modbus register {addr} = {val}")

                # --- Read snapshot ---
                snapshot = client.read_all()

                # --- Check: did a hard serial error (PermissionError/OSError) kill
                #     the port mid-read?  _read_raw() sets client.connected=False in
                #     that case.  Don't save a broken snapshot; reconnect next cycle.
                if not client.connected:
                    state.connected = False
                    state.last_error = "Serial port lost mid-poll — will reconnect"
                    logger.warning("Serial port lost mid-poll — discarding snapshot, reconnecting next cycle.")
                    try:
                        client.close()
                    except Exception:
                        pass
                    time.sleep(interval)
                    continue

                state.connected = True
                state.last_error = None

            db.log_snapshot(snapshot, previous_faults=state.previous_faults)
            state.previous_faults = snapshot["faults"]
            state.set_snapshot(snapshot)

        except Exception as e:
            state.connected = False
            state.last_error = str(e)
            logger.error(f"Poll loop error: {e}")
            if client:
                try:
                    client.close()
                except Exception:
                    pass

        time.sleep(interval)



def start_background_poller(interval: float = 5.0, simulate: bool = False):
    state.simulate = simulate
    t = threading.Thread(target=_poll_loop, args=(interval,), daemon=True)
    t.start()
    logger.info(f"Background poller started (interval={interval}s, simulate={simulate})")


def create_app(simulate: bool = False, poll_interval: float = 5.0):
    app = Flask(__name__)
    CORS(app)

    from app.routes import bp as api_bp
    app.register_blueprint(api_bp)

    start_background_poller(interval=poll_interval, simulate=simulate)

    return app
