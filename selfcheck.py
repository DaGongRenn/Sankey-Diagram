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

# 取自「100大热门板块清单」的板块名(需与 WHITELIST 匹配,数量 > 2*TOP_N 以便挑 Top)
BOARDS = ["CPO", "光模块", "PCB", "铜连接", "光芯片", "AI服务器", "液冷", "数据中心",
          "AI芯片", "GPU", "HBM", "存储芯片", "半导体设备", "晶圆代工", "光刻机",
          "碳化硅", "人形机器人", "机器视觉", "减速器", "伺服电机", "固态电池",
          "锂电池", "光伏", "风电", "氢能源", "创新药", "CRO", "医疗器械", "中药",
          "白酒", "啤酒", "家电", "医美", "黄金珠宝", "旅游酒店", "商业航天",
          "低空经济", "卫星互联网", "量子科技", "数据要素", "无人驾驶"]


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
    finals[0] += 12      # CPO 偏强流入
    finals[5] -= 10      # AI服务器 偏流出
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


PREV_DATE = "2098-12-31"   # 合成"昨天"的全市场序列,用于演示较昨量变


def synth_market(date_str: str, turnover_final: float, seed: int):
    """合成一天的全市场序列(涨跌家数 + 成交额累计)。"""
    p = snapshots.market_path(date_str)
    if p.exists():
        p.unlink()
    rng = np.random.default_rng(seed)
    times = _trading_times(date_str)
    n = len(times)
    for i, t in enumerate(times):
        frac = (i + 1) / n
        turnover = turnover_final * frac                      # 成交额随盘中累计
        down = int(3400 + 700 * frac + rng.normal(0, 40))     # 跌家数随盘走弱而增
        up = int(2100 - 680 * frac + rng.normal(0, 40))
        snapshots.append_market(date_str, max(up, 0), max(down, 0), turnover,
                                ts=t.isoformat(timespec="seconds"))


def render_session(session: str) -> bool:
    snaps = snapshots.load_snapshots(SYN_DATE, SYN_KIND)
    kf = snapshots.build_keyframes(snaps, session)
    mkf = snapshots.build_market_keyframes(snapshots.load_market(SYN_DATE), session)
    pmkf = snapshots.build_market_keyframes(snapshots.find_prev_market(SYN_DATE), session)
    source = snapshots.last_source(SYN_DATE, SYN_KIND)
    scene = sankey.prepare_scene(kf, session, date_label(SYN_DATE), source, mkf, pmkf)
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
    synth_market(PREV_DATE, 34660, seed=11)   # 昨天:总额更高 → 今天显示缩量
    synth_market(SYN_DATE, 33069, seed=12)     # 今天 ≈33069亿,收盘较昨 ≈ −1591亿
    ok = all(render_session(s) for s in ("midday", "close"))
    if ok:
        log.info("✅ 自检通过:渲染链路 OK。")
    else:
        log.error("❌ 自检失败。")
        sys.exit(1)


if __name__ == "__main__":
    main()
