"""
REST API + page routes for the PUMPGURU web app.

Endpoints:
    GET  /                          -> dashboard page
    GET  /reports                   -> reports page
    GET  /settings                  -> register map / connection settings page

    GET  /api/live                  -> latest snapshot (JSON)
    GET  /api/status                -> connection status
    GET  /api/history?hours=24      -> historical snapshots for charts
    GET  /api/faults/log?days=7     -> fault event log
    GET  /api/faults/summary?days=7 -> fault counts breakdown

    POST /api/reports/generate      -> generate an Excel report, returns download link
    GET  /api/reports/list          -> list previously generated reports
    GET  /api/reports/download/<f>  -> download a generated report file
"""

import os
import sys
import logging
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, jsonify, request, render_template, send_from_directory, current_app

from core.data_logger import DataLogger
from reports.report_generator import generate_report
from reports.pdf_report_generator import generate_pdf_report

bp = Blueprint("main", __name__)
logger = logging.getLogger("pumpguru.routes")
db = DataLogger()

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "output")


@bp.context_processor
def inject_global_flags():
    try:
        from config.register_map import ENABLE_REGISTER_MAP_PAGE
    except ImportError:
        ENABLE_REGISTER_MAP_PAGE = True
    return dict(ENABLE_REGISTER_MAP_PAGE=ENABLE_REGISTER_MAP_PAGE)


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@bp.route("/")
def dashboard_page():
    return render_template("dashboard.html", active="dashboard")


@bp.route("/reports")
def reports_page():
    return render_template("reports.html", active="reports")


@bp.route("/tanks")
def tanks_page():
    return render_template("tanks.html", active="tanks")


@bp.route("/settings")
def settings_page():
    from config.register_map import SERIAL_CONFIG
    return render_template(
        "settings.html",
        serial_config=SERIAL_CONFIG,
        active="settings",
    )


@bp.route("/register-map")
@bp.route("/register_map")
def register_map_page():
    from config.register_map import ENABLE_REGISTER_MAP_PAGE, MEASUREMENT_REGISTERS, FAULT_REGISTERS, SETPOINT_REGISTERS
    if not ENABLE_REGISTER_MAP_PAGE:
        return render_template("register_map_disabled.html", active="register_map"), 403
    return render_template(
        "register_map.html",
        measurements=MEASUREMENT_REGISTERS,
        faults=FAULT_REGISTERS,
        setpoints=SETPOINT_REGISTERS,
        active="register_map",
    )

@bp.route("/api/settings/update", methods=["POST"])
def api_update_settings():
    from app import state
    from config.register_map import SERIAL_CONFIG
    import re
    import os

    payload = request.get_json()
    if not payload or "slave_id" not in payload:
        return jsonify({"status": "error", "message": "Missing slave_id"}), 400

    new_id = int(payload["slave_id"])

    # 1. Update in-memory for the web app UI
    SERIAL_CONFIG["slave_id"] = new_id

    # 2. Update the config file so it persists on next restart
    reg_map_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "register_map.py")
    with open(reg_map_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Regex to find and replace `"slave_id": <number>,`
    new_content = re.sub(r'("slave_id"\s*:\s*)\d+', rf'\g<1>{new_id}', content)

    with open(reg_map_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    # 3. Tell background thread to reconnect with new config
    state.force_reconnect = True

    return jsonify({"status": "ok", "message": f"Slave ID updated to {new_id} and restarting connection."})

# --------------------------------------------------------------------------- #
# Live data API
# --------------------------------------------------------------------------- #
@bp.route("/api/live")
def api_live():
    from app import state
    snapshot = state.get_snapshot()
    if snapshot is None:
        return jsonify({"status": "no_data", "message": "Waiting for first poll..."}), 200
    return jsonify({"status": "ok", "data": snapshot})


@bp.route("/api/status")
def api_status():
    from app import state
    return jsonify({
        "connected": state.connected,
        "simulate_mode": state.simulate,
        "last_error": state.last_error,
        "poll_count": state.poll_count,
    })


@bp.route("/api/config")
def api_config():
    """Exposes the register map's measurement metadata (names, labels, units)
    so the frontend can render tiles/charts dynamically without hardcoding
    parameter names -- keeps the dashboard in sync with config/register_map.py
    automatically whenever registers are added or changed."""
    from config.register_map import MEASUREMENT_REGISTERS, FAULT_REGISTERS, SETPOINT_REGISTERS
    return jsonify({
        "measurements": {
            name: {"label": meta.get("label", name), "unit": meta.get("unit", "")}
            for name, meta in MEASUREMENT_REGISTERS.items()
        },
        "faults": {
            key: {"label": meta.get("label", key)}
            for key, meta in FAULT_REGISTERS.items()
        },
        "setpoints": list(SETPOINT_REGISTERS.keys()),
    })


@bp.route("/api/history")
def api_history():
    hours = float(request.args.get("hours", 24))
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    snapshots = db.get_snapshots(since=since)

    from config.register_map import MEASUREMENT_REGISTERS
    param_names = list(MEASUREMENT_REGISTERS.keys())

    # Return a lightweight shape for charting -- fully driven by the register
    # map, so this endpoint never needs editing when registers change.
    result = {"timestamps": []}
    for p in param_names:
        result[p] = []
    for s in snapshots:
        m = s["measurements"]
        result["timestamps"].append(s["timestamp"])
        for p in param_names:
            result[p].append(m.get(p))

    return jsonify(result)


@bp.route("/api/faults/log")
def api_faults_log():
    days = float(request.args.get("days", 7))
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    events = db.get_fault_events(since=since)
    return jsonify(events)


@bp.route("/api/faults/summary")
def api_faults_summary():
    from collections import Counter
    days = float(request.args.get("days", 7))
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    events = db.get_fault_events(since=since)
    counts = Counter(e["fault_name"] for e in events if e["state"] == "ACTIVE")
    return jsonify(dict(counts))


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
@bp.route("/api/reports/generate", methods=["POST"])
def api_generate_report():
    payload = request.get_json(silent=True) or {}
    days = int(payload.get("days", 7))
    report_format = str(payload.get("format", "excel")).lower().strip()

    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if report_format == "pdf":
        filename = f"pumpguru_report_{days}d_{ts}.pdf"
        output_path = os.path.join(REPORTS_DIR, filename)
        granularity = "hourly" if days == 1 else "daily"
        result_path = generate_pdf_report(days=days, granularity=granularity, output_path=output_path)
    else:
        filename = f"pumpguru_report_{days}d_{ts}.xlsx"
        output_path = os.path.join(REPORTS_DIR, filename)
        result_path = generate_report(days=days, output_path=output_path)

    if result_path is None:
        return jsonify({"status": "error", "message": f"No data available for the selected period ({days} day(s))."}), 400

    return jsonify({
        "status": "ok",
        "filename": filename,
        "format": "pdf" if filename.endswith(".pdf") else "excel",
        "download_url": f"/api/reports/download/{filename}"
    })


@bp.route("/api/reports/list")
def api_list_reports():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    allowed_exts = (".xlsx", ".pdf")
    files = sorted(
        [f for f in os.listdir(REPORTS_DIR) if f.lower().endswith(allowed_exts)],
        key=lambda f: os.path.getmtime(os.path.join(REPORTS_DIR, f)),
        reverse=True,
    )
    out = []
    for f in files:
        path = os.path.join(REPORTS_DIR, f)
        out.append({
            "filename": f,
            "format": "pdf" if f.lower().endswith(".pdf") else "excel",
            "created": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds"),
            "size_kb": round(os.path.getsize(path) / 1024, 1),
            "download_url": f"/api/reports/download/{f}",
        })
    return jsonify(out)


@bp.route("/api/reports/download/<path:filename>")
def api_download_report(filename):
    return send_from_directory(REPORTS_DIR, filename, as_attachment=True)


@bp.route("/api/write", methods=["POST"])
def api_write():
    from app import state
    payload = request.get_json(silent=True) or {}
    address = payload.get("address")
    value = payload.get("value")

    if address is None or value is None:
        return jsonify({"status": "error", "message": "Missing address or value"}), 400

    try:
        address = int(address)
        value = int(value)
    except ValueError:
        return jsonify({"status": "error", "message": "Address and value must be integers"}), 400

    # # Queue the write task for the background poller thread
    # state.pending_writes.append({"address": address, "value": value})
    # logger.info(f"Queued write to background thread: register {address} = {value}")

        # Queue the write task for the background poller thread
    state.queue_write(address, value)
    logger.info(f"Queued write to background thread: register {address} = {value}")

    return jsonify({"status": "ok", "message": f"Write of {value} to register {address} queued."})

