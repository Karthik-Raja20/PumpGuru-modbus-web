# PUMPGURU Modbus Web Monitoring & Reporting System

A complete system to connect to a PUMPGURU pump starter/protection controller
over Modbus RTU (RS485), serve a live web dashboard, and generate Excel
analysis reports — plus a standalone diagnostic CLI you can run immediately
against real hardware.

## Start here: test your hardware right now

You do NOT need to set up the full web app to start testing communication
with your PUMPGURU. Use the standalone tool first:

```
pip install pymodbus pyserial
python tools/modbus_quick_test.py --port COM3 --baud 9600 --slave 1
```

If that works, watch registers live while you trigger real fault conditions
to discover the actual register map:

```
python tools/modbus_quick_test.py --port COM3 --slave 1 --start 0 --count 60 --watch
```

Don't know the baud rate? `--autobaud` tries common rates automatically.
See `tools/modbus_quick_test.py --help` for all options, including a guarded
register-write mode for testing setpoints.

**Want a Windows .exe with no Python needed?** Run `tools/build_exe.bat` once
on any Windows machine that has Python installed — it produces a single
portable `modbus_quick_test.exe`.

## ⚠️ About register addresses

**Registers 3030–3035 are CONFIRMED** (voltage R-Y/Y-B/B-R, current R/Y/B phases)
and already wired into `config/register_map.py`. One thing is still a
default assumption: the **scale factor** (currently ÷10, e.g. raw 4152 →
415.2V). Compare a live dashboard reading against the PUMPGURU's own
front-panel display; if it's off by 10x, adjust `VOLTAGE_SCALE` /
`CURRENT_SCALE` at the top of `config/register_map.py` — everything else
updates automatically.

No fault registers (Dry Run, Over/Under Voltage, Over/Under Current, Phase
Detection) are confirmed yet, so the dashboard's fault panel currently shows
"No fault registers configured." Once you confirm those addresses (via the
manual or `tools/modbus_quick_test.py --watch` while triggering faults), add
them to `FAULT_REGISTERS` in `config/register_map.py` following the
commented example already in that file — the dashboard, reports, and API
will pick them up automatically with no other code changes.

## Install (full web app)

```
pip install flask flask-cors pymodbus pyserial openpyxl
```

## Run the web app

```
python run.py --port 5000 --interval 5          # real hardware
python run.py --simulate --port 5000             # demo mode, no hardware needed
```

Open `http://localhost:5000` (or `http://<host-ip>:5000` from another device
on the same network).

## Project layout

```
tools/
  modbus_quick_test.py   Standalone CLI — test hardware immediately, no setup
  build_exe.bat           Builds modbus_quick_test.exe on Windows

config/register_map.py    EDIT THIS: real register addresses + serial settings
core/modbus_client.py     Modbus RTU client used by the web app
core/data_logger.py       SQLite storage of every poll + fault events
core/poller.py            Standalone CLI poller (optional headless logging)

app/                      Flask web application
  __init__.py              App factory + background polling thread
  routes.py                 REST API + page routes
  templates/                dashboard.html, reports.html, settings.html
  static/                   CSS + JS

reports/report_generator.py   Excel report builder (summary/trends/faults)
scripts/register_scanner.py   Register reverse-engineering utility (batch mode)
data/pumpguru.db               Created automatically
```

## REST API

| Endpoint | Description |
|---|---|
| `GET /api/live` | Latest snapshot (measurements + faults + setpoints) |
| `GET /api/status` | Connection status |
| `GET /api/history?hours=N` | Historical series for charts |
| `GET /api/faults/log?days=N` | Fault event log |
| `GET /api/faults/summary?days=N` | Fault counts by type |
| `POST /api/reports/generate` | Generate Excel report — body `{"days": N}` |
| `GET /api/reports/list` | List generated reports |
| `GET /api/reports/download/<file>` | Download a report |

## Full documentation

See `PUMPGURU_Technical_Documentation.docx` for complete block diagrams,
technical datasheet, step-by-step commissioning process, and program
reference.
