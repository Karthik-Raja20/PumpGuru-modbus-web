"""
================================================================================
PUMPGURU REGISTER MAP — CONFIRMED ADDRESSES (2026-08-24)
================================================================================
These 6 registers were confirmed directly by the user against the real
PUMPGURU device. They are Modbus HOLDING REGISTERS (function code 0x03).

    3030  Voltage R-Y (line-to-line, phases R & Y)
    3031  Voltage Y-B (line-to-line, phases Y & B)
    3032  Voltage B-R (line-to-line, phases B & R)
    3033  Current R phase
    3034  Current Y phase
    3035  Current B phase

--------------------------------------------------------------------------------
⚠ ONE ASSUMPTION STILL NEEDS CONFIRMING: THE SCALE FACTOR
--------------------------------------------------------------------------------
The user confirmed WHICH register is WHICH, but not the raw-value scaling.
Modbus registers are 16-bit integers (0-65535) — they cannot hold a decimal
point, so the device sends a scaled integer and the scale must be divided
back out on the host side. This file defaults to SCALE = 10 (the most common
convention for pump protection relays: raw 4152 -> 415.2 V, raw 52 -> 5.2 A).

HOW TO VERIFY / FIX THIS in under a minute:
    1. Run: python tools/modbus_quick_test.py --port COM3 --slave 1 --start 3030 --count 6 --type holding
    2. Compare the raw values shown to the PUMPGURU's own front-panel display.
    3. If the panel shows 415 V and the tool shows raw value 415  -> set SCALE = 1 below.
       If the panel shows 415 V and the tool shows raw value 4150 -> set SCALE = 10 (default, already set).
       If the panel shows 415.2 V and the tool shows raw value 4152 -> set SCALE = 10 (default, already set).
       If raw value is much larger (e.g. 41520) -> set SCALE = 100.
Everything below reads SCALE from this one constant — change it once here and
every part of the app (dashboard, reports, API) updates automatically.
================================================================================
"""

# --- Serial connection (EDIT to match your setup) ----------------------------
SERIAL_CONFIG = {
    "port": "AUTO",          # "AUTO" = auto-detect USB-RS485 adapter; or set manually e.g. "COM3"
    "baudrate": 9600,        # confirm against device setup menu
    "bytesize": 8,
    "parity": "N",           # 'N', 'E', or 'O'
    "stopbits": 1,
    "timeout": 1.0,
    "slave_id": 1,           # Modbus Slave/Unit ID of the PUMPGURU device
}

# --- Scale factor — see note above. Change this ONE value if readings look
#     10x too high or too low compared to the device's own display. ----------
VOLTAGE_SCALE = 1    # raw register value ÷ this = Volts  (device sends whole-number volts, e.g. raw 445 = 445 V)
CURRENT_SCALE = 10   # raw register value ÷ this = Amps

# --- Data Retention ---
# Number of days of historical snapshots and fault events to keep in SQLite database.
# Older records beyond this threshold are automatically deleted to optimize storage.
DATA_RETENTION_DAYS = 30

# --- Feature Flags ---
# Set to True to enable the Register Map page (for engineers / testing).
# Set to False to disable & hide the Register Map page for operator mode.
ENABLE_REGISTER_MAP_PAGE = True

# --- CONFIRMED measurement registers (holding registers) -----------------------
# Order here controls dashboard tile order: Currents first (B, R, Y) then Voltages (B-R, R-Y, Y-B)
# UPDATE: A direct memory scan revealed the PUMPGURU device only exposes ODD 
# addresses starting at 3029. It throws an Exception if you read an even address!
# MEASUREMENT_REGISTERS = {
#     "current_b":  {"address": 3034, "reg_type": "holding", "data_type": "uint16", "scale": CURRENT_SCALE, "unit": "A", "label": "Current B Phase"},
#     "current_r":  {"address": 3032, "reg_type": "holding", "data_type": "uint16", "scale": CURRENT_SCALE, "unit": "A", "label": "Current R Phase"},
#     "current_y":  {"address": 3033, "reg_type": "holding", "data_type": "uint16", "scale": CURRENT_SCALE, "unit": "A", "label": "Current Y Phase"},
#     "voltage_br": {"address": 3031, "reg_type": "holding", "data_type": "uint16", "scale": VOLTAGE_SCALE, "unit": "V", "label": "Voltage B-R"},
#     "voltage_ry": {"address": 3029, "reg_type": "holding", "data_type": "uint16", "scale": VOLTAGE_SCALE, "unit": "V", "label": "Voltage R-Y"},
#     "voltage_yb": {"address": 3030, "reg_type": "holding", "data_type": "uint16", "scale": VOLTAGE_SCALE, "unit": "V", "label": "Voltage Y-B"},
# }
MEASUREMENT_REGISTERS = {
    "voltage_ry": {"address": 3029, "reg_type": "holding", "data_type": "uint16", "scale": VOLTAGE_SCALE, "unit": "V", "label": "Voltage R-Y"},
    "voltage_br": {"address": 3031, "reg_type": "holding", "data_type": "uint16", "scale": VOLTAGE_SCALE, "unit": "V", "label": "Voltage B-R"},
    "voltage_yb": {"address": 3030, "reg_type": "holding", "data_type": "uint16", "scale": VOLTAGE_SCALE, "unit": "V", "label": "Voltage Y-B"},
    "current_r":  {"address": 3032, "reg_type": "holding", "data_type": "uint16", "scale": CURRENT_SCALE, "unit": "A", "label": "Current R Phase"},
    "current_y":  {"address": 3033, "reg_type": "holding", "data_type": "uint16", "scale": CURRENT_SCALE, "unit": "A", "label": "Current Y Phase"},
    "current_b":  {"address": 3034, "reg_type": "holding", "data_type": "uint16", "scale": CURRENT_SCALE, "unit": "A", "label": "Current B Phase"},


    # --- Run-time counters ---
    "run_min_pump1_recent": {"address": 3044, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "min", "label": "Recent Run Minutes (Pump 1)"},
    "run_min_pump2_recent": {"address": 3045, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "min", "label": "Recent Run Minutes (Pump 2)"},
    "run_min_pump1_total":  {"address": 3046, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "min", "label": "Total Run Minutes (Pump 1)"},
    "run_min_pump2_total":  {"address": 3048, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "min", "label": "Total Run Minutes (Pump 2)"},

    # --- Timer setpoints ("Divide by 10") ---
    "set_on_time":  {"address": 3042, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "min", "label": "Set ON Time"},
    "set_off_time": {"address": 3043, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "min", "label": "Set OFF Time"},

    # --- Current setpoints ("multiple of 10") ---
    "set_current_1":   {"address": 3056, "reg_type": "holding", "data_type": "uint16", "scale": 10, "unit": "A", "label": "Set Current 1"},
    "set_current_2":   {"address": 3057, "reg_type": "holding", "data_type": "uint16", "scale": 10, "unit": "A", "label": "Set Current 2"},
    "set_dry_current": {"address": 3058, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "%", "label": "Set Dry-Run Current"},

    # --- Tank Levels (Binary sensors) ---
    "tank_bottom_low":  {"address": 3036, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "", "label": "Bottom Tank Low Sensor"},
    "tank_bottom_high": {"address": 3037, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "", "label": "Bottom Tank High Sensor"},
    "tank_top_low":     {"address": 3038, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "", "label": "Top Tank Low Sensor"},
    "tank_top_high":    {"address": 3039, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "", "label": "Top Tank High Sensor"},
    
    # --- System Control Registers (Read/Write) ---
    "control_auto_manual":   {"address": 3052, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "", "label": "Auto/Manual Mode"},
    "control_run_stop":      {"address": 3053, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "", "label": "Pump Run/Stop Control"},
    "control_pump_selection": {"address": 3054, "reg_type": "holding", "data_type": "uint16", "scale": 1, "unit": "", "label": "Pump Selection Mode"},
}

# --- CONFIRMED fault register (holding register, address 3041) ---------------
# The PUMPGURU writes a single integer CODE to this register:
#   0  = No fault / Normal
#   1  = Dry Run
#   2  = Overload
#   3  = Stall Pump
#   4  = Phase Reverse
#   5  = Under Voltage
#   6  = Phase Loss
#   7  = Over Voltage
#
# The modbus_client decodes the code into per-fault booleans so the rest of
# the app (dashboard, data logger, reports) works without any other changes.

FAULT_CODE_REGISTER = {
    "address": 3041,
    "reg_type": "holding",
}

# Maps the raw integer code -> (key, human-readable label)
FAULT_CODE_MAP = {
    1: ("dry_run",       "Dry Run"),
    2: ("overload",      "Overload"),
    3: ("stall_pump",    "Stall Pump"),
    4: ("phase_reverse", "Phase Reverse"),
    5: ("undervoltage",  "Under Voltage"),
    6: ("phase_loss",    "Phase Loss"),
    7: ("overvoltage",   "Over Voltage"),
}

# FAULT_REGISTERS is kept as a dict so existing code that iterates it still
# works — it lists every possible fault key with a label for the UI.
FAULT_REGISTERS = {
    key: {"label": label}
    for code, (key, label) in FAULT_CODE_MAP.items()
}

# --- No setpoint registers confirmed yet (same pattern as faults, above). ---
SETPOINT_REGISTERS = {}

# Merge everything for convenience elsewhere in the code
ALL_MEASUREMENTS = MEASUREMENT_REGISTERS
ALL_FAULTS = FAULT_REGISTERS
ALL_SETPOINTS = SETPOINT_REGISTERS