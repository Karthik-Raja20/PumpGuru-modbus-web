"""
================================================================================
REGISTER SCANNER — reverse-engineer PUMPGURU's real register map
================================================================================
Use this if you don't have the manual. It polls a range of registers
repeatedly and prints values that CHANGE between reads, so you can trigger
real-world conditions and see which address responds.

HOW TO USE:
    1. Run this script — it will continuously scan and print a table.
    2. While it's running, in the real world:
         - Let the pump run normally, note baseline values
         - Manually trip dry-run (or lower tank below sensor)
         - Unplug one phase briefly (if safe to do so / on a test setup)
         - Watch which register value flips from 0->1 or changes sharply
    3. Note the address + reg_type + what happened -> write it into
       config/register_map.py

Run:
    python scripts/register_scanner.py --start 0 --end 60 --type both
================================================================================
"""

import sys
import os
import time
import argparse
import inspect

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymodbus.client import ModbusSerialClient
from config.register_map import SERIAL_CONFIG


def _slave_kwarg_name(client_method):
    params = inspect.signature(client_method).parameters
    for candidate in ("slave", "device_id", "unit"):
        if candidate in params:
            return candidate
    return "slave"


def scan_once(client, slave_id, start, end, reg_type):
    values = {}
    for addr in range(start, end):
        try:
            if reg_type == "holding":
                kw = _slave_kwarg_name(client.read_holding_registers)
                res = client.read_holding_registers(address=addr, count=1, **{kw: slave_id})
            else:
                kw = _slave_kwarg_name(client.read_input_registers)
                res = client.read_input_registers(address=addr, count=1, **{kw: slave_id})
            if not res.isError():
                values[addr] = res.registers[0]
            else:
                values[addr] = None
        except Exception:
            values[addr] = None
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=60)
    parser.add_argument("--type", choices=["holding", "input", "both"], default="both")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between scans")
    args = parser.parse_args()

    client = ModbusSerialClient(
        port=SERIAL_CONFIG["port"],
        baudrate=SERIAL_CONFIG["baudrate"],
        bytesize=SERIAL_CONFIG["bytesize"],
        parity=SERIAL_CONFIG["parity"],
        stopbits=SERIAL_CONFIG["stopbits"],
        timeout=SERIAL_CONFIG["timeout"],
    )
    if not client.connect():
        print(f"FAILED to connect on {SERIAL_CONFIG['port']}. Check port name / USB adapter / drivers.")
        return

    slave_id = SERIAL_CONFIG["slave_id"]
    reg_types = ["holding", "input"] if args.type == "both" else [args.type]

    print(f"Scanning registers {args.start}-{args.end} on slave {slave_id}, types={reg_types}")
    print("Press Ctrl+C to stop. Watching for CHANGED values between scans...\n")

    previous = {rt: {} for rt in reg_types}

    try:
        while True:
            for rt in reg_types:
                current = scan_once(client, slave_id, args.start, args.end, rt)
                changed = {
                    addr: (previous[rt].get(addr), val)
                    for addr, val in current.items()
                    if previous[rt].get(addr) is not None and previous[rt].get(addr) != val
                }
                if changed:
                    print(f"--- [{rt.upper()}] CHANGED at {time.strftime('%H:%M:%S')} ---")
                    for addr, (old, new) in changed.items():
                        print(f"  addr {addr}: {old} -> {new}")
                previous[rt] = current
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped. Full last snapshot:")
        for rt in reg_types:
            print(f"\n[{rt.upper()}]")
            for addr, val in sorted(previous[rt].items()):
                print(f"  addr {addr}: {val}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
