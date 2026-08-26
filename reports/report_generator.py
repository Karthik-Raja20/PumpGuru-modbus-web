"""
PUMPGURU Report Generator
Produces an Excel workbook with:
    - Summary sheet (uptime %, fault counts, key stats)
    - Trend data + charts (voltage/current over time)
    - Fault event log (with duration of each fault)

Usage:
    python reports/report_generator.py --days 7 --output reports/output/weekly_report.xlsx
"""

import sys
import os
import argparse
from datetime import datetime, timedelta
from collections import Counter

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import LineChart, Reference
from openpyxl.utils import get_column_letter

from core.data_logger import DataLogger
from config.register_map import MEASUREMENT_REGISTERS

HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
TITLE_FONT = Font(name="Arial", bold=True, size=14)
NORMAL_FONT = Font(name="Arial", size=10)
ALERT_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")


def style_header_row(ws, row_idx, num_cols):
    for c in range(1, num_cols + 1):
        cell = ws.cell(row=row_idx, column=c)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")


def autosize_columns(ws, max_width=30):
    for col in ws.columns:
        length = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(length + 2, max_width)


def build_summary_sheet(wb, snapshots, fault_events, period_label):
    ws = wb.active
    ws.title = "Summary"

    ws["A1"] = "PUMPGURU — Pump Protection Report"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Period: {period_label}"
    ws["A2"].font = NORMAL_FONT
    ws["A3"] = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    ws["A3"].font = NORMAL_FONT

    row = 5
    ws.cell(row=row, column=1, value="Metric").font = Font(bold=True)
    ws.cell(row=row, column=2, value="Value").font = Font(bold=True)
    style_header_row(ws, row, 2)
    row += 1

    total_snapshots = len(snapshots)
    faulted_snapshots = sum(1 for s in snapshots if any(s["faults"].values()))
    uptime_pct = round(100 * (1 - faulted_snapshots / total_snapshots), 2) if total_snapshots else None

    metrics = [
        ("Total data points logged", total_snapshots),
        ("Snapshots with an active fault", faulted_snapshots),
        ("Estimated uptime (fault-free) %", f"{uptime_pct}%" if uptime_pct is not None else "N/A"),
        ("Total fault events (ACTIVE transitions)", sum(1 for e in fault_events if e["state"] == "ACTIVE")),
    ]
    for label, val in metrics:
        ws.cell(row=row, column=1, value=label).font = NORMAL_FONT
        ws.cell(row=row, column=2, value=val).font = NORMAL_FONT
        row += 1

    row += 2
    ws.cell(row=row, column=1, value="Fault Frequency Breakdown").font = Font(bold=True, size=12)
    row += 1
    ws.cell(row=row, column=1, value="Fault Type").font = Font(bold=True)
    ws.cell(row=row, column=2, value="Times Triggered").font = Font(bold=True)
    style_header_row(ws, row, 2)
    row += 1

    fault_counts = Counter(e["fault_name"] for e in fault_events if e["state"] == "ACTIVE")
    if fault_counts:
        for fault_name, count in fault_counts.most_common():
            ws.cell(row=row, column=1, value=fault_name).font = NORMAL_FONT
            ws.cell(row=row, column=2, value=count).font = NORMAL_FONT
            row += 1
    else:
        ws.cell(row=row, column=1, value="No faults recorded in this period.").font = NORMAL_FONT

    # ---- Measurement statistics (min / max / avg / imbalance) ----
    row += 2
    ws.cell(row=row, column=1, value="Measurement Analysis").font = Font(bold=True, size=12)
    row += 1
    headers = ["Parameter", "Min", "Max", "Average", "Unit"]
    for c, h in enumerate(headers, start=1):
        ws.cell(row=row, column=c, value=h).font = Font(bold=True)
    style_header_row(ws, row, len(headers))
    row += 1

    stats = {}
    for name, meta in MEASUREMENT_REGISTERS.items():
        values = [s["measurements"].get(name) for s in snapshots if s["measurements"].get(name) is not None]
        if values:
            stats[name] = {
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "avg": round(sum(values) / len(values), 2),
                "unit": meta.get("unit", ""),
                "label": meta.get("label", name),
            }
            ws.cell(row=row, column=1, value=stats[name]["label"]).font = NORMAL_FONT
            ws.cell(row=row, column=2, value=stats[name]["min"]).font = NORMAL_FONT
            ws.cell(row=row, column=3, value=stats[name]["max"]).font = NORMAL_FONT
            ws.cell(row=row, column=4, value=stats[name]["avg"]).font = NORMAL_FONT
            ws.cell(row=row, column=5, value=stats[name]["unit"]).font = NORMAL_FONT
            row += 1

    # ---- Voltage / current imbalance analysis (if 3-phase data present) ----
    voltage_keys = [k for k in MEASUREMENT_REGISTERS if "voltage" in k]
    current_keys = [k for k in MEASUREMENT_REGISTERS if "current" in k]

    if len(voltage_keys) >= 2 and all(k in stats for k in voltage_keys):
        row += 2
        ws.cell(row=row, column=1, value="Phase Balance Analysis").font = Font(bold=True, size=12)
        row += 1
        v_avgs = [stats[k]["avg"] for k in voltage_keys]
        v_mean = sum(v_avgs) / len(v_avgs)
        v_imbalance = round(100 * (max(v_avgs) - min(v_avgs)) / v_mean, 2) if v_mean else 0
        ws.cell(row=row, column=1, value="Voltage Imbalance (avg, max-min / mean)").font = NORMAL_FONT
        ws.cell(row=row, column=2, value=f"{v_imbalance}%").font = NORMAL_FONT
        row += 1

    if len(current_keys) >= 2 and all(k in stats for k in current_keys):
        c_avgs = [stats[k]["avg"] for k in current_keys]
        c_mean = sum(c_avgs) / len(c_avgs)
        c_imbalance = round(100 * (max(c_avgs) - min(c_avgs)) / c_mean, 2) if c_mean else 0
        ws.cell(row=row, column=1, value="Current Imbalance (avg, max-min / mean)").font = NORMAL_FONT
        ws.cell(row=row, column=2, value=f"{c_imbalance}%").font = NORMAL_FONT
        row += 1

    autosize_columns(ws)
    return ws


def build_trend_sheet(wb, snapshots):
    ws = wb.create_sheet("Trends")

    # Column order: Timestamp, then every measurement in MEASUREMENT_REGISTERS
    # (data-driven off the register map so this never goes stale when
    # registers are added/changed — no hardcoded parameter names).
    param_names = list(MEASUREMENT_REGISTERS.keys())
    headers = ["Timestamp"] + [MEASUREMENT_REGISTERS[p].get("label", p) for p in param_names]
    ws.append(headers)
    style_header_row(ws, 1, len(headers))

    for s in snapshots:
        m = s["measurements"]
        row = [s["timestamp"]] + [m.get(p) for p in param_names]
        ws.append(row)

    autosize_columns(ws)

    if len(snapshots) <= 1:
        return ws

    max_row = len(snapshots) + 1

    # Split into voltage columns and current columns automatically based on
    # each register's unit (V vs A) so charts stay meaningful regardless of
    # how many phases/parameters are configured.
    voltage_cols = []
    current_cols = []
    for i, p in enumerate(param_names):
        col_idx = i + 2  # column 1 is Timestamp
        unit = MEASUREMENT_REGISTERS[p].get("unit", "")
        if unit == "V":
            voltage_cols.append(col_idx)
        elif unit == "A":
            current_cols.append(col_idx)

    chart_anchor_row = 2

    def build_chart(cols, title, y_title):
        chart = LineChart()
        chart.title = title
        chart.y_axis.title = y_title
        chart.x_axis.title = "Time"
        chart.width = 18
        chart.height = 8
        min_col, max_col = min(cols), max(cols)
        if max_col - min_col == len(cols) - 1:
            data = Reference(ws, min_col=min_col, max_col=max_col, min_row=1, max_row=max_row)
            chart.add_data(data, titles_from_data=True)
        else:
            for c in cols:
                data = Reference(ws, min_col=c, max_col=c, min_row=1, max_row=max_row)
                chart.add_data(data, titles_from_data=True)
        return chart

    anchor_col_letter = get_column_letter(len(headers) + 2)
    next_row = chart_anchor_row

    if voltage_cols:
        ws.add_chart(build_chart(voltage_cols, "Voltage Trend", "Volts"), f"{anchor_col_letter}{next_row}")
        next_row += 18
    if current_cols:
        ws.add_chart(build_chart(current_cols, "Current Trend", "Amps"), f"{anchor_col_letter}{next_row}")

    return ws


def build_analysis_sheet(wb, snapshots):
    """Dedicated sheet: per-snapshot phase imbalance % over time, plus a chart.
    Uses whichever voltage/current registers are configured — fully driven
    by MEASUREMENT_REGISTERS, not hardcoded to specific phase names."""
    voltage_keys = [k for k in MEASUREMENT_REGISTERS if "voltage" in k]
    current_keys = [k for k in MEASUREMENT_REGISTERS if "current" in k]

    if len(voltage_keys) < 2 and len(current_keys) < 2:
        return None  # not enough multi-phase data to analyze imbalance

    ws = wb.create_sheet("Phase Analysis")
    headers = ["Timestamp"]
    if len(voltage_keys) >= 2:
        headers.append("Voltage Imbalance %")
    if len(current_keys) >= 2:
        headers.append("Current Imbalance %")
    ws.append(headers)
    style_header_row(ws, 1, len(headers))

    for s in snapshots:
        m = s["measurements"]
        row = [s["timestamp"]]
        if len(voltage_keys) >= 2:
            vals = [m.get(k) for k in voltage_keys if m.get(k) is not None]
            if len(vals) >= 2 and sum(vals):
                mean = sum(vals) / len(vals)
                imbalance = round(100 * (max(vals) - min(vals)) / mean, 2) if mean else 0
                row.append(imbalance)
            else:
                row.append(None)
        if len(current_keys) >= 2:
            vals = [m.get(k) for k in current_keys if m.get(k) is not None]
            if len(vals) >= 2 and sum(vals):
                mean = sum(vals) / len(vals)
                imbalance = round(100 * (max(vals) - min(vals)) / mean, 2) if mean else 0
                row.append(imbalance)
            else:
                row.append(None)
        ws.append(row)

    autosize_columns(ws)

    if len(snapshots) > 1 and len(headers) > 1:
        max_row = len(snapshots) + 1
        chart = LineChart()
        chart.title = "Phase Imbalance Over Time"
        chart.y_axis.title = "Imbalance %"
        chart.x_axis.title = "Time"
        chart.width = 20
        chart.height = 9
        data = Reference(ws, min_col=2, max_col=len(headers), min_row=1, max_row=max_row)
        chart.add_data(data, titles_from_data=True)
        ws.add_chart(chart, "F2")

    return ws


def build_fault_log_sheet(wb, fault_events):
    ws = wb.create_sheet("Fault Event Log")
    headers = ["Timestamp", "Fault", "State"]
    ws.append(headers)
    style_header_row(ws, 1, len(headers))

    for e in fault_events:
        row = [e["timestamp"], e["fault_name"], e["state"]]
        ws.append(row)
        if e["state"] == "ACTIVE":
            for c in range(1, 4):
                ws.cell(row=ws.max_row, column=c).fill = ALERT_FILL

    autosize_columns(ws)
    return ws


def generate_report(days: int = 7, output_path: str = None):
    db = DataLogger()
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    snapshots = db.get_snapshots(since=since)
    fault_events = db.get_fault_events(since=since)

    if not snapshots:
        print(f"No data found for the last {days} day(s). Run core/poller.py first to collect data.")
        return None

    wb = Workbook()
    period_label = f"Last {days} day(s)  ({since} to now)"
    build_summary_sheet(wb, snapshots, fault_events, period_label)
    build_trend_sheet(wb, snapshots)
    build_analysis_sheet(wb, snapshots)
    build_fault_log_sheet(wb, fault_events)

    if output_path is None:
        default_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(default_dir, exist_ok=True)
        output_path = os.path.join(default_dir, f"pumpguru_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    wb.save(output_path)
    print(f"Report saved: {output_path}")
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="how many days back to include")
    parser.add_argument("--output", type=str, default=None, help="output .xlsx path")
    args = parser.parse_args()
    generate_report(days=args.days, output_path=args.output)
