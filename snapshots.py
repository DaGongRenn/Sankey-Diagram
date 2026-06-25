# -*- coding: utf-8 -*-
"""
snapshots.py —— 快照落盘/读取/按 session 切窗口 + 抽关键帧 + 时间轴映射。

落盘格式:data/{KIND}_{YYYY-MM-DD}.jsonl,一行一个快照:
    {"ts": "2026-06-24T09:32:05+08:00", "boards": {"半导体": 12.34, ...}}
追加写,天然支持崩溃续跑;读取时按 ts 去重并排序。

时间轴(关键):把「交易时钟」映射成 0~1 的视频进度。收盘视频会把午休
11:30–13:00 从轴上剔除(动画连续):上午 120 分钟映射到 [0,0.5],
下午 120 分钟映射到 [0.5,1]。
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import config

log = logging.getLogger("snapshots")
TZ = ZoneInfo(config.TZ)


# ---------------------- 时间小工具 ----------------------
def now_tz() -> datetime:
    return datetime.now(TZ)


def today_str() -> str:
    return now_tz().strftime("%Y-%m-%d")


def _clock_min(hhmm: str) -> int:
    """'09:30' -> 570(当天的第几分钟)"""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def session_total_minutes(session: str) -> int:
    """该 session 覆盖的「交易分钟」总数(午盘 120,收盘 240)。"""
    return sum(_clock_min(e) - _clock_min(s) for s, e in config.SESSION_WINDOWS[session])


# ---------------------- 落盘 / 读取 ----------------------
def daily_path(date_str: str, kind: str | None = None):
    kind = (kind or config.SECTOR_KIND).upper()
    return config.DATA_DIR / f"{kind}_{date_str}.jsonl"


def append_snapshot(date_str: str, boards: dict[str, float], kind: str | None = None,
                    ts: str | None = None, src: str = "em") -> str:
    """追加一个快照到当日文件。ts 缺省取当前北京时间;src 记录数据来源(em/ths)。"""
    ts = ts or now_tz().isoformat(timespec="seconds")
    rec = {"ts": ts, "boards": boards, "src": src}
    with open(daily_path(date_str, kind), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return ts


def load_snapshots(date_str: str, kind: str | None = None) -> list[dict]:
    """读当日全部快照,按 ts 去重(保留最后一次)并按时间升序。"""
    p = daily_path(date_str, kind)
    if not p.exists():
        return []
    recs: dict[str, dict] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            recs[r["ts"]] = {"boards": r["boards"], "src": r.get("src", "em")}
        except Exception:
            continue
    return [{"ts": ts, "boards": v["boards"], "src": v["src"]} for ts, v in sorted(recs.items())]


def last_source(date_str: str, kind: str | None = None) -> str:
    """当日最后一帧的数据来源(em/ths),给渲染端决定显示方式;无数据默认 em。"""
    snaps = load_snapshots(date_str, kind)
    return snaps[-1]["src"] if snaps else "em"


# ---------------------- 时间轴映射 ----------------------
def map_progress(ts_iso: str, session: str, grace_min: float = 6.0):
    """把一个快照时间戳映射到 [0,1] 的视频进度;不在本 session 窗口内返回 None。

    - 开盘前 -> None(忽略)
    - 午休段 -> clamp 到上午收尾(0.5),累计值午休不变,正好复用 11:30 的值
    - 收盘后 grace 分钟内 -> 1.0;再久 -> None(防止把别的时段串进来)
    """
    total = session_total_minutes(session)
    dt = datetime.fromisoformat(ts_iso).astimezone(TZ)
    t = dt.hour * 60 + dt.minute + dt.second / 60.0
    wins = [(_clock_min(s), _clock_min(e)) for s, e in config.SESSION_WINDOWS[session]]
    first_start, last_end = wins[0][0], wins[-1][1]
    if t < first_start - 1e-6:
        return None
    if t > last_end + grace_min:
        return None
    offset = 0
    for cs, ce in wins:
        if t <= ce:
            inside = min(max(t, cs), ce)          # 落在午休则贴到段首/段尾
            return (offset + (inside - cs)) / total
        offset += ce - cs
    return 1.0                                     # 收盘后 grace 内


def progress_to_clock(p: float, session: str) -> str:
    """进度 [0,1] -> 'HH:MM' 交易时钟(给成片右上角时间戳用,午休已折叠)。"""
    total = session_total_minutes(session)
    tmin = max(0.0, min(1.0, p)) * total
    offset = 0
    for s, e in config.SESSION_WINDOWS[session]:
        cs, ce = _clock_min(s), _clock_min(e)
        seglen = ce - cs
        if tmin <= offset + seglen + 1e-6:
            clock = cs + (tmin - offset)
            return f"{int(clock) // 60:02d}:{int(round(clock)) % 60:02d}"
        offset += seglen
    ce = _clock_min(config.SESSION_WINDOWS[session][-1][1])
    return f"{ce // 60:02d}:{ce % 60:02d}"


# ---------------------- 关键帧 ----------------------
# ---------------------- 全市场氛围条:涨跌家数 + 成交额(独立稀疏序列)----------------------
def market_path(date_str: str):
    return config.DATA_DIR / f"MARKET_{date_str}.jsonl"      # 全市场,不分 kind


def append_market(date_str: str, up: int, down: int, turnover: float, ts: str | None = None) -> str:
    ts = ts or now_tz().isoformat(timespec="seconds")
    rec = {"ts": ts, "up": int(up), "down": int(down), "turnover": float(turnover)}
    with open(market_path(date_str), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return ts


def load_market(date_str: str) -> list[dict]:
    p = market_path(date_str)
    if not p.exists():
        return []
    recs: dict[str, dict] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            recs[r["ts"]] = r
        except Exception:
            continue
    return [recs[ts] for ts in sorted(recs)]


def find_prev_market(date_str: str) -> list[dict]:
    """取「上一个有数据的交易日」的全市场序列(用于较昨量变)。本地天然续存;
    CI 上靠 artifact 把昨日 MARKET 文件下载进 data/,这里同样能找到。"""
    prev_date, prev_file = None, None
    for f in config.DATA_DIR.glob("MARKET_*.jsonl"):
        d = f.stem.replace("MARKET_", "")
        if d < date_str and (prev_date is None or d > prev_date):
            prev_date, prev_file = d, f
    return load_market(prev_date) if prev_date else []


def build_market_keyframes(records: list[dict], session: str) -> list[tuple]:
    """全市场序列 → [(progress, up, down, turnover)],progress 严格递增。"""
    kf = []
    for r in records:
        p = map_progress(r["ts"], session)
        if p is None:
            continue
        kf.append((p, int(r["up"]), int(r["down"]), float(r["turnover"])))
    if not kf:
        return []
    dedup = {}
    for p, u, dn, to in sorted(kf, key=lambda x: x[0]):
        dedup[round(p, 5)] = (p, u, dn, to)
    return [dedup[k] for k in sorted(dedup)]


def build_keyframes(snapshots: list[dict], session: str) -> list[tuple]:
    """把快照序列转成关键帧 [(progress, boards, clock_str), ...],progress 严格递增。

    每个 2 分钟快照=一个关键帧;相邻关键帧之间由 sankey 模块做 ease-in-out 插值。
    """
    kf = []
    for rec in snapshots:
        p = map_progress(rec["ts"], session)
        if p is None:
            continue
        kf.append((p, rec["boards"], progress_to_clock(p, session)))
    if not kf:
        return []
    # 同 progress 去重(保留靠后/最新),确保 progress 严格递增,便于插值定位
    dedup: dict[float, tuple] = {}
    for p, b, clk in sorted(kf, key=lambda x: x[0]):
        dedup[round(p, 5)] = (p, b, clk)
    keyframes = [dedup[k] for k in sorted(dedup)]
    log.info("session=%s 关键帧=%d (覆盖进度 %.2f→%.2f)",
             session, len(keyframes), keyframes[0][0], keyframes[-1][0])
    return keyframes
