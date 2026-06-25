# -*- coding: utf-8 -*-
"""
config.py —— 全部可配置项集中在此。改这里就能调,不要动主逻辑。
A股板块资金「动态桑基图」每日自动出片。

约定:所有金额单位统一为「亿元」;所有时间逻辑统一用北京时间(Asia/Shanghai)。
"""
from __future__ import annotations
import os
from pathlib import Path

# ======================================================================
# 0. 路径
# ======================================================================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"      # 每日快照(.jsonl,一天一个),保留以便回放/续跑
OUT_DIR  = BASE_DIR / "out"       # 成片 mp4 与 meta.json
DATA_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

# ======================================================================
# 1. 数据源
# ======================================================================
# concept=概念板块(默认,贴合半导体/CPO/算力/PCB/存储芯片/人形机器人/商业航天等),
# industry=行业板块(备选)。
SECTOR_KIND = os.environ.get("SECTOR_KIND", "concept")   # "concept" | "industry"
# 数据源优先级:ths=同花顺(海外可达,默认) / em=东财(主力净流入口径,仅国内可达)。
# 东财封境外 IP,GitHub 上必须用 ths;本地想要东财"主力净流入"口径可设 em。
PREFER_SOURCE = os.environ.get("PREFER_SOURCE", "ths")

# 东方财富 push2 clist 接口。fs 决定板块类型;fid=f62 按「主力净流入」排序;
# f62 = 当日主力净流入额(单位:元),代码里 /1e8 转「亿元」。
EM_FS = {
    "concept":  "m:90+t:3",   # 概念板块
    "industry": "m:90+t:2",   # 行业板块
}
EM_FIELDS = "f12,f14,f62,f184,f3"     # 代码,名称,主力净流入额(元),主力净占比,涨跌幅
EM_HOSTS = [                          # 多 host 轮换:海外/被限流时自动换一个
    "push2.eastmoney.com",
    "1.push2.eastmoney.com",
    "17.push2.eastmoney.com",
    "29.push2.eastmoney.com",
]
EM_UT = "b2884a393a59ad64002292a3e90d46a5"   # 公开页面通用 ut 令牌
EM_PAGE_SIZE = 500                    # 概念板块约 370~400 个,抓全(否则"流出最猛"的板块排在降序末尾会被截断)

# akshare 兜底(东财直连全失败时)。对应 stock_sector_fund_flow_rank 的 sector_type。
AK_SECTOR_TYPE = {
    "concept":  "概念资金流",
    "industry": "行业资金流",
}

# 网络:超时/重试/退避;HTTP_PROXY 环境变量自动生效(海外 runner 可挂代理)。
HTTP_TIMEOUT   = 12          # 单次请求超时(秒)
HTTP_RETRIES   = 4           # 单次抓取的重试次数(会轮换 host)
HTTP_BACKOFF   = 1.6         # 退避基数:第 i 次失败后 sleep BACKOFF**i 秒
HTTP_HEADERS   = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
    "Accept": "*/*",
}

# ======================================================================
# 2. 采集时段(北京时间)与视频窗口
# ======================================================================
TZ = "Asia/Shanghai"

# 交易日的两段连续交易时间(收盘视频会把午休 11:30–13:00 从动画时间轴上剔除)
MORNING = ("09:30", "11:30")     # 上午连续竞价
AFTERNOON = ("13:00", "15:00")   # 下午连续竞价

# 两段成片覆盖的窗口。midday 只到 11:30;close 覆盖全天(自动跳午休)。
SESSION_WINDOWS = {
    "midday": [MORNING],                 # 午盘:仅上午
    "close":  [MORNING, AFTERNOON],      # 收盘:上午 + 下午(连续)
}

SAMPLE_INTERVAL_MIN = 2     # 采集间隔(分钟)。改它=改关键帧密度,渲染会自适应,主逻辑不动。
SKIP_NON_TRADING = True     # True=周末/节假日(akshare 日历)自动跳过,不出片

# ----- 顶部「全市场氛围条」(涨跌家数 + 成交额 + 较昨量变),变化慢、采得稀 -----
SHOW_MARKET_BAR = True
MARKET_EVERY = 3            # 每 N 次资金流轮询才采一次全市场指标(≈6 分钟/点,降负载)
# 成交额数据源:新浪指数(海外可达)。上证综指 / 深证综指 / 北证50,成交额(元)求和≈两市总成交
MARKET_TURNOVER_SINA = ["sh000001", "sz399106", "bj899050"]
MARKET_TURNOVER_SECIDS = ["1.000001", "0.399106", "0.899050"]   # 东财备用(仅国内可达)

# ======================================================================
# 3. 视频输出参数(抖音竖屏 9:16)
# ======================================================================
DURATION = 18               # 成片时长(秒),锁死
FPS      = 30               # 帧率
TOTAL_FRAMES = DURATION * FPS   # = 540,总渲染帧数
W, H = 1080, 1920           # 9:16 竖屏

CRF      = 20               # H.264 质量(越小越清晰、文件越大);附件过大可调到 23~24
VIDEO_BITRATE = "8M"        # 目标码率,保证抖音上传清晰
PIX_FMT  = "yuv420p"        # 兼容性最佳

# ======================================================================
# 4. 桑基结构与稳定性参数
# ======================================================================
TOP_N = 10                  # 自动模式下:流入/流出各取 Top N(WHITELIST 为空时生效)

# 白名单:非空时只显示这些「概念关键词」(按子串匹配同花顺实际板块名,取最短匹配),
# 按真实资金流方向分左右、大小定带宽,其余并入平衡节点。空列表 [] = 回到自动 Top-N。
# 同花顺概念名常带后缀(如 CPO→「共封装光学(CPO)」、PCB→「PCB概念」),用关键词即可;
# 没匹配上的关键词会在日志里提示「白名单未匹配」,据此调整。
WHITELIST = [
    "存储芯片", "先进封装", "CPO", "PCB", "消费电子", "商业航天",
    "白酒", "创新药", "锂矿", "人形机器人", "半导体", "算力",
    "光模块", "数据中心", "固态电池", "黄金", "券商", "军工",
    "智能驾驶", "储能",
]
MIN_BAND_PX = 7             # 带宽/节点高度的最小像素下限,保证小板块也看得见
ZERO_EPS = 0.05             # 数值在 ±ZERO_EPS(亿元)内视为「近零」,红绿在此区间平滑过渡

# ======================================================================
# 5. 布局(像素坐标,W=1080 / H=1920)
# ======================================================================
# 竖屏三列:左(流出,绿)| 中(主力资金 hub)| 右(流入,红)。节点为竖向条带,纵向堆叠。
LAYOUT = {
    "stack_top":    372,    # 三列纵向堆叠区域上边界
    "stack_bottom": 1556,   # 下边界(给底部说明留白)
    "node_w":       30,     # 左右节点条带宽度(像素)
    "node_gap":     14,     # 同列相邻节点最小间隙
    "left_x":       230,    # 左列节点条带「中心」x(留足左侧板块名空间)
    "right_x":      850,    # 右列节点条带「中心」x
    "hub_x":        540,    # 中列 hub 中心 x
    "hub_w":        70,     # hub 条带宽度
    "label_pad":    22,     # 节点文字与条带的水平间距
    "label_margin": 10,     # 文字距屏幕左右边缘的最小留白
    "ribbon_alpha": 150,    # 缎带填充基础不透明度(0~255)
    "glow_blur":    16,     # 辉光高斯模糊半径(像素)
    "glow_gain":    0.9,    # 辉光叠加强度
}

# 顶部全市场氛围条的位置(在图例 y≈190 与主图 stack_top=372 之间,克制小巧)
MARKET_BAR = {
    "x0":         96,       # 条左端
    "x1":         984,      # 条右端
    "y_counts":   238,      # 「跌NNNN / 涨NNNN」文字基线
    "bar_y":      262,      # 分段条顶部 y
    "bar_h":      9,        # 分段条高度(细)
    "y_turnover": 300,      # 「成交…较昨…」一行基线
    "font_count": 25,       # 涨跌家数字号(小)
    "font_turn":  22,       # 成交额行字号(更小)
}

# ======================================================================
# 6. 配色(科技风:近黑深蓝底 + 霓虹辉光)
# ======================================================================
COLORS = {
    "bg_top":    (10, 14, 26),     # 背景竖向渐变上端(深蓝)
    "bg_bottom": (4, 5, 11),       # 下端(近黑)
    "grid":      (38, 56, 92),     # 细网格线
    "inflow":      (255, 64, 92),    # 净流入:荧光红(偏品红)
    "outflow":     (38, 230, 146),   # 净流出:荧光绿(偏青)
    "balance_in":  (230, 176, 75),   # 增量入场:琥珀金(新增资金涌入)
    "balance_out": (96, 108, 128),   # 资金离场:暗烟灰(资金退场/消散)
    "hub":         (120, 196, 255),  # 冷青蓝:右上角时间戳等点缀色
    "core":        (208, 222, 242),  # 中枢柔光核心色(略暗的冷白,缎带汇入此色)
    "text":      (236, 242, 252),  # 主文字
    "text_dim":  (150, 164, 188),  # 次要文字
    "title":     (245, 249, 255),  # 标题
}

# ======================================================================
# 7. 字体(取列表中第一个存在的)
# ======================================================================
# 中文主字体:本地 Windows 用微软雅黑;CI(ubuntu)用 fonts-noto-cjk。
FONT_CJK_PATHS = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
# 科技感数字字体(Orbitron 风格;没有就回退到中文字体)
FONT_TECH_PATHS = [
    r"C:\Windows\Fonts\bahnschrift.ttf",
    r"C:\Windows\Fonts\consolab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
] + FONT_CJK_PATHS

# ======================================================================
# 8. 文案
# ======================================================================
SESSION_LABEL = {"midday": "午盘", "close": "收盘"}
TITLE_TMPL    = "{date} {label} 资金流向"          # 顶部标题
HUB_LABEL     = "主力资金"
DISCLAIMER    = "据公开市场数据整理 · 仅供参考,不构成投资建议"
WATERMARK     = os.environ.get("WATERMARK", "@主力去哪了")   # 水印 / 频道 id

# ======================================================================
# 9. 邮件(本地/手动发信用;CI 走 action-send-mail,读同名 secrets)
#    QQ 邮箱:host=smtp.qq.com,port=465;密码填「授权码」(不是登录密码)。
#    Gmail:把 SMTP_HOST 设回 smtp.gmail.com,密码填应用专用密码即可。
# ======================================================================
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")   # 你的 QQ 邮箱地址(如 123456@qq.com)
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")   # QQ 邮箱「授权码」(16 位字母)
MAIL_TO = os.environ.get("MAIL_TO", "") or MAIL_USERNAME
