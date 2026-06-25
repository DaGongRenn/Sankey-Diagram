# -*- coding: utf-8 -*-
"""render_snapshot.py —— 抓"当前"概念资金流(收盘后=当日收盘值)渲染一张收盘静态图。
单帧、无需盘中时间序列;在 GitHub(能连同花顺)上跑。用法:python render_snapshot.py [YYYY-MM-DD]
"""
import sys
import datasource
import snapshots
import sankey
import config
from run_window import date_label

date_str = sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else snapshots.today_str()
print("日期:", date_str)

boards = datasource.fetch_snapshot("concept")          # 当前=收盘后即今日收盘累计
print("主图数据 OK,板块数:", len(boards))
top = sorted(boards.items(), key=lambda kv: kv[1], reverse=True)[:5]
print("流入Top5:", [(n, round(v, 1)) for n, v in top])

mkf = []
try:
    mo = datasource.fetch_market_overview()
    mkf = [(1.0, mo["up"], mo["down"], mo["turnover"])]
    print("氛围条 OK:", mo)
except Exception as e:
    print("氛围条失败(忽略,不影响主图):", e)

kf = [(1.0, boards, "15:00")]                          # 单关键帧=收盘态
scene = sankey.prepare_scene(kf, "close", date_label(date_str), mkf, [])
out = config.OUT_DIR / f"close_{date_str}.png"
sankey.draw_frame(scene, config.TOTAL_FRAMES - 1).save(out)
print("已生成静态图:", out)
