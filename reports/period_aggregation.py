"""
Period-based data aggregation for PUMPGURU reports.

Given a flat list of snapshots (as returned by DataLogger.get_snapshots),
groups them into buckets by hour / day / week / month and computes
min/max/average per measurement per bucket, plus overall statistics and
fault summaries for the whole range. Used by both the Excel and PDF report
generators so the two output formats never disagree with each other.
"""

import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict, Counter

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.register_map import MEASUREMENT_REGISTERS

VALID_GRANULARITIES = ("hourly", "daily", "weekly", "monthly")


def _bucket_key(ts: datetime, granularity: str) -> str:
    """Returns a sortable string key identifying which bucket a timestamp
    falls into, plus a human-readable label is derived separately."""
    if granularity == "hourly":
        return ts.strftime("%Y-%m-%d %H:00")
    if granularity == "daily":
        return ts.strftime("%Y-%m-%d")
    if granularity == "weekly":
        iso_year, iso_week, _ = ts.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if granularity == "monthly":
        return ts.strftime("%Y-%m")
    raise ValueError(f"Unknown granularity: {granularity}")


def _bucket_label(key: str, granularity: str) -> str:
    """Human-friendly label for a bucket key, used as the row/axis label."""
    if granularity == "hourly":
        dt = datetime.strptime(key, "%Y-%m-%d %H:00")
        return dt.strftime("%b %d, %H:00")
    if granularity == "daily":
        dt = datetime.strptime(key, "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
    if granularity == "weekly":
        year, week = key.split("-W")
        return f"Week {week}, {year}"
    if granularity == "monthly":
        dt = datetime.strptime(key, "%Y-%m")
        return dt.strftime("%B %Y")
    return key


def parse_timestamp(ts_str: str) -> datetime:
    """Snapshots store timestamps as ISO strings (isoformat(timespec='seconds'))."""
    return datetime.fromisoformat(ts_str)


def resolve_date_range(
    since: str = None,
    until: str = None,
    days: int = None,
    granularity: str = "daily",
):
    """Resolves the effective (since, until) datetime range from whichever
    combination of parameters was given. Priority:
        1. Explicit since/until (ISO date or datetime strings), if given.
        2. days (lookback from now), if given.
        3. A sensible default window based on granularity, so a bare
           "give me an hourly report" request still returns something
           useful without requiring the caller to compute a window.
    """
    now = datetime.now()

    if since or until:
        since_dt = datetime.fromisoformat(since) if since else (now - timedelta(days=7))
        until_dt = datetime.fromisoformat(until) if until else now
        return since_dt, until_dt

    if days is not None:
        return now - timedelta(days=days), now

    default_lookback = {
        "hourly": timedelta(days=1),
        "daily": timedelta(days=30),
        "weekly": timedelta(weeks=12),
        "monthly": timedelta(days=365),
    }
    return now - default_lookback.get(granularity, timedelta(days=7)), now


def aggregate_by_period(snapshots: list, granularity: str = "daily") -> dict:
    """Groups snapshots into time buckets and computes per-bucket statistics
    for every configured measurement, plus fault-active-snapshot counts.

    Returns:
        {
          "granularity": "daily",
          "buckets": [
            {
              "key": "2026-08-24",
              "label": "Aug 24, 2026",
              "count": 288,
              "faulted_count": 3,
              "measurements": {
                 "voltage_ry": {"min": 408.1, "max": 421.4, "avg": 415.0},
                 ...
              }
            },
            ...
          ]
        }
    """
    if granularity not in VALID_GRANULARITIES:
        raise ValueError(f"granularity must be one of {VALID_GRANULARITIES}, got {granularity!r}")

    buckets_raw = defaultdict(lambda: {
        "count": 0,
        "faulted_count": 0,
        "values": defaultdict(list),
    })

    for s in snapshots:
        try:
            ts = parse_timestamp(s["timestamp"])
        except Exception:
            continue
        key = _bucket_key(ts, granularity)
        b = buckets_raw[key]
        b["count"] += 1
        if any(s.get("faults", {}).values()):
            b["faulted_count"] += 1
        for name in MEASUREMENT_REGISTERS.keys():
            val = s.get("measurements", {}).get(name)
            if val is not None:
                b["values"][name].append(val)

    buckets = []
    for key in sorted(buckets_raw.keys()):
        raw = buckets_raw[key]
        measurements = {}
        for name, vals in raw["values"].items():
            if vals:
                measurements[name] = {
                    "min": round(min(vals), 3),
                    "max": round(max(vals), 3),
                    "avg": round(sum(vals) / len(vals), 3),
                }
        buckets.append({
            "key": key,
            "label": _bucket_label(key, granularity),
            "count": raw["count"],
            "faulted_count": raw["faulted_count"],
            "measurements": measurements,
        })

    return {"granularity": granularity, "buckets": buckets}


def overall_stats(snapshots: list, fault_events: list) -> dict:
    """Whole-range summary statistics, independent of bucket granularity --
    used for the report's Summary section regardless of what period the
    trend table is broken into."""
    total = len(snapshots)
    faulted = sum(1 for s in snapshots if any(s.get("faults", {}).values()))
    uptime_pct = round(100 * (1 - faulted / total), 2) if total else None

    measurement_stats = {}
    for name, meta in MEASUREMENT_REGISTERS.items():
        vals = [s["measurements"].get(name) for s in snapshots if s.get("measurements", {}).get(name) is not None]
        if vals:
            measurement_stats[name] = {
                "min": round(min(vals), 3),
                "max": round(max(vals), 3),
                "avg": round(sum(vals) / len(vals), 3),
                "unit": meta.get("unit", ""),
                "label": meta.get("label", name),
            }

    fault_counts = Counter(e["fault_name"] for e in fault_events if e["state"] == "ACTIVE")

    voltage_keys = [k for k in MEASUREMENT_REGISTERS if "voltage" in k]
    current_keys = [k for k in MEASUREMENT_REGISTERS if "current" in k]
    voltage_imbalance = None
    current_imbalance = None
    if len(voltage_keys) >= 2 and all(k in measurement_stats for k in voltage_keys):
        avgs = [measurement_stats[k]["avg"] for k in voltage_keys]
        mean = sum(avgs) / len(avgs)
        voltage_imbalance = round(100 * (max(avgs) - min(avgs)) / mean, 2) if mean else 0
    if len(current_keys) >= 2 and all(k in measurement_stats for k in current_keys):
        avgs = [measurement_stats[k]["avg"] for k in current_keys]
        mean = sum(avgs) / len(avgs)
        current_imbalance = round(100 * (max(avgs) - min(avgs)) / mean, 2) if mean else 0

    return {
        "total_snapshots": total,
        "faulted_snapshots": faulted,
        "uptime_pct": uptime_pct,
        "total_fault_events": sum(1 for e in fault_events if e["state"] == "ACTIVE"),
        "measurement_stats": measurement_stats,
        "fault_counts": dict(fault_counts.most_common()),
        "voltage_imbalance_pct": voltage_imbalance,
        "current_imbalance_pct": current_imbalance,
    }
