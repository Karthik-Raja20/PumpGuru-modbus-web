"""
Modbus RTU client wrapper for PUMPGURU pump starter/protection device.

Handles:
    - Connecting over RS485 (USB-to-RS485 converter)
    - Reading holding/input registers per the register map
    - Decoding scaled values, 32-bit values, and packed status-word bits
    - Returning a clean dict of {parameter_name: value}

Usage:
    from core.modbus_client import PumpGuruClient
    client = PumpGuruClient()
    client.connect()
    data = client.read_all()
    client.close()
"""

import sys
import os
import logging
import inspect
import time
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

from config.register_map import (
    SERIAL_CONFIG,
    MEASUREMENT_REGISTERS,
    FAULT_REGISTERS,
    FAULT_CODE_REGISTER,
    FAULT_CODE_MAP,
    SETPOINT_REGISTERS,
)

logger = logging.getLogger("pumpguru.modbus")


def _slave_kwarg_name(client_method):
    """pymodbus has renamed the device-address keyword argument across
    versions (unit -> slave -> device_id). Detect which one the installed
    version expects so this code keeps working across pymodbus upgrades."""
    params = inspect.signature(client_method).parameters
    for candidate in ("slave", "device_id", "unit"):
        if candidate in params:
            return candidate
    return "slave"


# Keywords found in Device Manager description for common USB-RS485/RS232 chips
_USB_SERIAL_KEYWORDS = [
    "ch340", "ch341",          # Very common cheap RS485 dongle chip
    "cp210",                   # Silicon Labs CP2102/CP2104
    "ftdi", "ft232",           # FTDI FT232
    "prolific", "pl2303",      # Prolific PL2303
    "usb serial", "usb-serial",
    "rs485", "rs-485",
    "modbus",
]


def auto_detect_port(cfg: dict, silent: bool = False) -> str | None:
    """Scan all COM ports and return the first one that:
      1. Looks like a USB-to-serial adapter (by description keywords), AND
      2. Responds to a Modbus holding-register read on the configured slave ID.

    Falls back to any USB-serial port even if no Modbus response (better
    than nothing when the device is off but the adapter is plugged in).

    Returns the port name string (e.g. 'COM3') or None if nothing found.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        if not silent:
            logger.error("pyserial not installed — cannot auto-detect port. Install it with: pip install pyserial")
        return None

    all_ports = list_ports.comports()
    if not all_ports:
        if not silent:
            logger.warning("No COM ports found on this machine.")
        return None

    if not silent:
        logger.info(f"Auto-detecting port — scanning {len(all_ports)} available port(s)...")

    usb_candidates = []
    for p in all_ports:
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        combined = desc + " " + hwid
        if any(kw in combined for kw in _USB_SERIAL_KEYWORDS):
            usb_candidates.append(p)
            if not silent:
                logger.info(f"  USB-serial candidate: {p.device} — {p.description}")

    if not usb_candidates:
        # No known USB-serial chip found; try ALL ports as last resort
        if not silent:
            logger.warning("No USB-serial adapter found by description — trying ALL ports.")
        usb_candidates = all_ports

    # Try a quick Modbus ping on each candidate
    first_addr = min(m["address"] for m in MEASUREMENT_REGISTERS.values())
    slave_id   = cfg.get("slave_id", 1)

    for p in usb_candidates:
        port_name = p.device
        if not silent:
            logger.info(f"  Trying Modbus ping on {port_name} ...")
        try:
            test_client = ModbusSerialClient(
                port=port_name,
                baudrate=cfg.get("baudrate", 9600),
                bytesize=cfg.get("bytesize", 8),
                parity=cfg.get("parity", "N"),
                stopbits=cfg.get("stopbits", 1),
                timeout=cfg.get("timeout", 1.0),
            )
            if not test_client.connect():
                test_client.close()
                continue

            kw = _slave_kwarg_name(test_client.read_holding_registers)
            result = test_client.read_holding_registers(
                address=first_addr, count=1, **{kw: slave_id}
            )
            test_client.close()

            if result and not result.isError():
                if not silent:
                    logger.info(f"  ✔ PUMPGURU responded on {port_name} — using this port.")
                return port_name
            else:
                if not silent:
                    logger.info(f"  ✘ {port_name}: no Modbus response.")
        except Exception as exc:
            if not silent:
                logger.info(f"  ✘ {port_name}: error — {exc}")
            try:
                test_client.close()
            except Exception:
                pass

    # No Modbus response anywhere — return the first USB-serial port anyway
    # (device may be off; adapter is still the right port to use)
    if usb_candidates:
        fallback = usb_candidates[0].device
        if not silent:
            logger.warning(
                f"No Modbus response on any port. Using first USB-serial port as fallback: {fallback}"
            )
        return fallback

    if not silent:
        logger.error("Auto-detect failed: no suitable COM port found.")
    return None


class PumpGuruClient:
    def __init__(self, serial_config: dict = None):
        self.cfg = serial_config or SERIAL_CONFIG
        self.client = None
        self.connected = False
        self._auto_detect_failed = False

    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        port = self.cfg["port"]

        # --- Auto-detect: resolve "AUTO" to the real COM port ----------
        if str(port).upper() == "AUTO":
            if not self._auto_detect_failed:
                logger.info("Port set to AUTO — scanning for USB-RS485 adapter...")
            
            detected = auto_detect_port(self.cfg, silent=self._auto_detect_failed)
            if detected:
                port = detected
                # Cache it so subsequent reconnects skip the scan
                self.cfg = {**self.cfg, "port": port}
                self._auto_detect_failed = False
            else:
                if not self._auto_detect_failed:
                    logger.error(
                        "Auto-detect failed. Plug in the USB-RS485 adapter and restart, "
                        "or set the port manually in config/register_map.py."
                    )
                self._auto_detect_failed = True
                return False

        self.client = ModbusSerialClient(
            port=port,
            baudrate=self.cfg["baudrate"],
            bytesize=self.cfg["bytesize"],
            parity=self.cfg["parity"],
            stopbits=self.cfg["stopbits"],
            timeout=self.cfg["timeout"],
        )
        self.connected = self.client.connect()
        if self.connected:
            logger.info(f"Connected to PUMPGURU on {port} @ {self.cfg['baudrate']}bps")
        else:
            logger.error(f"Failed to connect on {port}. Check cable/port/driver.")
        return self.connected

    def close(self):
        if self.client:
            self.client.close()
            self.connected = False

    # ------------------------------------------------------------------ #
    _HARD_DISCONNECT_ERRORS = (PermissionError, OSError)

    def _read_raw(self, address: int, reg_type: str, count: int = 1):
        """Low-level read. Returns list of raw register ints, or None on failure.

        Hard OS errors (PermissionError, OSError) on the serial port mean the
        port has been seized or dropped — we flag self.connected=False so the
        poll loop immediately triggers a reconnect on the next cycle.
        """
        slave = self.cfg["slave_id"]
        try:
            if reg_type == "holding":
                kw = _slave_kwarg_name(self.client.read_holding_registers)
                result = self.client.read_holding_registers(address=address, count=count, **{kw: slave})
            else:  # "input"
                kw = _slave_kwarg_name(self.client.read_input_registers)
                result = self.client.read_input_registers(address=address, count=count, **{kw: slave})

            if result.isError():
                logger.warning(f"Modbus error reading {reg_type} reg {address}: {result}")
                return None
            return result.registers
        except ModbusException as e:
            logger.error(f"Exception reading {reg_type} reg {address}: {e}")
            return None
        except self._HARD_DISCONNECT_ERRORS as e:
            # PermissionError / OSError = port seized or USB dropped.
            # Mark as disconnected so the poll loop reconnects immediately.
            logger.error(
                f"Serial port error on reg {address} — marking disconnected for reconnect: {e}"
            )
            self.connected = False
            return None
        except Exception as e:
            logger.error(f"Unexpected error reading reg {address}: {e}")
            return None

    def _decode(self, raw_regs, data_type: str, scale: float):
        if raw_regs is None:
            return None
        if data_type == "uint16":
            val = raw_regs[0]
        elif data_type == "int16":
            val = raw_regs[0] - 65536 if raw_regs[0] > 32767 else raw_regs[0]
        elif data_type in ("uint32", "int32"):
            val = (raw_regs[0] << 16) + raw_regs[1]
            if data_type == "int32" and val > 0x7FFFFFFF:
                val -= 0x100000000
        elif data_type == "float32":
            import struct
            packed = struct.pack(">HH", raw_regs[0], raw_regs[1])
            val = struct.unpack(">f", packed)[0]
        else:
            val = raw_regs[0]
        return round(val / scale, 3) if scale != 1 else val

    # ------------------------------------------------------------------ #
    def read_measurements(self) -> dict:
        """Read all analog measurements, each register independently with retry.

        Strategy:
        1. First try a single block read (fast, fewer RS485 turnarounds).
        2. If the block read fails OR returns suspiciously short data, fall
           back to reading every register one-by-one.
        3. Each individual read is retried once (50 ms pause) to handle
           occasional RS485 inter-frame timing issues.

        This ensures a failure on ONE register never silences the others —
        each parameter is fetched and decoded independently.
        """
        import time as _time

        out = {}
        by_type = {}
        for name, meta in MEASUREMENT_REGISTERS.items():
            by_type.setdefault(meta["reg_type"], []).append((name, meta))

        for reg_type, items in by_type.items():
            items_sorted = sorted(items, key=lambda x: x[1]["address"])
            min_addr = min(m["address"] for _, m in items_sorted)
            max_meta = max(items_sorted, key=lambda x: x[1]["address"])[1]
            max_words = 2 if max_meta["data_type"] in ("uint32", "int32", "float32") else 1
            span = (max_meta["address"] - min_addr) + max_words

            # --- Attempt 1: single block read (optimisation for contiguous regs) ---
            raw_block = None
            if span <= 32:
                raw_block = self._read_raw(min_addr, reg_type, span)
                if raw_block is not None and len(raw_block) < span:
                    logger.warning(
                        f"Block read at {min_addr} returned {len(raw_block)} words, "
                        f"expected {span} — discarding block, will read individually."
                    )
                    raw_block = None  # treat as failure; read individually below

            # --- Per-register decode (uses block data if available, else individual) ---
            for name, meta in items_sorted:
                words = 2 if meta["data_type"] in ("uint32", "int32", "float32") else 1

                if raw_block is not None:
                    # Happy path: extract from the already-fetched block
                    offset = meta["address"] - min_addr
                    raw = raw_block[offset:offset + words]
                    raw = raw if len(raw) >= words else None
                else:
                    # Per-register read with one retry on failure
                    raw = self._read_raw(meta["address"], meta["reg_type"], words)
                    if raw is None:
                        _time.sleep(0.05)  # 50 ms inter-frame gap before retry
                        raw = self._read_raw(meta["address"], meta["reg_type"], words)
                        if raw is None:
                            logger.warning(
                                f"Register '{name}' (addr={meta['address']}) failed "
                                f"after 2 attempts — value will be null."
                            )

                out[name] = self._decode(raw, meta["data_type"], meta["scale"])

        return out

    def read_faults(self) -> dict:
        """Read the fault code register (addr 3041) and expand to per-fault booleans.

        The PUMPGURU sends a single integer code:
            0  = No fault
            1  = Dry Run
            2  = Overload
            3  = Stall Pump
            4  = Phase Reverse
            5  = Under Voltage
            6  = Phase Loss
            7  = Over Voltage

        Returns a dict like {"dry_run": False, "overload": True, ...} so the
        dashboard, data logger, and reports work without any other changes.

        NOTE: A failure to read register 3041 (e.g. device returns an exception
        frame for that address, or the register isn't accessible) is ISOLATED
        here — it NEVER propagates up to crash the poll loop or set
        state.connected = False.  Measurements will still be shown normally;
        the fault panel will show the amber "comms lost" state instead.
        """
        # Start with all faults as False (OK state)
        out = {key: False for key, _label in FAULT_CODE_MAP.values()}

        try:
            raw = self._read_raw(
                FAULT_CODE_REGISTER["address"],
                FAULT_CODE_REGISTER["reg_type"],
                count=1,
            )
        except Exception as exc:
            logger.warning(
                f"Could not read fault code register (addr={FAULT_CODE_REGISTER['address']}): {exc}"
                " — fault status will show as UNKNOWN. Measurements unaffected."
            )
            return {key: None for key, _label in FAULT_CODE_MAP.values()}

        if raw is None:
            # _read_raw already logged the Modbus-level error; just return unknowns.
            logger.warning(
                f"Fault code register (addr={FAULT_CODE_REGISTER['address']}) returned no data"
                " — fault status will show as UNKNOWN. Measurements unaffected."
            )
            return {key: None for key, _label in FAULT_CODE_MAP.values()}

        code = raw[0]
        logger.debug(f"Fault code register read: {code}")

        if code == 0:
            pass  # All clear — out already has all False
        elif code in FAULT_CODE_MAP:
            fault_key, fault_label = FAULT_CODE_MAP[code]
            out[fault_key] = True
            logger.warning(f"FAULT ACTIVE — code {code}: {fault_label}")
        else:
            logger.warning(f"Unknown fault code received from register 3041: {code}")

        return out


    def read_setpoints(self) -> dict:
        """Read configured protection setpoints (thresholds)."""
        out = {}
        for name, meta in SETPOINT_REGISTERS.items():
            raw = self._read_raw(meta["address"], meta["reg_type"], count=1)
            out[name] = self._decode(raw, meta["data_type"], meta["scale"])
        return out

    def read_all(self) -> dict:
        """Single snapshot: measurements + faults + setpoints + timestamp."""
        snapshot = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "measurements": self.read_measurements(),
            "faults": self.read_faults(),
            "setpoints": self.read_setpoints(),
        }
        return snapshot


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = PumpGuruClient()
    if client.connect():
        data = client.read_all()
        import json
        print(json.dumps(data, indent=2))
        client.close()
    else:
        print("Could not connect. Check SERIAL_CONFIG in config/register_map.py")
