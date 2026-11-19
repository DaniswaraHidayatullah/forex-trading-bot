"""Read-only comparison: python tools/eval_nanda_notifications.py JOURNAL TICKS.csv"""
import argparse
import csv
import json
import math
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def timestamp(value):
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("timestamp must include UTC offset")
    return t.astimezone(timezone.utc)


def load_ticks(path):
    times, prices = [], []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["symbol"] != "XAUUSDm":
                continue
            t = timestamp(row["timestamp"])
            bid, ask = float(row["bid"]), float(row["ask"])
            if (times and t < times[-1]) or not (math.isfinite(ask) and 0 < bid <= ask):
                raise ValueError("invalid or unsorted tick")
            times.append(t)
            prices.append((bid, ask))
    return times, prices


def evaluate(e, ack, times, prices):
    i = bisect_left(times, ack)
    if i == len(times) or times[i] - ack > timedelta(seconds=5):
        return {"result": "no_entry_tick"}
    bid, ask = prices[i]
    if not (e["tp"] < bid <= ask < e["sl"]):
        return {"result": "no_longer_valid"}
    info = {"bid_entry": bid, "entry_utc": times[i].isoformat()}
    previous = times[i]
    for k in range(i+1, len(times)):
        t = times[k]
        if t-previous > timedelta(seconds=60):
            return dict(info, result="tick_gap")
        if t-times[i] >= timedelta(days=2):
            return dict(info, result="expired")
        ask = prices[k][1]
        if ask >= e["sl"] or ask <= e["tp"]:
            return dict(info, result="tp" if ask <= e["tp"] else "sl", exit_ask=ask,
                        exit_utc=t.isoformat(), result_r=(bid-ask)/(e["sl"]-bid))
        previous = t
    return dict(info, result="insufficient_ticks")


def describe(counts):
    n = counts["tp"] + counts["sl"]
    return {"counts": dict(counts), "wr_pct": round(100*counts["tp"]/n, 1) if n else None}


def compare(state, times, prices):
    base = {t["id"]: t["reason"] for t in state["strategy"]["trades"]}
    rows = []
    for e in state["signals"]:
        a = state.get("alerts", {}).get(e["id"], {})
        if a.get("kind") != "confirm" or a.get("status") != "sent":
            continue
        ack = timestamp(a["acknowledged_utc"])
        rows.append(dict(id=e["id"], baseline=base.get(e["id"], "open"),
                         latency_seconds=(ack-timestamp(e["time"])).total_seconds(),
                         **evaluate(e, ack, times, prices)))
    return {"all_v2": describe(Counter(base.get(e["id"], "open") for e in state["signals"]
                                        if e["strategy_taken"])),
            "notified_baseline": describe(Counter(r["baseline"] for r in rows)),
            "post_discord": describe(Counter(r["result"] for r in rows)), "rows": rows}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("journal", type=Path)
    p.add_argument("ticks", type=Path)
    args = p.parse_args()
    print(json.dumps(compare(json.loads(args.journal.read_text(encoding="utf-8")),
                             *load_ticks(args.ticks)), indent=2))
