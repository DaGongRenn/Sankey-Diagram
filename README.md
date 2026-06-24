# A股板块资金「动态桑基图」每日自动出片

每个交易日,GitHub 云端自动产出两段抖音竖屏(9:16,1080×1920)资金流向桑基图视频,渲染完直接发你邮箱:

| 视频 | 覆盖窗口 | 触发(北京时间) | 邮件到达 |
|---|---|---|---|
| **午盘** | 09:30–11:30 | 约 09:10 起跑采集,11:30 渲染 | 约 11:32 |
| **收盘** | 全天 09:30–15:00(跳过午休,动画连续) | 约 12:45 起跑,15:00 渲染 | 约 15:03 |

两段用**同一套代码、同一套参数**,只是数据窗口不同 → 风格/布局/规则完全一致、稳定可复现。

---

## ⚠️ 先看这条:把仓库设为 Public(强烈建议)

桑基图需要**盘中持续轮询**东方财富(主力净流入是「当日累计」值,只能边走边采)。GitHub 定时任务最小粒度 5 分钟且常延迟,无法可靠地 2 分钟轮询,所以采集器是**长跑任务**(两段各约 2 小时)。

- **私有库**:每月约 5000+ Actions 分钟,**远超免费 2000 分钟**,会产生账单(约 $25+/月)。
- **公开库**:Actions **免费无限**。本项目代码不含任何密钥(密钥都在 Actions Secrets 里),公开无风险。

→ 仓库 **Settings → General → Danger Zone → Change visibility → Public**(本仓库只有桑基图项目,公开无密钥风险)。

> 另一个现实:GitHub runner 在海外,东方财富是国内接口,海外访问**可能时好时坏**。代码已做多 host 轮换 + 重试 + akshare 兜底 + 单次失败不中断;若云端持续连不上,见文末「改在本地跑」。

---

## 工作原理

```
午盘任务(09:10起)  采集 09:30–11:30 ─► 渲染午盘 ─► 发邮件 ─► 上传「上午快照」artifact
                                                                      │
收盘任务(12:45起)  下载上午快照 ◄──────────────────────────────────┘
                    采集 13:00–15:00(追加到同一日文件)─► 渲染全天 ─► 发邮件
```
- 每次快照(时间戳 + 各板块主力净流入)落盘成 `data/CONCEPT_YYYY-MM-DD.jsonl`,一天一个文件,**中途崩溃可续跑**。
- 收盘视频靠 artifact 拿到上午数据;万一午盘任务失败,收盘会**降级为仅下午**仍能出片,不报错。

---

## 一次性搭建(约 10 分钟)

### 1. 拿 QQ 邮箱「授权码」(发邮件用,不是登录密码)
- 登录网页版 QQ 邮箱 → **设置 → 账号(账户)** → 找到「POP3/IMAP/SMTP/Exchange/CardDAV 服务」
- 开启「**IMAP/SMTP 服务**」,按提示发短信验证 → 拿到一串 **16 位授权码**(形如 `abcdefghijklmnop`)
- 这串授权码就是下面的 `MAIL_PASSWORD`(发件服务器已配好 `smtp.qq.com:465`)

> 想用 Gmail:把两个 workflow 的 `server_address` 改回 `smtp.gmail.com`,`MAIL_PASSWORD` 填 Gmail 应用专用密码即可。

### 2. 配 Secrets
仓库 **Settings → Secrets and variables → Actions → New repository secret**,加 2 个:

| 名称 | 值 |
|---|---|
| `MAIL_USERNAME` | 你的 QQ 邮箱地址(如 `123456@qq.com`) |
| `MAIL_PASSWORD` | QQ 邮箱 16 位授权码 |

> 默认发件人=收件人=`MAIL_USERNAME`(发给自己)。想发别处,改两个 workflow 里的 `to:`。

### 3. 手动跑一次干跑测试(不用等开盘)
仓库 **Actions** → 选 **A股桑基·午盘** → **Run workflow** → 勾选 `dry_run` → 运行。
几分钟后邮箱应收到一封带 mp4 的【DRY】测试邮件(用合成数据渲染,验证装字体/装依赖/渲染/发信全链路)。收盘任务同理。

搞定 ✅ 之后每个交易日自动发两封。

---

## 文件结构

| 文件 | 作用 |
|---|---|
| `config.py` | **所有可配置项**(数据源/N/采集间隔/时长/FPS/配色/字体/布局/路径) |
| `datasource.py` | 东财 clist 抓取(多 host/重试/头部)+ akshare 兜底 → `{板块: 主力净流入亿元}` |
| `collector.py` | 交易时段轮询采集,落盘、崩溃可续 |
| `snapshots.py` | 快照读写 + 按 session 切窗口(跳午休)+ 抽关键帧 + 时间轴映射 |
| `sankey.py` | **桑基核心**:固定显示集/固定序/双侧平衡槽布局 + ease-in-out 插值 + Pillow 逐帧绘制 |
| `render.py` | 帧序列管道喂 ffmpeg 合成 mp4(自动定位 ffmpeg) |
| `run_window.py` | 渲染入口:`--session midday/close [--date]`,支持历史回放 |
| `emailer.py` | 本地 SMTP 发信(CI 用 action-send-mail) |
| `selfcheck.py` | 离线自检:合成一天数据跑通全链路出片 |
| `.github/workflows/sankey-midday.yml` / `sankey-close.yml` | 两个定时任务 |

---

## 可配置项(都在 `config.py` 顶部)

```python
SECTOR_KIND = "concept"     # concept=概念(默认) / industry=行业(备选)
TOP_N = 8                   # 流入/流出各取 Top N 作为全程固定显示板块
DURATION = 18 ; FPS = 30    # → 总渲染帧数 540(成片时长锁死,采集多少帧由它反推)
SAMPLE_INTERVAL_MIN = 2     # 采集间隔;改它=改关键帧密度,渲染自适应,主逻辑不动
W, H = 1080, 1920           # 抖音竖屏 9:16
WATERMARK = "@主力去哪了"    # 水印 / 频道 id
SHOW_MARKET_BAR = True       # 顶部全市场氛围条(涨跌家数+成交额+较昨量变)
MARKET_EVERY = 3             # 氛围条采得稀:每 3 次资金流轮询采一次(≈6min/点)
TOP_N / MIN_BAND_PX / COLORS / FONT_* / LAYOUT / CRF ...   # 配色、字体、布局、码率
```
只改 `SAMPLE_INTERVAL_MIN / FPS / DURATION` 即可调节细节/平滑度,不动主逻辑。采集越密→关键帧越多、细节越足;越疏→插值占比越大、越平滑。

> **顶部氛围条**:涨跌家数(akshare 乐咕)+ 两市成交额(东财指数 f6)+ 较昨同时刻量变。采集失败自动隐藏;「较昨」首日无基线时省略,次日起靠每日 `MARKET_*.jsonl` 序列(CI 用 `market-baseline` artifact 跨日衔接)自动迭代。

---

## 本地运行 / 历史回放

```powershell
pip install -r requirements.txt

# 离线自检(不联网,几分钟出两段样片到 out/)
python selfcheck.py

# 实采单次,确认接口通(打印各板块主力净流入)
python collector.py --session midday --once

# 本地一次性采集一整天(电脑需开机在交易时段)
python collector.py --session day

# 渲染 / 回放历史某天(确定性,同输入同输出)
python run_window.py --session close --date 2026-06-24

# 本地发邮件(需设 MAIL_USERNAME / MAIL_PASSWORD 环境变量)
python emailer.py --session close --date 2026-06-24
```
本地 ffmpeg:代码按 `FFMPEG_BIN → 系统 PATH → imageio-ffmpeg` 顺序自动定位,**会跳过损坏/桩文件**;最稳的是 `pip install imageio-ffmpeg`(自带可用二进制,已在 requirements 里)。

---

## 稳定性设计(对应「不能时好时坏」)
- **接口**:多 host 轮换 + 重试退避 + 头部伪装 + akshare 兜底;单次失败只跳过本次、不中断整天。
- **落盘**:每次成功快照立即追加写,崩溃重启续跑。
- **确定性布局**:节点集合/顺序由**整段最后一帧**定死,全程不重挑、不重排;固定比例尺;无随机 → 同输入必同输出。
- **桑基闭合**:主力净流入/流出不守恒,加灰色平衡节点(`总流出>总流入`→右侧「资金离场」;反之→左侧「增量入场」),每帧自动判断、左右各留槽位防抖。
- **可见性**:带宽设最小像素下限,小板块也看得见;节点标注真实数值;过零点红绿平滑过渡。
- **日志**:每步打印板块数/总额/耗时。

---

## 常见问题

- **中文显示成豆腐块 □**:云端缺字体——workflow 已装 `fonts-noto-cjk`;本地 Windows 自带雅黑不受影响。
- **`WinError 193` / ffmpeg 跑不起来**:本地缺可用 ffmpeg。`pip install imageio-ffmpeg`(已在 requirements 里)即可,代码会自动定位并跳过损坏/桩文件。
- **云端连不上东方财富(海外 runner)**:可在 Secrets/环境加 `HTTP_PROXY`(requests 自动生效);或改在本地跑(见下)。
- **午盘任务失败,收盘只有下午**:收盘任务对上午快照缺失做了降级,仍出片;想要全天就保证午盘任务成功。
- **邮件附件过大**:单段通常 3–10MB。QQ 邮箱普通附件上限 50MB(Gmail 25MB),基本不会超;万一超了把 `config.py` 的 `CRF` 调到 24。

### 改在本地跑(数据最稳、免 Actions 分钟)
若云端连东财不稳,把采集+渲染+发邮件放到你**国内、交易时段开机**的电脑上,用 Windows 计划任务:
- 11:30 触发 `python run_window.py --session midday && python emailer.py --session midday --date <今天>`(前提是 `collector.py --session day` 从 09:25 起已在跑)。
- 更简单:开机即 `python collector.py --session day`(跑满全天),再各自定时渲染+发信。
代码同一套,`config.py` 不变。
