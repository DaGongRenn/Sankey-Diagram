# -*- coding: utf-8 -*-
"""
selfcheck.py —— 离线自检:合成一整天的「累计主力净流入」曲线,跑通
采集格式 → 关键帧插值 → 桑基布局 → 出片 全链路。不需要联网。

产物:out/selfcheck_midday.mp4 / selfcheck_close.mp4 以及若干帧 PNG 预览。
也用于 CI 的 dry_run,验证装字体/装依赖/渲染管线是否正常。

    python selfcheck.py
"""
from __future__ import annotations
import logging
import sys
from datetime import datetime, timedelta

import numpy as np

import config
import snapshots
import sankey
from render import frames_to_mp4
from run_window import date_label

log = logging.getLogger("selfcheck")

SYN_DATE = "2099-01-01"          # 合成数据专用日期,避免和真实快照混淆
SYN_KIND = config.SECTOR_KIND

# 贴合「概念板块」的一批名字(含用户偏好板块),数量需 > 2*TOP_N 以便挑 Top
BOARDS = ["半导体", "CPO", "算力", "PCB", "存储芯片", "人形机器人", "商业航天",
          "光模块", "AI智能体", "券商", "房地产", "医药", "白酒", "煤炭",
          "钢铁", "锂电池", "汽车整车", "电力", "游戏", "消费电子"]


def _trading_times(date_str: str):
    """该日 9:30–11:30 与 13:00–15:00,每 SAMPLE_INTERVAL_MIN 一个时间点。"""
    y, m, d = map(int, date_str.split("-"))
    step = timedelta(minutes=config.SAMPLE_INTERVAL_MIN)
    out = []
    for s, e in (config.MORNING, config.AFTERNOON):
        sh, sm = map(int, s.split(":"))
        eh, em = map(int, e.split(":"))
        t = datetime(y, m, d, sh, sm, tzinfo=snapshots.TZ)
        end = datetime(y, m, d, eh, em, tzinfo=snapshots.TZ)
        while t <= end:
            out.append(t)
            t += step
    return out


def synth_day(date_str: str):
    """生成确定性的合成快照并落盘(覆盖式)。"""
    path = snapshots.daily_path(date_str, SYN_KIND)
    if path.exists():
        path.unlink()

    rng = np.random.default_rng(20260624)
    times = _trading_times(date_str)
    n = len(times)

    # 每个板块:终值(亿元)+ 带波动的随机游走,起点≈0、终点钉到终值
    finals = rng.normal(0, 9, len(BOARDS))
    finals[0] += 12      # 半导体偏强流入
    finals[5] -= 10      # 人形机器人偏流出
    paths = []
    for f in finals:
        incr = rng.normal(0, 1.0, n)
        walk = np.cumsum(incr)
        walk -= walk[0]
        walk += (f - walk[-1]) * np.linspace(0, 1, n)   # 终点对齐到 f,保留中途波动
        paths.append(walk)
    paths = np.array(paths)                              # [boards, n]

    for j, t in enumerate(times):
        boards = {BOARDS[b]: round(float(paths[b, j]), 3) for b in range(len(BOARDS))}
        snapshots.append_snapshot(date_str, boards, SYN_KIND, ts=t.isoformat(timespec="seconds"))
    log.info("合成 %d 个快照 → %s", n, path.name)


def render_session(session: str) -> bool:
    snaps = snapshots.load_snapshots(SYN_DATE, SYN_KIND)
    kf = snapshots.build_keyframes(snaps, session)
    scene = sankey.prepare_scene(kf, session, date_label(SYN_DATE))
    out = config.OUT_DIR / f"selfcheck_{session}.mp4"
    frames_to_mp4(scene, out)

    # 落几张帧 PNG 便于肉眼校验布局
    for i, tag in [(0, "start"), (config.TOTAL_FRAMES // 2, "mid"), (config.TOTAL_FRAMES - 1, "end")]:
        sankey.draw_frame(scene, i).save(config.OUT_DIR / f"selfcheck_{session}_{tag}.png")

    ok = out.exists() and out.stat().st_size > 0
    log.info("%s %s (%.1f MB)", "✓" if ok else "✗", out.name,
             out.stat().st_size / 1e6 if out.exists() else 0)
    return ok


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
                        stream=sys.stdout)
    log.info("自检开始:DURATION=%ds FPS=%d 总帧=%d 画幅=%dx%d 间隔=%dmin TOP_N=%d",
             config.DURATION, config.FPS, config.TOTAL_FRAMES, config.W, config.H,
             config.SAMPLE_INTERVAL_MIN, config.TOP_N)
    synth_day(SYN_DATE)
    ok = all(render_session(s) for s in ("midday", "close"))
    if ok:
        log.info("✅ 自检通过:渲染链路 OK。")
    else:
        log.error("❌ 自检失败。")
        sys.exit(1)


if __name__ == "__main__":
    main()
