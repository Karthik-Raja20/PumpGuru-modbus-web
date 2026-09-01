"""
PUMPGURU PDF Report Generator
Produces a formatted PDF report with:
    - Cover / summary page (uptime %, fault counts, key stats, phase imbalance)
    - Period trend charts (hourly/daily/weekly/monthly, rendered as images)
    - Measurement statistics table
    - Fault event log table

Uses the same period_aggregation module as the Excel report generator, so
the two output formats always agree with each other on every number.

Usage:
    python reports/pdf_report_generator.py --days 7 --granularity daily
    python reports/pdf_report_generator.py --since 2026-08-01 --until 2026-08-31 --granularity weekly
"""

import sys
import os
import argparse
import tempfile
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")  # headless rendering, no display needed
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

from core.data_logger import DataLogger
from config.register_map import MEASUREMENT_REGISTERS
from reports.period_aggregation import (
    aggregate_by_period, overall_stats, resolve_date_range, VALID_GRANULARITIES,
)

# ---- Brand palette (matches the web dashboard) ----
NAVY = colors.HexColor("#142433")
TEAL = colors.HexColor("#1C7293")
TEAL_DARK = colors.HexColor("#0F4C5C")
AMBER = colors.HexColor("#F2A541")
GREEN = colors.HexColor("#2E9E6D")
RED = colors.HexColor("#D9534F")
LIGHT_BG = colors.HexColor("#E8F1F5")
INK = colors.HexColor("#1A2530")
MUTED = colors.HexColor("#5C6B75")

MPL_VOLTAGE_COLORS = ["#1C7293", "#F2A541", "#2E9E6D"]
MPL_CURRENT_COLORS = ["#0F4C5C", "#D9534F", "#8B7FD6"]


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", fontSize=22, leading=26, textColor=NAVY, fontName="Helvetica-Bold", spaceAfter=4))
    styles.add(ParagraphStyle(name="ReportSubtitle", fontSize=11, leading=14, textColor=MUTED, fontName="Helvetica", spaceAfter=14))
    styles.add(ParagraphStyle(name="SectionHeading", fontSize=14, leading=18, textColor=NAVY, fontName="Helvetica-Bold", spaceBefore=16, spaceAfter=8))
    styles.add(ParagraphStyle(name="BodyMuted", fontSize=9.5, leading=13, textColor=MUTED, fontName="Helvetica"))
    return styles


def _render_period_chart(buckets, param_keys, title, y_label, tmpdir, filename):
    """Renders a matplotlib line chart of per-bucket averages for the given
    parameter keys, saved as a PNG for embedding in the PDF."""
    if not buckets or not param_keys:
        return None

    fig, ax = plt.subplots(figsize=(9.5, 3.4), dpi=150)
    labels = [b["label"] for b in buckets]
    colors_cycle = MPL_VOLTAGE_COLORS if "V" in y_label else MPL_CURRENT_COLORS

    any_series = False
    for i, key in enumerate(param_keys):
        series = [b["measurements"].get(key, {}).get("avg") for b in buckets]
        if any(v is not None for v in series):
            any_series = True
            label = MEASUREMENT_REGISTERS.get(key, {}).get("label", key)
            ax.plot(labels, series, marker="o", markersize=3, linewidth=1.8,
                     color=colors_cycle[i % len(colors_cycle)], label=label)

    if not any_series:
        plt.close(fig)
        return None

    ax.set_title(title, fontsize=11, color="#142433", fontweight="bold", loc="left")
    ax.set_ylabel(y_label, fontsize=9)
    ax.tick_params(axis="both", labelsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="-", linewidth=0.5, color="#e0e0e0")
    ax.legend(fontsize=8, frameon=False, loc="upper right")

    if len(labels) > 10:
        step = max(1, len(labels) // 10)
        for i, tick in enumerate(ax.get_xticklabels()):
            if i % step != 0:
                tick.set_visible(False)
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()

    path = os.path.join(tmpdir, filename)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _stat_table(headers, rows, col_widths=None, wrap_headers=False):
    header_style = ParagraphStyle(name="TableHeader", fontSize=7.5, leading=9, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_LEFT)
    if wrap_headers:
        header_row = [Paragraph(h, header_style) for h in headers]
    else:
        header_row = headers
    data = [header_row] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def generate_pdf_report(
    days: int = None,
    since: str = None,
    until: str = None,
    granularity: str = "daily",
    output_path: str = None,
):
    if granularity not in VALID_GRANULARITIES:
        raise ValueError(f"granularity must be one of {VALID_GRANULARITIES}")

    since_dt, until_dt = resolve_date_range(since=since, until=until, days=days, granularity=granularity)

    db = DataLogger()
    since_str = since_dt.isoformat(timespec="seconds")
    until_str = until_dt.isoformat(timespec="seconds")
    snapshots = db.get_snapshots(since=since_str, until=until_str)
    fault_events = db.get_fault_events(since=since_str, until=until_str)

    if not snapshots:
        print(f"No data found between {since_dt} and {until_dt}. Run core/poller.py first to collect data.")
        return None

    stats = overall_stats(snapshots, fault_events)
    agg = aggregate_by_period(snapshots, granularity=granularity)
    buckets = agg["buckets"]

    if output_path is None:
        default_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(default_dir, exist_ok=True)
        output_path = os.path.join(
            default_dir,
            f"pumpguru_report_{granularity}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
        )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    styles = _styles()
    story = []

    with tempfile.TemporaryDirectory() as tmpdir:
        # ---- Header ----
        story.append(Paragraph("PUMPGURU — Pump Protection Report", styles["ReportTitle"]))
        period_label = f"{since_dt.strftime('%Y-%m-%d %H:%M')} to {until_dt.strftime('%Y-%m-%d %H:%M')} &nbsp;&nbsp;|&nbsp;&nbsp; Granularity: {granularity.capitalize()}"
        story.append(Paragraph(period_label, styles["ReportSubtitle"]))
        story.append(Paragraph(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["BodyMuted"]))
        story.append(Spacer(1, 10))

        # ---- Summary stat cards (as a table for layout) ----
        uptime_display = f"{stats['uptime_pct']}%" if stats["uptime_pct"] is not None else "N/A"
        summary_rows = [
            ["Total Data Points", "Uptime", "Active Fault Events", "Voltage Imbalance", "Current Imbalance"],
            [
                str(stats["total_snapshots"]),
                uptime_display,
                str(stats["total_fault_events"]),
                f"{stats['voltage_imbalance_pct']}%" if stats["voltage_imbalance_pct"] is not None else "N/A",
                f"{stats['current_imbalance_pct']}%" if stats["current_imbalance_pct"] is not None else "N/A",
            ],
        ]
        summary_table = Table(summary_rows, colWidths=[1.31 * inch] * 5)
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("BACKGROUND", (0, 1), (-1, 1), LIGHT_BG),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 1), (-1, 1), 13),
            ("TEXTCOLOR", (0, 1), (-1, 1), TEAL_DARK),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 6))

        # ---- Measurement statistics table ----
        story.append(Paragraph("Measurement Analysis", styles["SectionHeading"]))
        meas_headers = ["Parameter", "Min", "Max", "Average", "Unit"]
        meas_rows = [
            [s["label"], f"{s['min']:.2f}", f"{s['max']:.2f}", f"{s['avg']:.2f}", s["unit"]]
            for s in stats["measurement_stats"].values()
        ]
        if meas_rows:
            story.append(_stat_table(meas_headers, meas_rows, col_widths=[2.2 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch]))
        else:
            story.append(Paragraph("No measurement data available for this period.", styles["BodyMuted"]))

        # ---- Fault frequency table ----
        story.append(Paragraph("Fault Frequency Breakdown", styles["SectionHeading"]))
        if stats["fault_counts"]:
            fault_rows = [[name, str(count)] for name, count in stats["fault_counts"].items()]
            story.append(_stat_table(["Fault Type", "Times Triggered"], fault_rows, col_widths=[4.4 * inch, 2 * inch]))
        else:
            story.append(Paragraph("No faults recorded in this period.", styles["BodyMuted"]))

        story.append(PageBreak())

        # ---- Period trend charts ----
        story.append(Paragraph(f"{granularity.capitalize()} Trend — Voltage &amp; Current (Average)", styles["SectionHeading"]))

        voltage_keys = [k for k, m in MEASUREMENT_REGISTERS.items() if m.get("unit") == "V"]
        current_keys = [k for k, m in MEASUREMENT_REGISTERS.items() if m.get("unit") == "A"]

        v_chart_path = _render_period_chart(buckets, voltage_keys, f"Voltage — {granularity.capitalize()} Average", "Volts", tmpdir, "voltage_chart.png")
        c_chart_path = _render_period_chart(buckets, current_keys, f"Current — {granularity.capitalize()} Average", "Amps", tmpdir, "current_chart.png")

        if v_chart_path:
            story.append(Image(v_chart_path, width=6.8 * inch, height=6.8 * inch * (3.4 / 9.5)))
            story.append(Spacer(1, 8))
        if c_chart_path:
            story.append(Image(c_chart_path, width=6.8 * inch, height=6.8 * inch * (3.4 / 9.5)))

        if not v_chart_path and not c_chart_path:
            story.append(Paragraph("Not enough data to render trend charts for this period.", styles["BodyMuted"]))

        # ---- Period breakdown table (min/max/avg per bucket) ----
        story.append(PageBreak())
        story.append(Paragraph(f"{granularity.capitalize()} Breakdown Table", styles["SectionHeading"]))
        if buckets:
            param_names = list(MEASUREMENT_REGISTERS.keys())
            # Short header labels (just the parameter label, no "(Avg)" suffix)
            # to avoid text overflow in narrow columns -- a single-row
            # "All values are period averages" note clarifies the units instead.
            headers = ["Period", "Pts"] + [MEASUREMENT_REGISTERS[p].get("label", p) for p in param_names]
            rows = []
            for b in buckets:
                row = [b["label"], str(b["count"])]
                for p in param_names:
                    m = b["measurements"].get(p)
                    row.append(f"{m['avg']:.1f}" if m else "--")
                rows.append(row)
            col_widths = [1.0 * inch, 0.4 * inch] + [0.9833 * inch] * len(param_names)
            story.append(Paragraph("All values shown are period averages.", styles["BodyMuted"]))
            story.append(Spacer(1, 4))
            table = _stat_table(headers, rows, col_widths=col_widths, wrap_headers=True)
            table.setStyle(TableStyle([("FONTSIZE", (0, 1), (-1, -1), 8)]))
            story.append(table)
        else:
            story.append(Paragraph("No data available for this period.", styles["BodyMuted"]))

        # ---- Fault event log ----
        if fault_events:
            story.append(PageBreak())
            story.append(Paragraph("Fault Event Log", styles["SectionHeading"]))
            log_rows = [[e["timestamp"], e["fault_name"], e["state"]] for e in fault_events]
            log_table = Table([["Timestamp", "Fault", "State"]] + log_rows, colWidths=[2.4 * inch, 2.4 * inch, 1.4 * inch], repeatRows=1)
            row_styles = [
                ("BACKGROUND", (0, 0), (-1, 0), TEAL),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
            for i, e in enumerate(fault_events, start=1):
                if e["state"] == "ACTIVE":
                    row_styles.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FFE5E3")))
            log_table.setStyle(TableStyle(row_styles))
            story.append(log_table)

        story.append(Spacer(1, 20))
        story.append(Paragraph("— End of Report —", styles["BodyMuted"]))

        doc = SimpleDocTemplate(
            output_path, pagesize=letter,
            topMargin=0.6 * inch, bottomMargin=0.6 * inch,
            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        )
        doc.build(story)

    print(f"PDF report saved: {output_path}")
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None, help="lookback window in days from now")
    parser.add_argument("--since", type=str, default=None, help="ISO date/datetime, e.g. 2026-08-01")
    parser.add_argument("--until", type=str, default=None, help="ISO date/datetime, e.g. 2026-08-31")
    parser.add_argument("--granularity", type=str, default="daily", choices=list(VALID_GRANULARITIES))
    parser.add_argument("--output", type=str, default=None, help="output .pdf path")
    args = parser.parse_args()
    generate_pdf_report(
        days=args.days, since=args.since, until=args.until,
        granularity=args.granularity, output_path=args.output,
    )
