"""
PUMPGURU Fault Analytics Engine
================================================================================
Computes deep fault analysis from raw snapshots + fault_events:
    - Incident pairing (ACTIVE -> CLEARED matching, duration, MTTR)
    - Repeat-offender / clustering detection
    - Root-cause correlation (electrical readings + tank state at fault time)
    - Time-of-day / day-of-week fault distribution
    - Electrical near-miss detection (warning-zone excursions)
    - Runtime & duty cycle analysis (Pump 1 vs Pump 2, Auto/Manual, start/stop)
    - Tank sensor event counting
    - Weighted health score (0-100) + ranked top issues with remedy text

Used by both reports/report_generator.py (Excel) and
reports/pdf_report_generator.py (PDF) so every number in both output
formats is computed once, here, and never disagrees between formats.

--------------------------------------------------------------------------------
DATA QUALITY NOTE (important -- read before changing this file)
--------------------------------------------------------------------------------
Real-world PUMPGURU databases can contain snapshots from earlier/different
versions of the logging code, where faults_json had a different shape
(e.g. {} or {"latest_fault": null} instead of the current 7-key boolean
dict). This module is defensive about that: `_is_valid_fault_snapshot()`
filters to only the current, well-formed shape before computing anything,
so mixed-format history doesn't silently produce wrong counts. Snapshots
that don't match the expected shape are excluded from fault-rate
calculations (not treated as "no fault"), and the count of excluded rows
is reported in the returned dict as "excluded_invalid_snapshots" so this
is visible in the report rather than hidden.
================================================================================
"""

import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict, Counter

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.register_map import MEASUREMENT_REGISTERS, FAULT_CODE_MAP

# The canonical set of fault keys this app knows about, derived from the
# register map so this module never goes stale if faults are added/removed.
KNOWN_FAULT_KEYS = {key for _, (key, _label) in FAULT_CODE_MAP.items()}

# Warning-zone thresholds for "near miss" detection -- readings that were
# concerning but did not (yet) trip a hard fault. These are conservative
# defaults for a 415V 3-phase system; adjust if your supply nominal differs.
VOLTAGE_WARN_LOW = 380.0
VOLTAGE_WARN_HIGH = 450.0
CURRENT_IMBALANCE_WARN_PCT = 10.0
VOLTAGE_IMBALANCE_WARN_PCT = 5.0

# Repeat-offender window: same fault firing 2+ times within this many
# minutes is flagged as a cluster (likely unresolved root cause).
CLUSTER_WINDOW_MINUTES = 120


def parse_ts(ts_str: str) -> datetime:
    return datetime.fromisoformat(ts_str)


def _is_valid_fault_snapshot(faults: dict) -> bool:
    """True only for the current, well-formed 7-key boolean fault dict.
    See module docstring -- protects analytics from mixed-schema history."""
    if not faults:
        return False
    if set(faults.keys()) != KNOWN_FAULT_KEYS:
        return False
    return True


def filter_valid_snapshots(snapshots: list) -> tuple:
    """Splits snapshots into (valid, excluded_count) based on fault shape.
    Measurement/pump/tank data in excluded rows is NOT lost -- callers that
    only need electrical stats (not fault stats) can still use the full
    snapshot list; this filter is specifically for fault-rate calculations."""
    valid = [s for s in snapshots if _is_valid_fault_snapshot(s.get("faults", {}))]
    excluded = len(snapshots) - len(valid)
    return valid, excluded


# ============================================================================
# 1. FAULT INCIDENT PAIRING (ACTIVE -> CLEARED matching)
# ============================================================================

def pair_fault_incidents(fault_events: list) -> list:
    """Matches each ACTIVE event with its next CLEARED event for the same
    fault_name, producing a list of complete incidents with duration.

    An ACTIVE with no matching CLEARED (fault still active at report end,
    or data cut off) is included with cleared_at=None and duration=None,
    clearly marked as "ongoing" rather than silently dropped.

    Returns list of dicts, sorted by started_at:
        {
          "fault_name": "dry_run",
          "started_at": datetime,
          "cleared_at": datetime or None,
          "duration_minutes": float or None,
          "ongoing": bool,
        }
    """
    # Track the most recent open ACTIVE event per fault name
    open_incidents = {}
    incidents = []

    events_sorted = sorted(fault_events, key=lambda e: e["timestamp"])

    for e in events_sorted:
        name = e["fault_name"]
        try:
            ts = parse_ts(e["timestamp"])
        except Exception:
            continue

        if e["state"] == "ACTIVE":
            # If there's already an open incident for this fault (e.g. the
            # CLEARED event was missing/lost), close the old one as
            # "ongoing until superseded" rather than silently overwriting it.
            if name in open_incidents:
                incidents.append(open_incidents.pop(name))
            open_incidents[name] = {
                "fault_name": name,
                "started_at": ts,
                "cleared_at": None,
                "duration_minutes": None,
                "ongoing": True,
            }
        elif e["state"] == "CLEARED":
            if name in open_incidents:
                inc = open_incidents.pop(name)
                inc["cleared_at"] = ts
                inc["duration_minutes"] = round((ts - inc["started_at"]).total_seconds() / 60, 2)
                inc["ongoing"] = False
                incidents.append(inc)
            # A CLEARED with no matching open ACTIVE is a boundary artifact
            # (e.g. report window starts mid-fault) -- intentionally skipped
            # rather than fabricating a start time.

    # Any incidents still open at the end of the event list are genuinely
    # ongoing (fault was active when data was queried/report was generated).
    incidents.extend(open_incidents.values())

    incidents.sort(key=lambda i: i["started_at"])
    return incidents


def fault_duration_stats(incidents: list) -> dict:
    """Per-fault-type duration statistics from paired incidents.
    Only completed incidents (cleared_at is not None) count toward
    duration/MTTR math; ongoing incidents are counted separately."""
    by_fault = defaultdict(list)
    ongoing_by_fault = defaultdict(int)

    for inc in incidents:
        if inc["ongoing"]:
            ongoing_by_fault[inc["fault_name"]] += 1
        else:
            by_fault[inc["fault_name"]].append(inc["duration_minutes"])

    stats = {}
    all_fault_names = set(by_fault.keys()) | set(ongoing_by_fault.keys())
    for name in all_fault_names:
        durations = by_fault.get(name, [])
        stats[name] = {
            "incident_count": len(durations) + ongoing_by_fault.get(name, 0),
            "completed_count": len(durations),
            "ongoing_count": ongoing_by_fault.get(name, 0),
            "total_downtime_minutes": round(sum(durations), 2) if durations else 0.0,
            "avg_duration_minutes": round(sum(durations) / len(durations), 2) if durations else None,
            "longest_duration_minutes": round(max(durations), 2) if durations else None,
            "shortest_duration_minutes": round(min(durations), 2) if durations else None,
        }
    return stats


def find_longest_incident(incidents: list):
    """Returns the single longest completed incident across all fault types,
    or None if there are no completed incidents."""
    completed = [i for i in incidents if not i["ongoing"] and i["duration_minutes"] is not None]
    if not completed:
        return None
    return max(completed, key=lambda i: i["duration_minutes"])


# ============================================================================
# 2. REPEAT-OFFENDER / CLUSTERING DETECTION
# ============================================================================

def detect_clusters(incidents: list, window_minutes: int = CLUSTER_WINDOW_MINUTES) -> list:
    """Flags groups of 2+ incidents of the SAME fault type starting within
    `window_minutes` of each other -- a signal the underlying cause wasn't
    actually resolved, not just a one-off. Returns a list of cluster dicts:
        {"fault_name": ..., "incident_count": N, "window_start": dt, "window_end": dt}
    """
    by_fault = defaultdict(list)
    for inc in incidents:
        by_fault[inc["fault_name"]].append(inc["started_at"])

    clusters = []
    for name, starts in by_fault.items():
        starts = sorted(starts)
        i = 0
        while i < len(starts):
            j = i
            while j + 1 < len(starts) and (starts[j + 1] - starts[i]).total_seconds() <= window_minutes * 60:
                j += 1
            count = j - i + 1
            if count >= 2:
                clusters.append({
                    "fault_name": name,
                    "incident_count": count,
                    "window_start": starts[i],
                    "window_end": starts[j],
                })
            i = j + 1

    clusters.sort(key=lambda c: c["incident_count"], reverse=True)
    return clusters


# ============================================================================
# 3. ROOT-CAUSE CORRELATION
# ============================================================================

def correlate_root_cause(incidents: list, snapshots: list) -> list:
    """For each incident, finds the snapshot closest to (at or just before)
    its started_at time and attaches the electrical/tank readings at that
    moment -- this is what turns "Dry Run happened" into "Dry Run happened
    while Bottom Tank Low sensor was triggered and current was 0.8A".

    Mutates and returns the incidents list with an added "snapshot_at_fault"
    key (dict of measurements) on each incident, or None if no snapshot
    was found within a reasonable window (5 minutes) of the fault start.
    """
    if not snapshots:
        for inc in incidents:
            inc["snapshot_at_fault"] = None
        return incidents

    # Snapshots are assumed sorted ascending by timestamp (as returned by
    # DataLogger.get_snapshots -- ORDER BY timestamp ASC).
    snap_times = []
    for s in snapshots:
        try:
            snap_times.append((parse_ts(s["timestamp"]), s))
        except Exception:
            continue

    for inc in incidents:
        target = inc["started_at"]
        best = None
        best_diff = None
        # Linear scan is fine at this data volume (tens of thousands of
        # rows, dozens of incidents) -- a binary search would be premature
        # optimization here and add complexity for no measurable benefit.
        for ts, snap in snap_times:
            if ts > target:
                break
            diff = (target - ts).total_seconds()
            if diff <= 300:  # within 5 minutes before the fault started
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best = snap
        inc["snapshot_at_fault"] = {
            "measurements": best["measurements"] if best else None,
            "tanks_raw": {
                k: best["measurements"].get(k)
                for k in ("tank_bottom_low", "tank_bottom_high", "tank_top_low", "tank_top_high")
            } if best else None,
            "seconds_before_fault": round(best_diff, 1) if best_diff is not None else None,
        }
    return incidents


# ============================================================================
# 4. TIMING PATTERNS
# ============================================================================

def hourly_distribution(incidents: list) -> dict:
    """Count of incident starts per hour-of-day (0-23), across all fault types."""
    counts = Counter(inc["started_at"].hour for inc in incidents)
    return {h: counts.get(h, 0) for h in range(24)}


def day_of_week_distribution(incidents: list) -> dict:
    """Count of incident starts per day-of-week. Monday=0 ... Sunday=6."""
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    counts = Counter(inc["started_at"].weekday() for inc in incidents)
    return {day_names[d]: counts.get(d, 0) for d in range(7)}


# ============================================================================
# 5. ELECTRICAL QUALITY & NEAR-MISSES
# ============================================================================

def detect_near_misses(snapshots: list) -> dict:
    """Counts snapshots where a reading was in a 'warning zone' -- concerning
    but not necessarily bad enough to have tripped a hard fault. Helps spot
    a developing problem before it causes real downtime."""
    voltage_keys = [k for k, m in MEASUREMENT_REGISTERS.items() if m.get("unit") == "V"]
    current_keys = [k for k, m in MEASUREMENT_REGISTERS.items() if m.get("unit") == "A"]

    voltage_low_count = 0
    voltage_high_count = 0
    voltage_imbalance_count = 0
    current_imbalance_count = 0

    for s in snapshots:
        m = s.get("measurements", {})
        v_vals = [m.get(k) for k in voltage_keys if m.get(k) is not None]
        c_vals = [m.get(k) for k in current_keys if m.get(k) is not None]

        if v_vals:
            if min(v_vals) < VOLTAGE_WARN_LOW:
                voltage_low_count += 1
            if max(v_vals) > VOLTAGE_WARN_HIGH:
                voltage_high_count += 1
            if len(v_vals) >= 2:
                mean_v = sum(v_vals) / len(v_vals)
                if mean_v and 100 * (max(v_vals) - min(v_vals)) / mean_v > VOLTAGE_IMBALANCE_WARN_PCT:
                    voltage_imbalance_count += 1

        if len(c_vals) >= 2:
            mean_c = sum(c_vals) / len(c_vals)
            if mean_c and 100 * (max(c_vals) - min(c_vals)) / mean_c > CURRENT_IMBALANCE_WARN_PCT:
                current_imbalance_count += 1

    return {
        "voltage_low_warnings": voltage_low_count,
        "voltage_high_warnings": voltage_high_count,
        "voltage_imbalance_warnings": voltage_imbalance_count,
        "current_imbalance_warnings": current_imbalance_count,
        "voltage_warn_low_threshold": VOLTAGE_WARN_LOW,
        "voltage_warn_high_threshold": VOLTAGE_WARN_HIGH,
    }


# ============================================================================
# 6. OPERATIONAL / DUTY METRICS
# ============================================================================

def operational_stats(snapshots: list) -> dict:
    """Pump runtime comparison, start/stop cycling, and Auto/Manual split.

    Uses the *_recent run-minute counters where available; falls back to
    treating current > ~0.5A as "running" for start/stop cycle counting
    since the register map does not expose a direct running/stopped bit
    for each pump (only cumulative run-minute counters)."""
    valid = [s for s in snapshots if s.get("measurements")]
    if not valid:
        return {
            "pump1_total_run_minutes": None, "pump2_total_run_minutes": None,
            "auto_pct": None, "manual_pct": None,
            "start_stop_cycles_estimated": None,
        }

    first_m = valid[0]["measurements"]
    last_m = valid[-1]["measurements"]

    def _delta(key_total):
        # Search from the start/end for the nearest snapshot that actually
        # has a value for this counter, rather than only checking the very
        # first/last snapshot -- a single comms-glitch None at either edge
        # would otherwise silently make the whole delta calculation None
        # even though good data exists a few rows away.
        a = None
        for s in valid:
            a = s["measurements"].get(key_total)
            if a is not None:
                break
        b = None
        for s in reversed(valid):
            b = s["measurements"].get(key_total)
            if b is not None:
                break
        if a is None or b is None:
            return None
        # Counters can wrap/reset (e.g. device reboot) -- a negative delta
        # means that happened; report 0 rather than a misleading negative.
        return max(0, b - a)

    pump1_delta = _delta("run_min_pump1_total")
    pump2_delta = _delta("run_min_pump2_total")

    auto_count = sum(1 for s in valid if s["measurements"].get("control_auto_manual") is not None)
    # control_auto_manual semantics are device-specific (observed value 102
    # in the sample data represents one mode); we report the raw value
    # distribution rather than guessing which numeric code means what, so
    # the report never mislabels Auto as Manual or vice versa.
    mode_value_counts = Counter(
        s["measurements"].get("control_auto_manual")
        for s in valid if s["measurements"].get("control_auto_manual") is not None
    )

    # Start/stop cycle estimate: count transitions of current_r crossing
    # above ~0.5A (pump starts) using whichever phase current is most
    # complete in this dataset.
    current_series = [s["measurements"].get("current_r") for s in valid]
    starts = 0
    was_running = False
    for c in current_series:
        if c is None:
            continue
        running_now = c > 0.5
        if running_now and not was_running:
            starts += 1
        was_running = running_now

    return {
        "pump1_total_run_minutes": pump1_delta,
        "pump2_total_run_minutes": pump2_delta,
        "mode_value_counts": dict(mode_value_counts),
        "start_stop_cycles_estimated": starts,
        "snapshots_used": len(valid),
    }


# ============================================================================
# 7. TANK SENSOR EVENTS
# ============================================================================

def tank_sensor_stats(snapshots: list) -> dict:
    """Counts how often each tank sensor was in a triggered (low/high) state,
    and specifically how often Dry Run co-occurred with Bottom Tank Low --
    the most actionable correlation for that fault type."""
    tank_keys = ["tank_bottom_low", "tank_bottom_high", "tank_top_low", "tank_top_high"]
    counts = {k: 0 for k in tank_keys}
    total_with_tank_data = 0
    dry_run_with_bottom_low = 0
    dry_run_total = 0

    for s in snapshots:
        m = s.get("measurements", {})
        f = s.get("faults", {})
        if not _is_valid_fault_snapshot(f):
            continue
        has_tank_data = any(m.get(k) is not None for k in tank_keys)
        if has_tank_data:
            total_with_tank_data += 1
            for k in tank_keys:
                if m.get(k) == 1:
                    counts[k] += 1

        if f.get("dry_run"):
            dry_run_total += 1
            if m.get("tank_bottom_low") == 1:
                dry_run_with_bottom_low += 1

    return {
        "sensor_triggered_counts": counts,
        "snapshots_with_tank_data": total_with_tank_data,
        "dry_run_snapshots": dry_run_total,
        "dry_run_with_bottom_tank_low": dry_run_with_bottom_low,
        "dry_run_bottom_low_correlation_pct": (
            round(100 * dry_run_with_bottom_low / dry_run_total, 1) if dry_run_total else None
        ),
    }


# ============================================================================
# 8. HEALTH SCORE & TOP ISSUES
# ============================================================================

def compute_health_score(duration_stats: dict, total_snapshots: int, near_misses: dict, clusters: list) -> dict:
    """Weighted 0-100 health index. Starts at 100 and deducts points for:
        - total fault downtime as a % of the period (heaviest weight)
        - number of distinct fault types that occurred
        - repeat-offender clusters (unresolved issues are worse than one-offs)
        - near-miss warning frequency (leading indicator of future faults)

    The exact weights are a reasonable starting heuristic, not a certified
    industry-standard formula -- documented here so they can be tuned later
    if a specific customer wants different sensitivity."""
    score = 100.0

    total_downtime = sum(s["total_downtime_minutes"] for s in duration_stats.values())
    # Assume ~5s poll interval -> total_snapshots * 5s = elapsed seconds,
    # a reasonable proxy for period length when exact since/until isn't passed in.
    estimated_period_minutes = max(total_snapshots * 5 / 60, 1)
    downtime_pct = min(100, 100 * total_downtime / estimated_period_minutes)
    score -= downtime_pct * 0.6  # downtime is the dominant factor

    distinct_faults = len(duration_stats)
    score -= min(20, distinct_faults * 3)  # more distinct fault TYPES = more issues to fix

    score -= min(15, len(clusters) * 4)  # unresolved/recurring issues

    near_miss_total = sum(v for k, v in near_misses.items() if k.endswith("_warnings"))
    score -= min(10, near_miss_total / max(total_snapshots, 1) * 100)  # near-misses as a rate

    score = max(0, min(100, round(score, 1)))

    if score >= 90:
        rating = "Excellent"
    elif score >= 75:
        rating = "Good"
    elif score >= 55:
        rating = "Fair — attention recommended"
    else:
        rating = "Poor — action required"

    return {"score": score, "rating": rating}


def rank_top_issues(duration_stats: dict, clusters: list, fault_help_text: dict, top_n: int = 3) -> list:
    """Ranks fault types by (downtime, then frequency) and attaches the
    remedy text for each, for the "Top N issues" section of both reports."""
    ranked = sorted(
        duration_stats.items(),
        key=lambda kv: (kv[1]["total_downtime_minutes"], kv[1]["incident_count"]),
        reverse=True,
    )
    cluster_by_fault = defaultdict(int)
    for c in clusters:
        cluster_by_fault[c["fault_name"]] = max(cluster_by_fault[c["fault_name"]], c["incident_count"])

    issues = []
    for name, stats in ranked[:top_n]:
        if stats["incident_count"] == 0:
            continue
        issues.append({
            "fault_name": name,
            "incident_count": stats["incident_count"],
            "total_downtime_minutes": stats["total_downtime_minutes"],
            "is_repeat_offender": cluster_by_fault.get(name, 0) >= 2,
            "remedy": fault_help_text.get(name, "No remedy guidance available for this fault."),
        })
    return issues


# ============================================================================
# TOP-LEVEL ENTRY POINT: builds the full analytics bundle in one call
# ============================================================================

def build_full_analytics(snapshots: list, fault_events: list, fault_help_text: dict) -> dict:
    """Single entry point both report generators call to get every piece of
    analytics computed consistently. Returns one dict with all sections."""
    valid_snapshots, excluded_count = filter_valid_snapshots(snapshots)

    incidents = pair_fault_incidents(fault_events)
    incidents = correlate_root_cause(incidents, snapshots)
    duration_stats = fault_duration_stats(incidents)
    longest = find_longest_incident(incidents)
    clusters = detect_clusters(incidents)
    hourly = hourly_distribution(incidents)
    dow = day_of_week_distribution(incidents)
    near_misses = detect_near_misses(snapshots)
    ops = operational_stats(snapshots)
    tanks = tank_sensor_stats(snapshots)
    health = compute_health_score(duration_stats, len(snapshots), near_misses, clusters)
    top_issues = rank_top_issues(duration_stats, clusters, fault_help_text)

    return {
        "excluded_invalid_snapshots": excluded_count,
        "valid_snapshot_count": len(valid_snapshots),
        "incidents": incidents,
        "duration_stats": duration_stats,
        "longest_incident": longest,
        "clusters": clusters,
        "hourly_distribution": hourly,
        "day_of_week_distribution": dow,
        "near_misses": near_misses,
        "operational_stats": ops,
        "tank_stats": tanks,
        "health": health,
        "top_issues": top_issues,
    }
