# -*- coding: utf-8 -*-
"""
run_window.py —— 渲染入口:读当日快照 → 切 session 窗口 → 出竖屏 mp4 + meta.json。

    python run_window.py --session midday                 # 今天的午盘
    python run_window.py --session close                  # 今天的收盘
    python run_window.py --session close --date 2026-06-20 # 回放历史快照重渲(确定性)

成片:out/{session}_{date}.mp4;附带 out/{session}_{date}.meta.json(给发邮件用)。
"""
from __future__ import annotations
import argparse
import json
import logging
import sys

import config
import snapshots
import sankey
from render import frames_to_mp4

log = logging.getLogger("run_window")


def date_label(date_str: str) -> str:
    """'2026-06-24' -> '6月24日'(标题用,无前导零)。"""
    _, m, d = date_str.split("-")
    return f"{int(m)}月{int(d)}日"


def main():
    ap = argparse.ArgumentParser(description="渲染板块资金桑基图竖屏视频")
    ap.add_argument("--session", required=True, choices=["midday", "close"])
    ap.add_argument("--date", default=None, help="YYYY-MM-DD,缺省=今天(北京时间)")
    ap.add_argument("--kind", default=config.SECTOR_KIND, choices=["concept", "industry"])
    ap.add_argument("--out", default=None, help="输出 mp4 路径(缺省 out/{session}_{date}.mp4)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
                        stream=sys.stdout)

    date_str = args.date or snapshots.today_str()
    snaps = snapshots.load_snapshots(date_str, args.kind)
    log.info("读取快照 date=%s kind=%s 条数=%d", date_str, args.kind, len(snaps))
    if not snaps:
        log.error("当日无快照,无法渲染。先跑 collector.py 采集,或用 selfcheck.py 合成。")
        sys.exit(2)

    keyframes = snapshots.build_keyframes(snaps, args.session)
    if not keyframes:
        log.error("session=%s 窗口内无有效关键帧(可能采集时段不匹配)。", args.session)
        sys.exit(2)

    # 数据来源(em=东财自动Top-N / ths=同花顺),由收盘那帧决定显示方式
    source = snapshots.last_source(date_str, args.kind)
    # 全市场氛围条:今日序列 + 上一交易日序列(较昨量变);缺失则自动隐藏/省略
    market_kf = snapshots.build_market_keyframes(snapshots.load_market(date_str), args.session)
    prev_market_kf = snapshots.build_market_keyframes(snapshots.find_prev_market(date_str), args.session)
    log.info("数据来源=%s 氛围条:今日点=%d 昨日点=%d", source, len(market_kf), len(prev_market_kf))
    scene = sankey.prepare_scene(keyframes, args.session, date_label(date_str),
                                 source, market_kf, prev_market_kf)
    out_path = config.OUT_DIR / f"{args.session}_{date_str}.mp4" if not args.out else args.out
    frames_to_mp4(scene, out_path)

    # meta.json:给 workflow / 发邮件步骤读取
    meta = {
        "date": date_str,
        "date_label": date_label(date_str),
        "session": args.session,
        "session_label": config.SESSION_LABEL[args.session],
        "kind": args.kind,
        "out": str(out_path),
        "keyframes": len(keyframes),
        "inflow_top": scene["inflow"],
        "outflow_top": scene["outflow"],
    }
    meta_path = config.OUT_DIR / f"{args.session}_{date_str}.meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("✓ 完成 %s | 关键帧%d | 流入Top=%s", out_path, len(keyframes), scene["inflow"])
    print(str(out_path))


if __name__ == "__main__":
    main()
