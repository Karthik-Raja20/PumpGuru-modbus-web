"""
PUMPGURU Fault Analytics Engine

Turns the raw fault_events log + snapshots history into the numbers a human
actually wants from a report: how long did each fault last, what caused it,
is it getting worse, and what should be checked first.

Used by BOTH report_generator.py (Excel) and pdf_report_generator.py (PDF)
so the two output formats never disagree with each other -- exactly the
same pattern as period_aggregation.py.

--------------------------------------------------------------------------
DATA QUALITY NOTE (important):
--------------------------------------------------------------------------
Real fault_events logs can contain duplicate consecutive states, e.g.:
    ACTIVE, ACTIVE, CLEARED     (double-fire, no state actually changed)
    CLEARED, CLEARED            (orphan clear with nothing open)
This happens from comms blips, simulate mode, or a fault re-triggering
before the previous transition was flushed. The incident-pairing logic
below is deliberately state-aware (tracks one "open incident" per fault
name) so it silently absorbs this noise instead of producing negative
durations or crashing.
--------------------------------------------------------------------------

Usage:
    from reports.fault_analytics import run_fault_analytics
    analytics = run_fault_analytics(snapshots, fault_events, since_dt, until_dt)
"""

import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from bisect import bisect_right

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.register_map import MEASUREMENT_REGISTERS, FAULT_REGISTERS

try:
    from config.register_map import FAULT_HELP_TEXT
except ImportError:
    FAULT_HELP_TEXT = {}

# --------------------------------------------------------------------------- #
# Tunable thresholds -- edit these if your site's normal operating range
# differs. Everything below reads from these constants, same pattern as
# VOLTAGE_SCALE / CURRENT_SCALE in register_map.py.
# --------------------------------------------------------------------------- #

# A fault occurring again within this many hours of a previous occurrence of
# the SAME fault type is flagged as part of a "repeat offender" cluster.
REPEAT_OFFENDER_WINDOW_HOURS = 2
# Minimum incidents in a window to count as a cluster worth flagging.
REPEAT_OFFENDER_MIN_COUNT = 3

# Near-miss voltage band (line-to-line). Readings outside the "normal" band
# but not extreme enough to have tripped a real Over/Under Voltage fault are
# counted as near-misses -- an early warning before a fault actually fires.
# Defaults follow the panel's own guidance: 360-480 VAC is acceptable line-line.
NEAR_MISS_VOLTAGE_LOW = 380.0
NEAR_MISS_VOLTAGE_HIGH = 460.0
NEAR_MISS_VOLTAGE_HARD_LOW = 360.0   # below this, treat as a real fault-level dip
NEAR_MISS_VOLTAGE_HARD_HIGH = 480.0  # above this, treat as a real fault-level surge

# Health score weights (out of 100, deducted from a perfect 100).
HEALTH_WEIGHT_UPTIME = 40      # weight given to fault-free uptime %
HEALTH_WEIGHT_FREQUENCY = 25   # weight given to fault event frequency
HEALTH_WEIGHT_DOWNTIME = 20    # weight given to total downtime duration
HEALTH_WEIGHT_REPEATS = 15     # weight given to repeat-offender clusters


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _parse_ts(ts_str: str) -> datetime:
    return datetime.fromisoformat(ts_str)


def _fault_label(fault_name: str) -> str:
    """Human-readable label, falling back to the raw key if not configured."""
    meta = FAULT_REGISTERS.get(fault_name)
    if meta:
        return meta.get("label", fault_name)
    return fault_name.replace("_", " ").title()


# --------------------------------------------------------------------------- #
# 1. Incident pairing (ACTIVE -> CLEARED) with noise tolerance
# --------------------------------------------------------------------------- #

def pair_fault_incidents(fault_events: list, until_dt: datetime) -> list:
    """Turns the flat ACTIVE/CLEARED event log into discrete incidents:
        {
          "fault_name": "dry_run",
          "label": "Dry Run",
          "start": datetime(...),
          "end": datetime(...),
          "duration_minutes": 4.2,
          "ongoing": False,
        }

    State-aware per fault type: duplicate ACTIVEs are ignored (incident stays
    open), orphan CLEAREDs with nothing open are ignored. Any incident still
    open at `until_dt` (no matching CLEARED in range) is closed at `until_dt`
    and marked ongoing=True so it still counts toward downtime.
    """
    events_sorted = sorted(fault_events, key=lambda e: e["timestamp"])
    open_incidents = {}   # fault_name -> start datetime
    incidents = []

    for e in events_sorted:
        try:
            ts = _parse_ts(e["timestamp"])
        except Exception:
            continue
        name = e["fault_name"]
        state = e["state"]

        if state == "ACTIVE":
            if name not in open_incidents:
                open_incidents[name] = ts
            # else: duplicate ACTIVE while already open -- ignore, noise
        elif state == "CLEARED":
            if name in open_incidents:
                start = open_incidents.pop(name)
                duration_min = round((ts - start).total_seconds() / 60.0, 2)
                incidents.append({
                    "fault_name": name,
                    "label": _fault_label(name),
                    "start": start,
                    "end": ts,
                    "duration_minutes": duration_min,
                    "ongoing": False,
                })
            # else: orphan CLEARED with nothing open -- ignore, noise

    # Anything still open at the end of the range is an ongoing incident.
    for name, start in open_incidents.items():
        duration_min = round((until_dt - start).total_seconds() / 60.0, 2)
        incidents.append({
            "fault_name": name,
            "label": _fault_label(name),
            "start": start,
            "end": until_dt,
            "duration_minutes": duration_min,
            "ongoing": True,
        })

    incidents.sort(key=lambda i: i["start"])
    return incidents


# --------------------------------------------------------------------------- #
# 2. Repeat-offender clustering
# --------------------------------------------------------------------------- #

def detect_repeat_offenders(incidents: list,
                             window_hours: float = REPEAT_OFFENDER_WINDOW_HOURS,
                             min_count: int = REPEAT_OFFENDER_MIN_COUNT) -> list:
    """Groups incidents of the SAME fault type that recur within a rolling
    window and flags clusters with at least `min_count` occurrences -- this
    usually means "not actually fixed" rather than "one-off event".

    Returns a list of clusters:
        {"fault_name": ..., "label": ..., "count": 4,
         "window_start": dt, "window_end": dt}
    """
    by_fault = defaultdict(list)
    for inc in incidents:
        by_fault[inc["fault_name"]].append(inc["start"])

    clusters = []
    window = timedelta(hours=window_hours)

    for name, starts in by_fault.items():
        starts = sorted(starts)
        i = 0
        n = len(starts)
        while i < n:
            j = i
            while j + 1 < n and starts[j + 1] - starts[i] <= window:
                j += 1
            count = j - i + 1
            if count >= min_count:
                clusters.append({
                    "fault_name": name,
                    "label": _fault_label(name),
                    "count": count,
                    "window_start": starts[i],
                    "window_end": starts[j],
                })
                i = j + 1  # don't overlap the next cluster with this one
            else:
                i += 1

    clusters.sort(key=lambda c: c["window_start"])
    return clusters


# --------------------------------------------------------------------------- #
# 3. Root-cause correlation -- attach electrical/tank readings to incidents
# --------------------------------------------------------------------------- #

def correlate_incidents(incidents: list, snapshots: list) -> list:
    """For each incident, finds the snapshot at-or-just-before the incident's
    start time and attaches its measurements + tank states, so the report can
    show WHY a fault tripped, not just THAT it tripped.

    Uses binary search over pre-sorted snapshot timestamps for performance,
    since a 30-day / 5-second-interval history can be ~500,000 rows.
    """
    if not snapshots:
        for inc in incidents:
            inc["trip_reading"] = None
        return incidents

    snap_sorted = sorted(snapshots, key=lambda s: s["timestamp"])
    snap_ts = [_parse_ts(s["timestamp"]) for s in snap_sorted]

    tank_keys = [k for k in MEASUREMENT_REGISTERS if k.startswith("tank_")]
    voltage_keys = [k for k in MEASUREMENT_REGISTERS if k.startswith("voltage_")]
    current_keys = [k for k in MEASUREMENT_REGISTERS if k.startswith("current_")]

    for inc in incidents:
        idx = bisect_right(snap_ts, inc["start"]) - 1
        if idx < 0:
            inc["trip_reading"] = None
            continue
        snap = snap_sorted[idx]
        m = snap.get("measurements", {})
        inc["trip_reading"] = {
            "timestamp": snap["timestamp"],
            "voltages": {k: m.get(k) for k in voltage_keys},
            "currents": {k: m.get(k) for k in current_keys},
            "tanks": {k: m.get(k) for k in tank_keys},
            "set_current_1": m.get("set_current_1"),
            "set_current_2": m.get("set_current_2"),
            "set_dry_current": m.get("set_dry_current"),
        }

    return incidents


# --------------------------------------------------------------------------- #
# 4. Timing pattern distribution
# --------------------------------------------------------------------------- #

def timing_distribution(incidents: list) -> dict:
    """Hourly (0-23) and day-of-week (Mon-Sun) counts of incident START times,
    so the report can show e.g. 'Undervoltage clusters overnight' or
    'Overload mostly happens on Mondays'."""
    hourly = Counter()
    day_of_week = Counter()
    for inc in incidents:
        hourly[inc["start"].hour] += 1
        day_of_week[inc["start"].strftime("%A")] += 1

    days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return {
        "hourly": {h: hourly.get(h, 0) for h in range(24)},
        "day_of_week": {d: day_of_week.get(d, 0) for d in days_order},
    }


# --------------------------------------------------------------------------- #
# 5. Downtime / MTTR per fault type
# --------------------------------------------------------------------------- #

def downtime_by_fault(incidents: list) -> dict:
    """Per fault type: count, total downtime minutes, longest single incident,
    average time-to-clear (MTTR). Sorted by total downtime descending, since
    that's usually the more actionable ranking than raw count."""
    grouped = defaultdict(list)
    for inc in incidents:
        grouped[inc["fault_name"]].append(inc)

    result = {}
    for name, incs in grouped.items():
        durations = [i["duration_minutes"] for i in incs]
        longest = max(incs, key=lambda i: i["duration_minutes"])
        result[name] = {
            "label": _fault_label(name),
            "count": len(incs),
            "total_downtime_minutes": round(sum(durations), 2),
            "longest_duration_minutes": round(longest["duration_minutes"], 2),
            "longest_start": longest["start"],
            "avg_clear_minutes": round(sum(durations) / len(durations), 2),
            "remedy": FAULT_HELP_TEXT.get(name, ""),
            "ongoing_count": sum(1 for i in incs if i["ongoing"]),
        }

    return dict(sorted(result.items(), key=lambda kv: kv[1]["total_downtime_minutes"], reverse=True))


# --------------------------------------------------------------------------- #
# 6. Near-miss detection (voltage excursions that didn't quite trip a fault)
# --------------------------------------------------------------------------- #

def detect_near_misses(snapshots: list) -> dict:
    """Counts snapshots where a voltage reading sits in the 'warning zone'
    (outside normal, inside the hard fault threshold) while NO fault was
    active at that moment -- an early signal before a real trip happens."""
    voltage_keys = [k for k in MEASUREMENT_REGISTERS if k.startswith("voltage_")]
    low_count = 0
    high_count = 0
    examples = []

    for s in snapshots:
        faults = s.get("faults", {})
        if any(v is True for v in faults.values()):
            continue  # already a real fault at this moment, not a "near miss"
        m = s.get("measurements", {})
        for k in voltage_keys:
            val = m.get(k)
            if val is None:
                continue
            if NEAR_MISS_VOLTAGE_HARD_LOW < val < NEAR_MISS_VOLTAGE_LOW:
                low_count += 1
                if len(examples) < 10:
                    examples.append({"timestamp": s["timestamp"], "register": k, "value": val, "type": "low"})
            elif NEAR_MISS_VOLTAGE_HIGH < val < NEAR_MISS_VOLTAGE_HARD_HIGH:
                high_count += 1
                if len(examples) < 10:
                    examples.append({"timestamp": s["timestamp"], "register": k, "value": val, "type": "high"})

    return {
        "low_voltage_near_misses": low_count,
        "high_voltage_near_misses": high_count,
        "total_near_misses": low_count + high_count,
        "examples": examples,
    }


# --------------------------------------------------------------------------- #
# 7. Operational & duty metrics (runtime, cycling, mode usage, tank events)
# --------------------------------------------------------------------------- #

def operational_metrics(snapshots: list) -> dict:
    """Pump runtime, start/stop cycling, Auto/Manual split, pump-selection
    split, and tank sensor trigger counts over the period.

    IMPORTANT ASSUMPTIONS (flagged explicitly so the report never silently
    shows a wrong number):
      - "run_min_pump1_total" / "run_min_pump2_total" are treated as
        monotonically increasing cumulative counters. Total runtime for the
        period = last_value - first_value. If the counter ever resets
        (device reboot, overflow) this will under-report -- we detect and
        flag that case rather than showing a negative number.
      - control_auto_manual / control_pump_selection have NO CONFIRMED value
        mapping in register_map.py yet (e.g. your real data shows a constant
        raw value of 102, not an obvious 0/1). Rather than invent a label,
        this returns the raw distinct values with their counts, and a flag
        telling the report to show "(raw value, unconfirmed mapping)".
    """
    if not snapshots:
        return {"available": False}

    snap_sorted = sorted(snapshots, key=lambda s: s["timestamp"])

    def _series(key):
        return [(s["timestamp"], s.get("measurements", {}).get(key)) for s in snap_sorted]

    # --- Runtime totals (delta of cumulative counter across the period) ---
    runtime = {}
    for pump, key in (("pump1", "run_min_pump1_total"), ("pump2", "run_min_pump2_total")):
        vals = [v for _, v in _series(key) if v is not None]
        if len(vals) >= 2:
            delta = vals[-1] - vals[0]
            counter_reset_detected = delta < 0
            runtime[pump] = {
                "total_run_minutes": None if counter_reset_detected else round(delta, 1),
                "counter_reset_detected": counter_reset_detected,
                "first_reading": vals[0],
                "last_reading": vals[-1],
            }
        else:
            runtime[pump] = {"total_run_minutes": None, "counter_reset_detected": False,
                              "first_reading": None, "last_reading": None}

    # --- Start/stop cycle count (transitions of control_run_stop) ---
    run_stop_series = [v for _, v in _series("control_run_stop") if v is not None]
    cycles = 0
    for i in range(1, len(run_stop_series)):
        if run_stop_series[i] != run_stop_series[i - 1]:
            cycles += 1
    # A "cycle" is typically counted as one full stop->run transition, so
    # divide raw transition count by 2 (rounded up) if it looks like paired
    # transitions; report the raw transition count too for transparency.
    start_stop_cycles = {
        "raw_transitions": cycles,
        "estimated_cycles": (cycles + 1) // 2,
    }

    # --- Auto/Manual + Pump Selection: raw distinct values, mapping unconfirmed ---
    def _distribution(key):
        vals = [v for _, v in _series(key) if v is not None]
        counts = Counter(vals)
        total = sum(counts.values())
        return {
            "mapping_confirmed": False,
            "raw_value_counts": dict(counts),
            "raw_value_pct": {k: round(100 * c / total, 1) for k, c in counts.items()} if total else {},
        }

    auto_manual = _distribution("control_auto_manual")
    pump_selection = _distribution("control_pump_selection")

    # --- Tank sensor trigger counts (0->1 transitions per sensor) ---
    tank_keys = [k for k in MEASUREMENT_REGISTERS if k.startswith("tank_")]
    tank_events = {}
    for key in tank_keys:
        vals = [v for _, v in _series(key) if v is not None]
        triggers = sum(1 for i in range(1, len(vals)) if vals[i] == 1 and vals[i - 1] == 0)
        tank_events[key] = {
            "label": MEASUREMENT_REGISTERS[key].get("label", key),
            "trigger_count": triggers,
        }

    return {
        "available": True,
        "runtime": runtime,
        "start_stop_cycles": start_stop_cycles,
        "auto_manual_distribution": auto_manual,
        "pump_selection_distribution": pump_selection,
        "tank_events": tank_events,
    }


# --------------------------------------------------------------------------- #
# 8. Health score + top priority issues
# --------------------------------------------------------------------------- #

def compute_health_score(total_snapshots: int, faulted_snapshots: int,
                          downtime_summary: dict, repeat_clusters: list,
                          total_period_minutes: float) -> dict:
    """A single 0-100 index blending uptime, fault frequency, total downtime,
    and repeat-offender behavior. Deliberately simple/transparent (linear
    deductions, no black-box weighting) so it's explainable to a customer,
    not just a number pulled from nowhere.
    """
    if total_snapshots == 0:
        return {"score": None, "grade": "No Data"}

    uptime_pct = 100 * (1 - faulted_snapshots / total_snapshots)
    uptime_deduction = HEALTH_WEIGHT_UPTIME * (1 - uptime_pct / 100)

    total_events = sum(f["count"] for f in downtime_summary.values())
    # Normalize: 1 event/day is "acceptable", scale deduction from there.
    days = max(total_period_minutes / 1440, 1)
    events_per_day = total_events / days
    frequency_deduction = min(HEALTH_WEIGHT_FREQUENCY, HEALTH_WEIGHT_FREQUENCY * (events_per_day / 5))

    total_downtime_min = sum(f["total_downtime_minutes"] for f in downtime_summary.values())
    downtime_pct_of_period = (total_downtime_min / total_period_minutes) if total_period_minutes else 0
    downtime_deduction = min(HEALTH_WEIGHT_DOWNTIME, HEALTH_WEIGHT_DOWNTIME * (downtime_pct_of_period / 0.05))

    repeat_deduction = min(HEALTH_WEIGHT_REPEATS, HEALTH_WEIGHT_REPEATS * (len(repeat_clusters) / 3))

    score = max(0, round(100 - uptime_deduction - frequency_deduction - downtime_deduction - repeat_deduction))

    if score >= 90:
        grade = "Excellent"
    elif score >= 75:
        grade = "Good"
    elif score >= 50:
        grade = "Fair — needs attention"
    else:
        grade = "Poor — immediate attention recommended"

    return {"score": score, "grade": grade, "uptime_pct": round(uptime_pct, 2)}


def top_priority_issues(downtime_summary: dict, n: int = 3) -> list:
    """Top N fault types ranked by total downtime, each with its remedy text
    pulled straight from FAULT_HELP_TEXT -- ready to print directly in the
    'What to check first' section of the report."""
    ranked = list(downtime_summary.items())[:n]  # already sorted by downtime desc
    return [
        {
            "fault_name": name,
            "label": data["label"],
            "count": data["count"],
            "total_downtime_minutes": data["total_downtime_minutes"],
            "remedy": data["remedy"] or "No remedy guidance configured for this fault.",
        }
        for name, data in ranked
    ]


# --------------------------------------------------------------------------- #
# Master entry point
# --------------------------------------------------------------------------- #

def run_fault_analytics(snapshots: list, fault_events: list,
                         since_dt: datetime, until_dt: datetime) -> dict:
    """Runs the full analytics pipeline and returns everything both the
    Excel and PDF report generators need, in one consistent structure.
    """
    incidents = pair_fault_incidents(fault_events, until_dt)
    incidents = correlate_incidents(incidents, snapshots)
    repeat_clusters = detect_repeat_offenders(incidents)
    timing = timing_distribution(incidents)
    downtime_summary = downtime_by_fault(incidents)
    near_misses = detect_near_misses(snapshots)

    total_snapshots = len(snapshots)
    faulted_snapshots = sum(1 for s in snapshots if any(s.get("faults", {}).values()))
    total_period_minutes = max((until_dt - since_dt).total_seconds() / 60.0, 1)

    health = compute_health_score(
        total_snapshots, faulted_snapshots, downtime_summary,
        repeat_clusters, total_period_minutes,
    )
    top_issues = top_priority_issues(downtime_summary, n=3)
    operational = operational_metrics(snapshots)

    return {
        "period": {"since": since_dt, "until": until_dt, "minutes": total_period_minutes},
        "incidents": incidents,
        "downtime_by_fault": downtime_summary,
        "repeat_offender_clusters": repeat_clusters,
        "timing_distribution": timing,
        "near_misses": near_misses,
        "health": health,
        "top_priority_issues": top_issues,
        "operational": operational,
        "total_incidents": len(incidents),
        "ongoing_incidents": sum(1 for i in incidents if i["ongoing"]),
    }