# -*- coding: utf-8 -*-
"""
collector.py —— 交易时段轮询采集器(长跑任务)。

每 SAMPLE_INTERVAL_MIN 分钟抓一次板块主力净流入快照,立即落盘(崩溃可续)。
单次抓取失败只跳过本次、绝不中断整天采集。

各 session 负责的采集窗口:
    midday → 上午 09:30–11:30
    close  → 下午 13:00–15:00   (上午数据由午盘任务的 artifact 提供)
    day    → 全天(上午 + 下午,本地一次性采集用)

用法:
    python collector.py --session midday          # 跑到 11:30 自动结束
    python collector.py --session close            # 跑到 15:00 自动结束
    python collector.py --session midday --once    # 只抓一次(测试/CI 自检)
    python collector.py --session day --date 2026-06-24 --force
"""
from __future__ import annotations
import argparse
import logging
import sys
import time
from datetime import datetime

import config
from datasource import fetch_snapshot, fetch_market_overview
from snapshots import TZ, now_tz, today_str, append_snapshot, append_market

log = logging.getLogger("collector")

# 各 session 实际轮询的交易窗口(与“成片覆盖窗口”SESSION_WINDOWS 区分开)
COLLECT_WINDOWS = {
    "midday": [config.MORNING],
    "close":  [config.AFTERNOON],
    "day":    [config.MORNING, config.AFTERNOON],
}


def parse_clock(date_str: str, hhmm: str) -> datetime:
    y, m, d = map(int, date_str.split("-"))
    hh, mm = map(int, hhmm.split(":"))
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def is_trading_day(date_str: str) -> bool:
    """优先用 akshare 交易日历;不可用则退化为「周一~周五」。"""
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        days = {str(x)[:10] for x in df["trade_date"].astype(str)}
        return date_str in days
    except Exception as e:
        log.warning("交易日历不可用(%s),退化为周一~周五判断", e)
        y, m, d = map(int, date_str.split("-"))
        return datetime(y, m, d).weekday() < 5


def wait_until(target: datetime):
    """睡到 target(分块睡,保持任务存活、能被日志观测)。"""
    while True:
        remain = (target - now_tz()).total_seconds()
        if remain <= 0:
            return
        if remain > 90:
            log.info("等待中… 距 %s 还有 %.1f 分钟", target.strftime("%H:%M"), remain / 60)
        time.sleep(min(remain, 30))


def poll_window(date_str: str, kind: str, start: str, end: str) -> int:
    """在 [start,end] 交易窗口内每隔 SAMPLE_INTERVAL_MIN 抓一次。返回成功帧数。"""
    start_dt, end_dt = parse_clock(date_str, start), parse_clock(date_str, end)
    if now_tz() >= end_dt:
        log.info("窗口 %s–%s 已过,跳过", start, end)
        return 0
    if now_tz() < start_dt:
        log.info("尚未开盘,等待窗口 %s …", start)
        wait_until(start_dt)

    n = 0
    while now_tz() < end_dt:
        t0 = time.time()
        ok = False
        try:
            boards, src = fetch_snapshot(kind)
            ts = append_snapshot(date_str, boards, kind, src=src)
            n += 1
            ok = True
            inflow = sum(v for v in boards.values() if v > 0)
            outflow = sum(v for v in boards.values() if v < 0)
            log.info("[%s] 第%d采 板块=%d 流入合计=%.1f亿 流出合计=%.1f亿 (%.1fs)",
                     ts[11:19], n, len(boards), inflow, outflow, time.time() - t0)
        except Exception as ex:
            log.error("采集失败(跳过本次,不中断整天): %s", ex)
        # 全市场氛围条:采得稀(每 MARKET_EVERY 次成功轮询采一次),best-effort,不影响主采集
        if ok and config.SHOW_MARKET_BAR and (n - 1) % config.MARKET_EVERY == 0:
            try:
                mo = fetch_market_overview()
                append_market(date_str, mo["up"], mo["down"], mo["turnover"])
                log.info("  [全市场] 涨%d 跌%d 成交%.0f亿", mo["up"], mo["down"], mo["turnover"])
            except Exception as me:
                log.warning("  全市场指标抓取失败(跳过): %s", me)
        remain = (end_dt - now_tz()).total_seconds()
        if remain <= 0:
            break
        time.sleep(min(config.SAMPLE_INTERVAL_MIN * 60, remain))
    log.info("窗口 %s–%s 结束,本窗成功 %d 帧", start, end, n)
    return n


def main():
    ap = argparse.ArgumentParser(description="A股板块资金 桑基图 采集器")
    ap.add_argument("--session", required=True, choices=list(COLLECT_WINDOWS))
    ap.add_argument("--date", default=None, help="YYYY-MM-DD,缺省=今天(北京时间)")
    ap.add_argument("--kind", default=config.SECTOR_KIND, choices=["concept", "industry"])
    ap.add_argument("--once", action="store_true", help="只抓一次后退出(测试用)")
    ap.add_argument("--force", action="store_true", help="无视交易日历强制采集")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
                        stream=sys.stdout)

    date_str = args.date or today_str()
    log.info("启动采集 session=%s date=%s kind=%s once=%s",
             args.session, date_str, args.kind, args.once)

    if args.once:
        boards, src = fetch_snapshot(args.kind)
        ts = append_snapshot(date_str, boards, args.kind, src=src)
        top = sorted(boards.items(), key=lambda kv: kv[1], reverse=True)[:5]
        log.info("单次采集 ts=%s 来源=%s 板块=%d 流入Top5=%s", ts, src, len(boards),
                 [(n, round(v, 2)) for n, v in top])
        return

    if config.SKIP_NON_TRADING and not args.force and not is_trading_day(date_str):
        log.info("%s 非交易日,跳过(加 --force 可强制)。", date_str)
        return

    total = 0
    for start, end in COLLECT_WINDOWS[args.session]:
        total += poll_window(date_str, args.kind, start, end)
    log.info("采集完成 session=%s date=%s 总帧数=%d", args.session, date_str, total)
    if total == 0:
        log.warning("一帧都没采到——可能行情接口全程不可达,渲染将无数据。")


if __name__ == "__main__":
    main()
