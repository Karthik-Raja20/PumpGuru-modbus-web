"""
PUMPGURU PDF Report Generator — Comprehensive Fault Analysis Edition
================================================================================
Produces a multi-page PDF report:

    Page 1: Executive Dashboard & Health Score
            - Health Index badge, Key KPIs, Top 3 Actionable Alerts
    Page 2: Fault Frequency, Downtime & Timing Patterns
            - Fault breakdown table, hourly/day-of-week distribution charts
    Page 3: Root-Cause Incident Log
            - Every fault incident with start/clear time, duration, and the
              electrical/tank readings at the moment it tripped
    Page 4: Electrical & Parameter Analysis
            - Voltage/current statistics, trend charts, near-miss counts
    Page 5: Operational & Tank Behavior
            - Pump 1 vs Pump 2 runtime, start/stop cycles, tank sensor events

The Aventek logo appears in the header of every page (see branding.py).

Uses reports/period_aggregation.py for time-bucketed trends and
reports/fault_analytics.py for all fault/incident/health analytics, so the
Excel report (report_generator.py) always agrees with this PDF on every number.

--------------------------------------------------------------------------------
WHERE TO EDIT BRANDING (logo, company name, colors)
--------------------------------------------------------------------------------
Everything branding-related is imported from reports/branding.py — edit that
ONE file to change the logo, colors, or report title. Nothing in this file
should need manual editing for a rebrand.
================================================================================

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
from reportlab.lib import colors as rl_colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

from core.data_logger import DataLogger
from config.register_map import MEASUREMENT_REGISTERS, FAULT_REGISTERS
from reports.period_aggregation import (
    aggregate_by_period, overall_stats, resolve_date_range, VALID_GRANULARITIES,
)
from reports.fault_analytics import build_full_analytics
from reports import branding

# ---- FAULT_HELP_TEXT -----------------------------------------------------
# Remedy/troubleshooting guidance shown next to each fault in the report.
# This is the exact text you provided; edit here if wording changes.
FAULT_HELP_TEXT = {
    "dry_run": "Check the pump wire connection and water level. Also check the dry run % setting.",
    "overload": "Check if the pump is jammed, or has bush, bearing, or winding damage. Also check pump wiring and overload settings.",
    "stall_pump": "Check if the pump rotor is stuck, jammed, or facing excessive mechanical resistance — similar to an overload or blockage condition.",
    "phase_reverse": "Check the incoming supply wire sequence — verify the Red, Yellow, and Blue phase order is correct.",
    "undervoltage": "Check incoming supply voltage with a multimeter. R-Y, Y-B, B-R should read 360–480 VAC, and R-N, Y-N, B-N should read approx. 200–240 VAC.",
    "phase_loss": "Check incoming supply voltage with a multimeter. R-Y, Y-B, B-R should read 360–480 VAC, and R-N, Y-N, B-N should read approx. 200–240 VAC.",
    "overvoltage": "Check incoming supply voltage with a multimeter. R-Y, Y-B, B-R should read 360–480 VAC, and R-N, Y-N, B-N should read approx. 200–240 VAC.",
}

FAULT_LABELS = {key: meta.get("label", key) for key, meta in FAULT_REGISTERS.items()}

# ---- Brand palette (imported from branding.py -- edit that file to rebrand) ----
NAVY = rl_colors.HexColor(f"#{branding.COLOR_PRIMARY_DARK}")
TEAL = rl_colors.HexColor(f"#{branding.COLOR_PRIMARY}")
TEAL_DARK = rl_colors.HexColor(f"#{branding.COLOR_PRIMARY_DEEP}")
AMBER = rl_colors.HexColor(f"#{branding.COLOR_AMBER}")
GREEN = rl_colors.HexColor(f"#{branding.COLOR_GREEN}")
RED = rl_colors.HexColor(f"#{branding.COLOR_RED}")
LIGHT_BG = rl_colors.HexColor(f"#{branding.COLOR_LIGHT_BG}")
INK = rl_colors.HexColor(f"#{branding.COLOR_INK}")
MUTED = rl_colors.HexColor(f"#{branding.COLOR_MUTED}")

MPL_VOLTAGE_COLORS = [f"#{branding.COLOR_PRIMARY}", f"#{branding.COLOR_AMBER}", f"#{branding.COLOR_GREEN}"]
MPL_CURRENT_COLORS = [f"#{branding.COLOR_PRIMARY_DEEP}", f"#{branding.COLOR_RED}", "#8B7FD6"]

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", fontSize=20, leading=24, textColor=NAVY, fontName="Helvetica-Bold", spaceAfter=4))
    styles.add(ParagraphStyle(name="ReportSubtitle", fontSize=10.5, leading=14, textColor=MUTED, fontName="Helvetica", spaceAfter=10))
    styles.add(ParagraphStyle(name="SectionHeading", fontSize=13.5, leading=17, textColor=NAVY, fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=7))
    styles.add(ParagraphStyle(name="BodyMuted", fontSize=9.5, leading=13, textColor=MUTED, fontName="Helvetica"))
    styles.add(ParagraphStyle(name="BodyText2", fontSize=9.5, leading=13.5, textColor=INK, fontName="Helvetica"))
    styles.add(ParagraphStyle(name="RemedyText", fontSize=9, leading=12.5, textColor=INK, fontName="Helvetica-Oblique"))
    return styles


# ============================================================================
# Logo header — drawn on EVERY page via reportlab's onPage callback
# ============================================================================

def _draw_page_header(canvas, doc):
    """Called by reportlab for every single page. Draws the logo top-left
    and a thin brand rule, plus a page number bottom-right. This is how the
    logo 'reflects on all pages' rather than only the cover page."""
    canvas.saveState()
    page_w, page_h = letter

    if branding.LOGO_DARK_TEXT_PATH and os.path.exists(branding.LOGO_DARK_TEXT_PATH):
        logo_w = branding.PDF_LOGO_WIDTH_INCHES * inch
        # Preserve the logo's real aspect ratio rather than distorting it.
        from PIL import Image as PILImage
        with PILImage.open(branding.LOGO_DARK_TEXT_PATH) as pil_im:
            aspect = pil_im.height / pil_im.width
        logo_h = logo_w * aspect
        canvas.drawImage(
            branding.LOGO_DARK_TEXT_PATH,
            0.6 * inch, page_h - 0.55 * inch - logo_h,
            width=logo_w, height=logo_h,
            preserveAspectRatio=True, mask="auto",
        )

    # thin brand rule under the header area
    canvas.setStrokeColor(TEAL)
    canvas.setLineWidth(1.2)
    canvas.line(0.6 * inch, page_h - 0.85 * inch, page_w - 0.6 * inch, page_h - 0.85 * inch)

    # footer: page number + company name
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.6 * inch, 0.4 * inch, f"{branding.COMPANY_NAME} — {branding.REPORT_TITLE}")
    canvas.drawRightString(page_w - 0.6 * inch, 0.4 * inch, f"Page {doc.page}")

    canvas.restoreState()


# ============================================================================
# Chart rendering helpers
# ============================================================================

def _render_period_chart(buckets, param_keys, title, y_label, tmpdir, filename):
    if not buckets or not param_keys:
        return None
    fig, ax = plt.subplots(figsize=(9.5, 3.2), dpi=150)
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

    ax.set_title(title, fontsize=11, color=f"#{branding.COLOR_PRIMARY_DARK}", fontweight="bold", loc="left")
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


def _render_bar_chart(labels, values, title, y_label, tmpdir, filename, color=None):
    if not labels or not any(values):
        return None
    fig, ax = plt.subplots(figsize=(9.5, 3.0), dpi=150)
    ax.bar(labels, values, color=color or f"#{branding.COLOR_PRIMARY}")
    ax.set_title(title, fontsize=11, color=f"#{branding.COLOR_PRIMARY_DARK}", fontweight="bold", loc="left")
    ax.set_ylabel(y_label, fontsize=9)
    ax.tick_params(axis="both", labelsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="-", linewidth=0.5, color="#e0e0e0")
    plt.xticks(rotation=30 if len(labels) > 8 else 0, ha="right" if len(labels) > 8 else "center")
    plt.tight_layout()
    path = os.path.join(tmpdir, filename)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _stat_table(headers, rows, col_widths=None, wrap_headers=False, wrap_body_cols=None):
    header_style = ParagraphStyle(name="TableHeader", fontSize=7.5, leading=9, textColor=rl_colors.white, fontName="Helvetica-Bold", alignment=TA_LEFT)
    body_style = ParagraphStyle(name="TableBody", fontSize=7.8, leading=10, textColor=INK, fontName="Helvetica")

    header_row = [Paragraph(h, header_style) for h in headers] if wrap_headers else headers

    if wrap_body_cols:
        wrapped_rows = []
        for row in rows:
            new_row = list(row)
            for ci in wrap_body_cols:
                new_row[ci] = Paragraph(str(new_row[ci]), body_style)
            wrapped_rows.append(new_row)
        rows = wrapped_rows

    data = [header_row] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.white, LIGHT_BG]),
        ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _fmt_minutes(m):
    """Formats a minute count as e.g. '4h 12m' for readability."""
    if m is None:
        return "--"
    total_seconds_rounded = round(m * 60)
    total_minutes_rounded = total_seconds_rounded // 60
    h = int(total_minutes_rounded // 60)
    mm = int(total_minutes_rounded % 60)
    if h > 0:
        return f"{h}h {mm}m"
    return f"{mm}m" if m >= 1 else f"{m*60:.0f}s"


# ============================================================================
# PAGE 1: Executive Dashboard & Health Score
# ============================================================================

def _build_page1(story, styles, stats, analytics, since_dt, until_dt, granularity):
    story.append(Paragraph(branding.REPORT_TITLE, styles["ReportTitle"]))
    period_label = f"{since_dt.strftime('%Y-%m-%d %H:%M')} to {until_dt.strftime('%Y-%m-%d %H:%M')} &nbsp;|&nbsp; Granularity: {granularity.capitalize()}"
    story.append(Paragraph(period_label, styles["ReportSubtitle"]))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["BodyMuted"]))
    story.append(Spacer(1, 10))

    # ---- Health Score badge ----
    health = analytics["health"]
    score = health["score"]
    score_color = GREEN if score >= 75 else (AMBER if score >= 55 else RED)
    health_table = Table(
        [["System Health Index", f"{score:.0f} / 100", health["rating"]]],
        colWidths=[2.3 * inch, 1.6 * inch, 3.0 * inch],
    )
    health_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (0, 0), rl_colors.white),
        ("FONTNAME", (0, 0), (0, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (0, 0), 11),
        ("BACKGROUND", (1, 0), (1, 0), score_color),
        ("TEXTCOLOR", (1, 0), (1, 0), rl_colors.white),
        ("FONTNAME", (1, 0), (1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (1, 0), (1, 0), 16),
        ("BACKGROUND", (2, 0), (2, 0), LIGHT_BG),
        ("TEXTCOLOR", (2, 0), (2, 0), INK),
        ("FONTNAME", (2, 0), (2, 0), "Helvetica-Bold"),
        ("FONTSIZE", (2, 0), (2, 0), 11),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#cccccc")),
    ]))
    story.append(health_table)
    story.append(Spacer(1, 12))

    # ---- Key KPIs ----
    uptime_display = f"{stats['uptime_pct']}%" if stats["uptime_pct"] is not None else "N/A"
    total_incidents = sum(s["incident_count"] for s in analytics["duration_stats"].values())
    total_downtime = sum(s["total_downtime_minutes"] for s in analytics["duration_stats"].values())
    completed = [i for i in analytics["incidents"] if not i["ongoing"]]
    mttr = round(sum(i["duration_minutes"] for i in completed) / len(completed), 1) if completed else None

    run_min_1 = analytics["operational_stats"].get("pump1_total_run_minutes") or 0
    run_min_2 = analytics["operational_stats"].get("pump2_total_run_minutes") or 0

    kpi_rows = [
        ["Uptime", "Total Fault Incidents", "Total Downtime", "MTTR (avg clear time)", "Total Run Hours"],
        [
            uptime_display,
            str(total_incidents),
            _fmt_minutes(total_downtime),
            _fmt_minutes(mttr),
            f"{(run_min_1 + run_min_2) / 60:.1f} h",
        ],
    ]
    kpi_table = Table(kpi_rows, colWidths=[1.31 * inch] * 5)
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("BACKGROUND", (0, 1), (-1, 1), LIGHT_BG),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 12),
        ("TEXTCOLOR", (0, 1), (-1, 1), TEAL_DARK),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#cccccc")),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 14))

    # ---- Top 3 Actionable Alerts ----
    story.append(Paragraph("Top Priority Issues — What to Check First", styles["SectionHeading"]))
    top_issues = analytics["top_issues"]
    if not top_issues:
        story.append(Paragraph("No significant fault activity in this period. No action needed.", styles["BodyText2"]))
    else:
        for i, issue in enumerate(top_issues, start=1):
            label = FAULT_LABELS.get(issue["fault_name"], issue["fault_name"])
            badge_color = RED if i == 1 else (AMBER if i == 2 else TEAL)
            repeat_note = " (repeat offender — recurring cluster detected)" if issue["is_repeat_offender"] else ""
            row = Table(
                [[f"#{i}", f"{label} — {issue['incident_count']} incidents, {_fmt_minutes(issue['total_downtime_minutes'])} downtime{repeat_note}"]],
                colWidths=[0.4 * inch, 6.5 * inch],
            )
            row.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), badge_color),
                ("TEXTCOLOR", (0, 0), (0, 0), rl_colors.white),
                ("FONTNAME", (0, 0), (0, 0), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (0, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (1, 0), (1, 0), LIGHT_BG),
                ("FONTNAME", (1, 0), (1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (1, 0), (1, 0), 9.5),
                ("LEFTPADDING", (1, 0), (1, 0), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.append(row)
            story.append(Paragraph(f"Remedy: {issue['remedy']}", styles["RemedyText"]))
            story.append(Spacer(1, 6))

    if analytics["excluded_invalid_snapshots"] > 0:
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            f"Note: {analytics['excluded_invalid_snapshots']} snapshots in this period had an "
            f"unrecognized data format (likely from an earlier logging version) and were excluded "
            f"from fault statistics to keep counts accurate. Electrical readings from those "
            f"snapshots are unaffected.",
            styles["BodyMuted"],
        ))


# ============================================================================
# PAGE 2: Fault Frequency, Downtime & Timing Patterns
# ============================================================================

def _build_page2(story, styles, analytics, tmpdir):
    story.append(Paragraph("Fault Frequency & Downtime Breakdown", styles["SectionHeading"]))
    duration_stats = analytics["duration_stats"]
    if duration_stats:
        headers = ["Fault", "Cnt", "Downtime", "Avg Clear", "Longest", "Active", "Remedy"]
        rows = []
        for name, s in sorted(duration_stats.items(), key=lambda kv: kv[1]["total_downtime_minutes"], reverse=True):
            rows.append([
                FAULT_LABELS.get(name, name),
                str(s["incident_count"]),
                _fmt_minutes(s["total_downtime_minutes"]),
                _fmt_minutes(s["avg_duration_minutes"]),
                _fmt_minutes(s["longest_duration_minutes"]),
                str(s["ongoing_count"]) if s["ongoing_count"] else "--",
                FAULT_HELP_TEXT.get(name, ""),
            ])
        col_widths = [0.8 * inch, 0.5 * inch, 0.78 * inch, 0.78 * inch, 0.62 * inch, 0.58 * inch, 2.84 * inch]
        story.append(_stat_table(headers, rows, col_widths=col_widths, wrap_headers=True, wrap_body_cols=[6]))
    else:
        story.append(Paragraph("No fault incidents recorded in this period.", styles["BodyMuted"]))

    story.append(Spacer(1, 10))

    # ---- Repeat offenders / clusters ----
    clusters = analytics["clusters"]
    if clusters:
        story.append(Paragraph("Repeat-Offender Clusters (same fault firing repeatedly)", styles["SectionHeading"]))
        cluster_rows = [
            [FAULT_LABELS.get(c["fault_name"], c["fault_name"]), str(c["incident_count"]),
             c["window_start"].strftime("%Y-%m-%d %H:%M"), c["window_end"].strftime("%Y-%m-%d %H:%M")]
            for c in clusters[:10]
        ]
        story.append(_stat_table(
            ["Fault", "Occurrences", "Cluster Start", "Cluster End"],
            cluster_rows, col_widths=[1.8 * inch, 1.1 * inch, 1.9 * inch, 1.9 * inch],
        ))
        story.append(Paragraph(
            "A cluster indicates the same fault fired multiple times within a short window — "
            "usually a sign the underlying cause was not fully resolved between trips.",
            styles["BodyMuted"],
        ))

    story.append(PageBreak())

    # ---- Timing pattern charts ----
    story.append(Paragraph("Fault Timing Patterns", styles["SectionHeading"]))
    hourly = analytics["hourly_distribution"]
    hour_labels = [f"{h:02d}" for h in range(24)]
    hour_values = [hourly.get(h, 0) for h in range(24)]
    hourly_chart = _render_bar_chart(hour_labels, hour_values, "Fault Incidents by Hour of Day", "Incidents", tmpdir, "hourly.png")
    if hourly_chart:
        story.append(Image(hourly_chart, width=6.8 * inch, height=6.8 * inch * (3.0 / 9.5)))
        story.append(Spacer(1, 8))

    dow = analytics["day_of_week_distribution"]
    dow_values = [dow.get(d, 0) for d in DAY_ORDER]
    dow_labels = [d[:3] for d in DAY_ORDER]
    dow_chart = _render_bar_chart(dow_labels, dow_values, "Fault Incidents by Day of Week", "Incidents", tmpdir, "dow.png", color=f"#{branding.COLOR_PRIMARY_DEEP}")
    if dow_chart:
        story.append(Image(dow_chart, width=6.8 * inch, height=6.8 * inch * (3.0 / 9.5)))

    if not hourly_chart and not dow_chart:
        story.append(Paragraph("Not enough fault incidents in this period to show a timing pattern.", styles["BodyMuted"]))


# ============================================================================
# PAGE 3: Root-Cause Incident Log
# ============================================================================

def _build_page3(story, styles, analytics):
    story.append(Paragraph("Root-Cause Incident Log", styles["SectionHeading"]))
    story.append(Paragraph(
        "Every fault incident below is paired with the electrical/tank readings recorded "
        "closest to the moment it started, so you can see WHY it tripped, not just that it did.",
        styles["BodyMuted"],
    ))
    story.append(Spacer(1, 6))

    incidents = analytics["incidents"]
    if not incidents:
        story.append(Paragraph("No fault incidents recorded in this period.", styles["BodyMuted"]))
        return

    headers = ["Fault", "Started", "Cleared", "Duration", "Readings at Trip"]
    rows = []
    # Most-recent-first is generally more useful for a technician skimming
    # the report, so incidents are shown newest to oldest.
    for inc in sorted(incidents, key=lambda i: i["started_at"], reverse=True):
        snap = inc.get("snapshot_at_fault") or {}
        m = snap.get("measurements") or {}
        if m:
            reading_str = (
                f"V: {m.get('voltage_ry','--')}/{m.get('voltage_yb','--')}/{m.get('voltage_br','--')}  "
                f"I: {m.get('current_r','--')}/{m.get('current_y','--')}/{m.get('current_b','--')}"
            )
        else:
            reading_str = "No reading within 5 min of trip"

        rows.append([
            FAULT_LABELS.get(inc["fault_name"], inc["fault_name"]),
            inc["started_at"].strftime("%Y-%m-%d %H:%M:%S"),
            inc["cleared_at"].strftime("%Y-%m-%d %H:%M:%S") if inc["cleared_at"] else "ONGOING",
            _fmt_minutes(inc["duration_minutes"]) if inc["duration_minutes"] is not None else "--",
            reading_str,
        ])

    col_widths = [0.85 * inch, 1.35 * inch, 1.35 * inch, 0.65 * inch, 2.2 * inch]
    table = _stat_table(headers, rows, col_widths=col_widths, wrap_headers=True, wrap_body_cols=[4])
    story.append(table)


# ============================================================================
# PAGE 4: Electrical & Parameter Analysis
# ============================================================================

def _build_page4(story, styles, stats, analytics, buckets, granularity, tmpdir):
    story.append(Paragraph("Electrical Parameter Analysis", styles["SectionHeading"]))

    # Only show TRUE live electrical readings here (voltage/current phases),
    # not setpoints, tank sensors, or control codes -- those get their own
    # sections elsewhere in the report where they're meaningful. Mixing a
    # 0/1 binary tank sensor or a 180A overload SETPOINT into a "min/max/avg
    # electrical reading" table produces numbers that look like data but
    # mean nothing (and previously corrupted the imbalance % calculation --
    # see the fix in period_aggregation.py).
    electrical_keys = {
        k for k, m in MEASUREMENT_REGISTERS.items()
        if m.get("unit") in ("V", "A") and "set_" not in k
    }
    meas_headers = ["Parameter", "Min", "Max", "Average", "Unit"]
    meas_rows = [
        [s["label"], f"{s['min']:.2f}", f"{s['max']:.2f}", f"{s['avg']:.2f}", s["unit"]]
        for key, s in stats["measurement_stats"].items() if key in electrical_keys
    ]
    if meas_rows:
        story.append(_stat_table(meas_headers, meas_rows, col_widths=[2.2 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch]))
    else:
        story.append(Paragraph("No measurement data available for this period.", styles["BodyMuted"]))

    story.append(Spacer(1, 8))
    imb_rows = [
        ["Voltage Phase Imbalance", f"{stats['voltage_imbalance_pct']}%" if stats["voltage_imbalance_pct"] is not None else "N/A"],
        ["Current Phase Imbalance", f"{stats['current_imbalance_pct']}%" if stats["current_imbalance_pct"] is not None else "N/A"],
    ]
    story.append(_stat_table(["Metric", "Value"], imb_rows, col_widths=[3 * inch, 2 * inch]))

    # ---- Configured setpoints, shown separately from live readings so the
    # two are never confused with each other. ----
    setpoint_keys = {k for k, m in MEASUREMENT_REGISTERS.items() if "set_" in k}
    setpoint_rows = [
        [s["label"], f"{s['avg']:.2f}", s["unit"]]
        for key, s in stats["measurement_stats"].items() if key in setpoint_keys
    ]
    if setpoint_rows:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Configured Setpoints (period average — check Settings page for current live value)", styles["SectionHeading"]))
        story.append(_stat_table(["Setpoint", "Average Value", "Unit"], setpoint_rows, col_widths=[3.2 * inch, 1.9 * inch, 1.2 * inch]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Near-Miss Warning Counts (concerning but did not trip a fault)", styles["SectionHeading"]))
    nm = analytics["near_misses"]
    nm_rows = [
        [f"Voltage below {nm['voltage_warn_low_threshold']:.0f}V", str(nm["voltage_low_warnings"])],
        [f"Voltage above {nm['voltage_warn_high_threshold']:.0f}V", str(nm["voltage_high_warnings"])],
        ["Voltage phase imbalance warning-zone", str(nm["voltage_imbalance_warnings"])],
        ["Current phase imbalance warning-zone", str(nm["current_imbalance_warnings"])],
    ]
    story.append(_stat_table(["Near-Miss Type", "Occurrences"], nm_rows, col_widths=[4 * inch, 2 * inch]))
    story.append(Paragraph(
        "Near-misses are readings that entered a warning zone without triggering a hard fault — "
        "a frequent count here can be an early indicator of a developing problem.",
        styles["BodyMuted"],
    ))

    story.append(PageBreak())
    story.append(Paragraph(f"{granularity.capitalize()} Trend — Voltage & Current (Average)", styles["SectionHeading"]))
    voltage_keys = [k for k, m in MEASUREMENT_REGISTERS.items() if m.get("unit") == "V"]
    current_keys = [k for k, m in MEASUREMENT_REGISTERS.items() if m.get("unit") == "A"]
    v_chart = _render_period_chart(buckets, voltage_keys, f"Voltage — {granularity.capitalize()} Average", "Volts", tmpdir, "voltage_chart.png")
    c_chart = _render_period_chart(buckets, current_keys, f"Current — {granularity.capitalize()} Average", "Amps", tmpdir, "current_chart.png")
    if v_chart:
        story.append(Image(v_chart, width=6.8 * inch, height=6.8 * inch * (3.2 / 9.5)))
        story.append(Spacer(1, 8))
    if c_chart:
        story.append(Image(c_chart, width=6.8 * inch, height=6.8 * inch * (3.2 / 9.5)))
    if not v_chart and not c_chart:
        story.append(Paragraph("Not enough data to render trend charts for this period.", styles["BodyMuted"]))


# ============================================================================
# PAGE 5: Operational & Tank Behavior
# ============================================================================

def _build_page5(story, styles, analytics):
    story.append(Paragraph("Operational & Duty Cycle Analysis", styles["SectionHeading"]))
    ops = analytics["operational_stats"]
    op_rows = [
        ["Pump 1 — Total Run Time", _fmt_minutes(ops.get("pump1_total_run_minutes"))],
        ["Pump 2 — Total Run Time", _fmt_minutes(ops.get("pump2_total_run_minutes"))],
        ["Estimated Start/Stop Cycles", str(ops.get("start_stop_cycles_estimated", "--"))],
    ]
    story.append(_stat_table(["Metric", "Value"], op_rows, col_widths=[3.5 * inch, 2.5 * inch]))

    mode_counts = ops.get("mode_value_counts", {})
    if mode_counts:
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            "Operation Mode Distribution (raw control_auto_manual register values — "
            "device-specific codes, see Settings page for your device's mode mapping)",
            styles["BodyMuted"],
        ))
        mode_rows = [[str(k), str(v)] for k, v in sorted(mode_counts.items(), key=lambda kv: -kv[1])]
        story.append(_stat_table(["Mode Value", "Snapshot Count"], mode_rows, col_widths=[3 * inch, 2 * inch]))

    story.append(Spacer(1, 12))
    story.append(Paragraph("Tank Sensor Behavior", styles["SectionHeading"]))
    tanks = analytics["tank_stats"]
    tank_rows = [
        ["Bottom Tank — Low Sensor Triggered", str(tanks["sensor_triggered_counts"].get("tank_bottom_low", 0))],
        ["Bottom Tank — High Sensor Triggered", str(tanks["sensor_triggered_counts"].get("tank_bottom_high", 0))],
        ["Top Tank — Low Sensor Triggered", str(tanks["sensor_triggered_counts"].get("tank_top_low", 0))],
        ["Top Tank — High Sensor Triggered", str(tanks["sensor_triggered_counts"].get("tank_top_high", 0))],
    ]
    story.append(_stat_table(["Sensor Event", "Times Triggered"], tank_rows, col_widths=[3.5 * inch, 2.5 * inch]))

    if tanks.get("dry_run_bottom_low_correlation_pct") is not None:
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            f"Dry Run &amp; Bottom Tank Correlation: {tanks['dry_run_bottom_low_correlation_pct']}% of Dry Run "
            f"fault snapshots ({tanks['dry_run_with_bottom_tank_low']} of {tanks['dry_run_snapshots']}) occurred "
            f"while the Bottom Tank Low sensor was also triggered — "
            + ("a strong correlation, supporting a genuine low-water condition." if tanks['dry_run_bottom_low_correlation_pct'] > 60
               else "a weak correlation, worth investigating other causes (wiring, sensor calibration) for the remaining incidents."),
            styles["BodyText2"],
        ))


# ============================================================================
# Main entry point
# ============================================================================

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
    analytics = build_full_analytics(snapshots, fault_events, FAULT_HELP_TEXT)

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
        _build_page1(story, styles, stats, analytics, since_dt, until_dt, granularity)
        story.append(PageBreak())
        _build_page2(story, styles, analytics, tmpdir)
        story.append(PageBreak())
        _build_page3(story, styles, analytics)
        story.append(PageBreak())
        _build_page4(story, styles, stats, analytics, buckets, granularity, tmpdir)
        story.append(PageBreak())
        _build_page5(story, styles, analytics)

        story.append(Spacer(1, 20))
        story.append(Paragraph("— End of Report —", styles["BodyMuted"]))

        # topMargin is larger than the old version to leave room for the
        # logo header drawn by _draw_page_header() on every page.
        doc = SimpleDocTemplate(
            output_path, pagesize=letter,
            topMargin=1.05 * inch, bottomMargin=0.65 * inch,
            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        )
        doc.build(story, onFirstPage=_draw_page_header, onLaterPages=_draw_page_header)

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
