#!/usr/bin/env python3
"""
================================================================================
 MODBUS RTU QUICK-TEST UTILITY  —  standalone, ready to run right now
================================================================================
This tool needs NO project setup, NO register map, NO config file. Point it at
any Modbus RTU slave (including your PUMPGURU) over a USB-RS485 adapter and
immediately see live register values. Use it to:

    1. Confirm basic communication (port, baud, slave ID) works at all.
    2. Read raw values from any register range to identify what's really there.
    3. Continuously watch a register while you trigger real-world conditions
       (dry run, phase loss, etc.) to find out which address responds.
    4. Optionally write to a holding register (e.g. to test a setpoint or
       remote start/stop) — DISABLED unless you pass --allow-write.

--------------------------------------------------------------------------------
INSTALL (one line, one time):
    pip install pymodbus

RUN EXAMPLES:
    # 1) Quick connectivity test — read holding+input regs 0-19
    python modbus_quick_test.py --port COM3 --baud 9600 --slave 1

    # 2) Read a wider range, input registers only
    python modbus_quick_test.py --port /dev/ttyUSB0 --baud 9600 --slave 1 \\
        --start 0 --count 60 --type input

    # 3) Live-watch mode: re-reads every 1s and highlights CHANGED values
    python modbus_quick_test.py --port COM3 --slave 1 --watch

    # 4) Try several common baud rates automatically (when unknown)
    python modbus_quick_test.py --port COM3 --slave 1 --autobaud

    # 5) Read a single holding register as a specific data type
    python modbus_quick_test.py --port COM3 --slave 1 --start 10 --count 2 \\
        --type holding --as float32

    # 6) (Advanced/optional) write a test value to a holding register
    python modbus_quick_test.py --port COM3 --slave 1 --write-address 40 \\
        --write-value 250 --allow-write

--------------------------------------------------------------------------------
EXIT CODES:
    0 = success (or clean Ctrl+C exit from --watch)
    1 = connection failed
    2 = read failed / device did not respond
    3 = bad arguments
================================================================================
"""

import argparse
import struct
import sys
import time
import inspect
from datetime import datetime

try:
    from pymodbus.client import ModbusSerialClient
except ImportError:
    print("ERROR: pymodbus is not installed.")
    print("Install it with:  pip install pymodbus pyserial")
    sys.exit(3)


def _slave_kwarg_name(client_method):
    """pymodbus has renamed the device-address keyword argument across
    versions (unit -> slave -> device_id). Detect which one the installed
    version expects so this tool keeps working across pymodbus upgrades."""
    params = inspect.signature(client_method).parameters
    for candidate in ("slave", "device_id", "unit"):
        if candidate in params:
            return candidate
    return "slave"


COMMON_BAUD_RATES = [9600, 19200, 38400, 4800, 2400, 1200, 57600, 115200]


def decode(regs, as_type):
    """Decode a list of raw 16-bit register ints into the requested type."""
    if as_type == "uint16":
        return regs[0]
    if as_type == "int16":
        return regs[0] - 65536 if regs[0] > 32767 else regs[0]
    if as_type in ("uint32", "int32"):
        if len(regs) < 2:
            return None
        val = (regs[0] << 16) + regs[1]
        if as_type == "int32" and val > 0x7FFFFFFF:
            val -= 0x100000000
        return val
    if as_type == "float32":
        if len(regs) < 2:
            return None
        packed = struct.pack(">HH", regs[0], regs[1])
        return round(struct.unpack(">f", packed)[0], 4)
    return regs[0]


def make_client(port, baud, bytesize, parity, stopbits, timeout):
    return ModbusSerialClient(
        port=port,
        baudrate=baud,
        bytesize=bytesize,
        parity=parity,
        stopbits=stopbits,
        timeout=timeout,
    )


def try_read(client, slave, reg_type, start, count):
    """Returns (registers_list_or_None, error_message_or_None)."""
    try:
        if reg_type == "holding":
            kw = _slave_kwarg_name(client.read_holding_registers)
            result = client.read_holding_registers(address=start, count=count, **{kw: slave})
        else:
            kw = _slave_kwarg_name(client.read_input_registers)
            result = client.read_input_registers(address=start, count=count, **{kw: slave})
        if result.isError():
            return None, str(result)
        return result.registers, None
    except Exception as e:
        return None, str(e)


def print_table(addr_start, reg_type, values, as_type, prev_values=None):
    step = 2 if as_type in ("uint32", "int32", "float32") else 1
    print(f"\n  {'ADDR':<8}{'RAW (hex)':<14}{'RAW (dec)':<14}{'DECODED (' + as_type + ')':<20}{'CHANGED' if prev_values else ''}")
    print("  " + "-" * 70)
    i = 0
    addr = addr_start
    while i < len(values):
        chunk = values[i:i + step]
        if len(chunk) < step:
            break
        decoded = decode(chunk, as_type)
        raw_hex = " ".join(f"0x{v:04X}" for v in chunk)
        raw_dec = " ".join(str(v) for v in chunk)
        changed = ""
        if prev_values is not None:
            prev_chunk = prev_values[i:i + step] if len(prev_values) >= i + step else None
            if prev_chunk is not None and prev_chunk != chunk:
                changed = "  <<< CHANGED"
        print(f"  {addr:<8}{raw_hex:<14}{raw_dec:<14}{str(decoded):<20}{changed}")
        i += step
        addr += step
    print()


def do_autobaud(args):
    print("Trying common baud rates to find one that responds...\n")
    for baud in COMMON_BAUD_RATES:
        print(f"  Trying {baud} bps...", end=" ", flush=True)
        client = make_client(args.port, baud, args.bytesize, args.parity, args.stopbits, args.timeout)
        if not client.connect():
            print("port open failed.")
            continue
        values, err = try_read(client, args.slave, "holding", args.start, min(args.count, 4))
        if values is None:
            values, err = try_read(client, args.slave, "input", args.start, min(args.count, 4))
        client.close()
        if values is not None:
            print(f"SUCCESS — device responded at {baud} bps.")
            print(f"  Use --baud {baud} for further testing.\n")
            return baud
        else:
            print(f"no response ({err})")
    print("\nNo response at any common baud rate. Check wiring (A/B polarity), slave ID, and power.")
    return None


def do_single_read(args):
    client = make_client(args.port, args.baud, args.bytesize, args.parity, args.stopbits, args.timeout)
    print(f"Connecting to {args.port} @ {args.baud}bps, {args.bytesize}{args.parity}{args.stopbits}, slave ID {args.slave} ...")
    if not client.connect():
        print(f"FAILED to open serial port '{args.port}'.")
        print("Check: correct port name, adapter drivers installed, port not in use by another program.")
        return 1

    print("Port opened successfully. Sending Modbus read request...\n")

    types_to_try = ["holding", "input"] if args.type == "both" else [args.type]
    any_success = False

    for reg_type in types_to_try:
        values, err = try_read(client, args.slave, reg_type, args.start, args.count)
        print(f"[{reg_type.upper()} REGISTERS]  address {args.start}-{args.start + args.count - 1}")
        if values is None:
            print(f"  No response / error: {err}")
            print(f"  (This is normal if the device doesn't expose this register type at these addresses.)")
        else:
            any_success = True
            print_table(args.start, reg_type, values, args.as_type)

    client.close()

    if not any_success:
        print("\nNo successful reads. Troubleshooting checklist:")
        print("  1. Is the correct serial port selected? (check Device Manager on Windows, `ls /dev/tty*` on Linux)")
        print("  2. Is the baud rate correct? Try --autobaud to scan common rates.")
        print("  3. Is the Slave ID correct? Check the PUMPGURU's own setup/config menu.")
        print("  4. Is RS485 A/B wiring polarity correct? Try swapping A and B if unsure.")
        print("  5. Is anything else (e.g. PUMPGURU's own PC software) holding the port open?")
        return 2

    return 0


def do_watch(args):
    client = make_client(args.port, args.baud, args.bytesize, args.parity, args.stopbits, args.timeout)
    print(f"Connecting to {args.port} @ {args.baud}bps, slave ID {args.slave} ...")
    if not client.connect():
        print(f"FAILED to open serial port '{args.port}'.")
        return 1

    print(f"Watching {args.type} registers {args.start}-{args.start + args.count - 1}.")
    print("Trigger real-world conditions now (dry run, phase loss, etc.) and watch for CHANGED rows.")
    print("Press Ctrl+C to stop.\n")

    types_to_try = ["holding", "input"] if args.type == "both" else [args.type]
    prev = {t: None for t in types_to_try}

    try:
        while True:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"--- scan at {timestamp} ---")
            for reg_type in types_to_try:
                values, err = try_read(client, args.slave, reg_type, args.start, args.count)
                if values is None:
                    print(f"  [{reg_type}] error: {err}")
                    continue
                print_table(args.start, reg_type, values, args.as_type, prev_values=prev[reg_type])
                prev[reg_type] = values
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        client.close()
    return 0


def do_write(args):
    if not args.allow_write:
        print("Refusing to write: pass --allow-write to confirm you intend to write to the device.")
        print("Writing to the wrong register can change device configuration or trigger the pump.")
        return 3

    client = make_client(args.port, args.baud, args.bytesize, args.parity, args.stopbits, args.timeout)
    print(f"Connecting to {args.port} @ {args.baud}bps, slave ID {args.slave} ...")
    if not client.connect():
        print(f"FAILED to open serial port '{args.port}'.")
        return 1

    print(f"WRITING value {args.write_value} to holding register {args.write_address} ...")
    confirm = input(f"Type 'yes' to confirm this write to a live device: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        client.close()
        return 0

    try:
        kw = _slave_kwarg_name(client.write_register)
        result = client.write_register(address=args.write_address, value=args.write_value, **{kw: args.slave})
        if result.isError():
            print(f"Write FAILED: {result}")
            client.close()
            return 2
        print("Write succeeded. Reading back the register to confirm...")
        values, err = try_read(client, args.slave, "holding", args.write_address, 1)
        if values is not None:
            print(f"  Register {args.write_address} now reads: {values[0]}")
        else:
            print(f"  Could not read back register: {err}")
    except Exception as e:
        print(f"Write raised an exception: {e}")
        client.close()
        return 2

    client.close()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Standalone Modbus RTU quick-test / diagnostic utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port", required=True, help="Serial port, e.g. COM3 (Windows) or /dev/ttyUSB0 (Linux)")
    parser.add_argument("--baud", type=int, default=9600, help="Baud rate (default 9600)")
    parser.add_argument("--bytesize", type=int, default=8, choices=[7, 8], help="Data bits (default 8)")
    parser.add_argument("--parity", default="N", choices=["N", "E", "O"], help="Parity (default N)")
    parser.add_argument("--stopbits", type=int, default=1, choices=[1, 2], help="Stop bits (default 1)")
    parser.add_argument("--timeout", type=float, default=1.0, help="Read timeout in seconds (default 1.0)")
    parser.add_argument("--slave", type=int, default=1, help="Modbus Slave/Unit ID (default 1)")

    parser.add_argument("--start", type=int, default=0, help="Starting register address (default 0)")
    parser.add_argument("--count", type=int, default=20, help="Number of registers to read (default 20)")
    parser.add_argument("--type", choices=["holding", "input", "both"], default="both", help="Register type (default both)")
    parser.add_argument("--as", dest="as_type", choices=["uint16", "int16", "uint32", "int32", "float32"], default="uint16", help="How to decode each value (default uint16)")

    parser.add_argument("--watch", action="store_true", help="Continuously re-read and highlight changed values")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between reads in --watch mode (default 1.0)")
    parser.add_argument("--autobaud", action="store_true", help="Try common baud rates to find one that responds")

    parser.add_argument("--write-address", type=int, help="Holding register address to write to (optional)")
    parser.add_argument("--write-value", type=int, help="Value to write (0-65535, optional)")
    parser.add_argument("--allow-write", action="store_true", help="Required flag to actually perform a write")

    args = parser.parse_args()

    print("=" * 70)
    print("  MODBUS RTU QUICK-TEST UTILITY")
    print("=" * 70)

    if args.autobaud:
        found = do_autobaud(args)
        return 0 if found else 1

    if args.write_address is not None:
        if args.write_value is None:
            print("ERROR: --write-value is required when using --write-address")
            return 3
        return do_write(args)

    if args.watch:
        return do_watch(args)

    return do_single_read(args)


if __name__ == "__main__":
    sys.exit(main())
