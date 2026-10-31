#!/usr/bin/env python3
"""Build bitFlyer absolute-price terrain JSON + HTML for an arbitrary time window."""

from __future__ import annotations

import argparse
import glob
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_BF = ROOT / "data/raw/crypto/bitflyer"
RAW_FX = ROOT / "data/raw/ref/yahoo/usdjpy"
TEMPLATE = ROOT / "scripts/templates/orderbook-terrain-3d.template.html"
OUT_JSON = ROOT / "data/viz/terrain-bitflyer.json"
OUT_HTML_DATA = ROOT / "data/viz/orderbook-terrain-3d.html"
OUT_HTML_REPO = ROOT / "viz/orderbook-terrain-3d.html"

JST = timezone(timedelta(hours=9))


def parse_path_utc(p: str | Path) -> datetime:
    parts = Path(p).parts
    return datetime(
        int(parts[-5]),
        int(parts[-4]),
        int(parts[-3]),
        int(parts[-2]),
        int(Path(p).stem),
        tzinfo=timezone.utc,
    )


def parse_user_dt(s: str) -> datetime:
    """Accept 'YYYY-MM-DDTHH:MM' as JST, or with Z/offset."""
    s = s.strip()
    if s.endswith("Z"):
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    if "+" in s[10:] or s.count("-") > 2:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    # bare local → JST
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(timezone.utc)


def load_frames(t0: datetime | None, t1: datetime | None) -> list[dict]:
    board_files = sorted(glob.glob(str(RAW_BF / "board/**/*.json"), recursive=True))
    ticker_files = sorted(glob.glob(str(RAW_BF / "ticker/**/*.json"), recursive=True))
    vol: dict[datetime, float] = {}
    for p in ticker_files:
        d = json.load(open(p))
        raw = d.get("raw") or {}
        v = raw.get("volume") or raw.get("volume_by_product")
        if v is None:
            continue
        vol[parse_path_utc(p)] = float(v)

    frames: list[dict] = []
    for p in board_files:
        t = parse_path_utc(p)
        if t0 and t < t0:
            continue
        if t1 and t > t1:
            continue
        d = json.load(open(p))
        m = d["metrics"]
        frames.append(
            {
                "t": t,
                "mid": m["mid_price"],
                "best_bid": m["best_bid"],
                "best_ask": m["best_ask"],
                "spread": m["spread"],
                "cum_vol": vol.get(t),
                "bids": d["bids"],
                "asks": d["asks"],
                "tlabel": "/".join(Path(p).parts[-4:]).replace(".json", ""),
            }
        )
    if len(frames) < 2:
        raise SystemExit(f"need >=2 frames in window, got {len(frames)}")
    for i, fr in enumerate(frames):
        if i == 0 or fr["cum_vol"] is None or frames[i - 1]["cum_vol"] is None:
            fr["dvol"] = 0.0
        else:
            fr["dvol"] = max(0.0, fr["cum_vol"] - frames[i - 1]["cum_vol"])
    return frames


def build_payload(frames: list[dict]) -> dict:
    mids = [fr["mid"] for fr in frames]
    gates = []
    for i, fr in enumerate(frames):
        gates.append(
            {
                "tIndex": i,
                "tIndexStart": i,
                "tIndexEnd": i,
                "mid": fr["mid"],
                "best_bid": fr["best_bid"],
                "best_ask": fr["best_ask"],
                "spread": fr["spread"],
                "volume": round(fr["dvol"], 6),
                "label": fr["t"].astimezone(JST).strftime("%H:%M"),
            }
        )

    mid_min, mid_max = min(mids), max(mids)
    MARGIN = 150000
    BIN = 5000
    p0 = math.floor((mid_min - MARGIN) / BIN) * BIN
    p1 = math.ceil((mid_max + MARGIN) / BIN) * BIN
    n_price = int((p1 - p0) / BIN)
    n_time = len(frames)

    grid = [[0.0] * n_price for _ in range(n_time)]
    river_lo, river_hi, river_mid = [], [], []
    side = [[0] * n_price for _ in range(n_time)]

    for ti, fr in enumerate(frames):
        lo, hi, mid = fr["best_bid"], fr["best_ask"], fr["mid"]
        if hi - lo < BIN:
            lo = mid - BIN * 0.5
            hi = mid + BIN * 0.5
        river_mid.append(mid)
        river_lo.append(lo)
        river_hi.append(hi)
        for i in range(n_price):
            bin_lo = p0 + i * BIN
            bin_hi = bin_lo + BIN
            if bin_hi <= lo:
                side[ti][i] = -1
            elif bin_lo >= hi:
                side[ti][i] = 1
            else:
                side[ti][i] = 0
        for levels in (fr["bids"], fr["asks"]):
            for x in levels:
                pr = x["price"]
                if pr < p0 or pr >= p1:
                    continue
                ii = int((pr - p0) / BIN)
                if 0 <= ii < n_price and side[ti][ii] != 0:
                    grid[ti][ii] += x["size"]

    def smooth_banks(g, side_map):
        out = [row[:] for row in g]
        for t in range(n_time):
            for i in range(n_price):
                s0 = side_map[t][i]
                if s0 == 0:
                    out[t][i] = 0.0
                    continue
                s = 0.0
                c = 0
                for dt in (-1, 0, 1):
                    for di in (-1, 0, 1):
                        tt, ii = t + dt, i + di
                        if 0 <= tt < n_time and 0 <= ii < n_price and side_map[tt][ii] == s0:
                            s += g[tt][ii]
                            c += 1
                out[t][i] = s / c if c else 0.0
        return out

    grid_s = smooth_banks(smooth_banks(grid, side), side)
    max_h = max((grid_s[t][i] for t in range(n_time) for i in range(n_price)), default=1.0) or 1.0

    fx_files = sorted(glob.glob(str(RAW_FX / "**/*.json"), recursive=True))
    fx_t, fx_v = [], []
    for p in fx_files:
        d = json.load(open(p))
        fx_t.append(parse_path_utc(p).timestamp())
        fx_v.append(float(d["price"]))

    def catmull_rom(t, ts, vs):
        if not ts:
            return 0.0
        if t <= ts[0]:
            return vs[0]
        if t >= ts[-1]:
            return vs[-1]
        i = 0
        while i < len(ts) - 1 and ts[i + 1] < t:
            i += 1
        i0, i1, i2, i3 = max(0, i - 1), i, min(len(ts) - 1, i + 1), min(len(ts) - 1, i + 2)
        t1, t2 = ts[i1], ts[i2]
        u = 0 if t2 == t1 else (t - t1) / (t2 - t1)
        p0v, p1v, p2v, p3v = vs[i0], vs[i1], vs[i2], vs[i3]
        u2, u3 = u * u, u * u * u
        return 0.5 * (
            (2 * p1v)
            + (-p0v + p2v) * u
            + (2 * p0v - 5 * p1v + 4 * p2v - p3v) * u2
            + (-p0v + 3 * p1v - 3 * p2v + p3v) * u3
        )

    fx_per = [catmull_rom(fr["t"].timestamp(), fx_t, fx_v) for fr in frames]
    fx_min, fx_max = min(fx_per), max(fx_per)
    fx_span = (fx_max - fx_min) or 1e-9
    fx_norm = [(v - fx_min) / fx_span for v in fx_per]

    water, flat, side_flat = [], [], []
    for t in range(n_time):
        for i in range(n_price):
            s = side[t][i]
            side_flat.append(s)
            water.append(1 if s == 0 else 0)
            flat.append(0.0 if s == 0 else round(grid_s[t][i] / max_h, 5))

    max_gate_vol = max((g["volume"] for g in gates), default=1) or 1
    for g in gates:
        g["volNorm"] = round(g["volume"] / max_gate_vol, 5)
        ti = g["tIndex"]
        t0i = max(0, ti - 1)
        t1i = min(n_time - 1, ti + 1)
        g["dirTime"] = t1i - t0i
        g["dirPrice"] = river_mid[t1i] - river_mid[t0i]

    return {
        "nTime": n_time,
        "nPrice": n_price,
        "bin": BIN,
        "price0": p0,
        "price1": p1,
        "maxHeightBtc": max_h,
        "maxGateVolume": max_gate_vol,
        "riverMid": [round(m, 1) for m in river_mid],
        "riverLo": [round(x, 1) for x in river_lo],
        "riverHi": [round(x, 1) for x in river_hi],
        "heights": flat,
        "water": water,
        "side": side_flat,
        "fx": [round(v, 5) for v in fx_per],
        "fxNorm": [round(v, 5) for v in fx_norm],
        "fxMin": fx_min,
        "fxMax": fx_max,
        "gates": gates,
        "t0Label": frames[0]["tlabel"],
        "t1Label": frames[-1]["tlabel"],
        "t0Iso": frames[0]["t"].isoformat(),
        "t1Iso": frames[-1]["t"].isoformat(),
    }


def write_outputs(payload: dict) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML_REPO.parent.mkdir(parents=True, exist_ok=True)
    data_js = json.dumps(payload, separators=(",", ":"))
    OUT_JSON.write_text(data_js, encoding="utf-8")
    tpl = TEMPLATE.read_text(encoding="utf-8")
    if "__DATA__" not in tpl:
        raise SystemExit("template missing __DATA__")
    html = tpl.replace("__DATA__", data_js, 1)
    OUT_HTML_DATA.write_text(html, encoding="utf-8")
    OUT_HTML_REPO.write_text(html, encoding="utf-8")
    print(
        f"wrote {OUT_JSON.name} + HTML nTime={payload['nTime']} "
        f"{payload['t0Label']} → {payload['t1Label']}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="t_from", default=None, help="start JST e.g. 2026-10-31T09:00")
    ap.add_argument("--to", dest="t_to", default=None, help="end JST e.g. 2026-10-31T11:00")
    args = ap.parse_args()
    t0 = parse_user_dt(args.t_from) if args.t_from else None
    t1 = parse_user_dt(args.t_to) if args.t_to else None
    frames = load_frames(t0, t1)
    payload = build_payload(frames)
    write_outputs(payload)


if __name__ == "__main__":
    main()
