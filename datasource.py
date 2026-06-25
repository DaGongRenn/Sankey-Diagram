# -*- coding: utf-8 -*-
"""
datasource.py —— 板块资金流快照数据源(单位:亿元)。

优先:同花顺概念/行业资金流「净额」(akshare,海外 runner 可达,默认)。
备用:东方财富 push2「主力净流入」(口径更准,但封境外 IP,仅国内可达)。
兜底:akshare 东财 stock_sector_fund_flow_rank。
顺序由 config.PREFER_SOURCE 决定(ths/em)。

对外只暴露一个函数:
    fetch_snapshot(kind="concept") -> dict[str, float]
返回 {板块名: 当日累计资金净流入(亿元)};失败抛 DataSourceError。

注意:净流入是「当日累计」值——每次拿到的是截至此刻的累计净额,
把不同时刻的快照按时间拼起来,就是盘中演变曲线。
"""
from __future__ import annotations
import os
os.environ.setdefault("TQDM_DISABLE", "1")   # 静音 akshare 内部 tqdm 进度条,清爽日志
import logging
import time
import requests

import config

log = logging.getLogger("datasource")


class DataSourceError(Exception):
    pass


# ----------------------------------------------------------------------
# 主数据源:东方财富 clist
# ----------------------------------------------------------------------
def _fetch_eastmoney(kind: str) -> dict[str, float]:
    """从东财 clist 拉一页概念/行业板块主力净流入。轮换 host + 重试。"""
    fs = config.EM_FS[kind]
    params = {
        "pn": 1, "pz": config.EM_PAGE_SIZE, "po": 1, "np": 1,
        "fltt": 2, "invt": 2, "fid": "f62", "fs": fs,
        "fields": config.EM_FIELDS, "ut": config.EM_UT,
        "_": int(time.time() * 1000),
    }
    last_err = None
    for attempt in range(config.HTTP_RETRIES):
        host = config.EM_HOSTS[attempt % len(config.EM_HOSTS)]
        url = f"https://{host}/api/qt/clist/get"
        try:
            r = requests.get(url, params=params, headers=config.HTTP_HEADERS,
                             timeout=config.HTTP_TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            boards = _parse_eastmoney(payload)
            if boards:
                log.info("东财直连成功 host=%s 板块数=%d", host, len(boards))
                return boards
            last_err = DataSourceError(f"空结果 (host={host})")
        except Exception as e:           # 网络/JSON/字段任一失败都不致命,换 host 重试
            last_err = e
            log.warning("东财抓取失败 host=%s 第%d次: %s", host, attempt + 1, e)
        time.sleep(config.HTTP_BACKOFF ** attempt)
    raise DataSourceError(f"东财全部重试失败: {last_err}")


def _parse_eastmoney(payload: dict) -> dict[str, float]:
    """解析 clist 返回。f14=名称, f62=主力净流入(元) -> 亿元。"""
    data = (payload or {}).get("data")
    if not data:
        return {}
    diff = data.get("diff")
    # diff 多为 list;个别版本是 {"0": {...}, ...} 的 dict,这里统一成 list。
    items = diff.values() if isinstance(diff, dict) else (diff or [])
    out: dict[str, float] = {}
    for it in items:
        name = it.get("f14")
        raw = it.get("f62")
        if not name or raw in (None, "-", ""):
            continue
        try:
            out[str(name)] = float(raw) / 1e8     # 元 -> 亿元
        except (TypeError, ValueError):
            continue
    return out


# ----------------------------------------------------------------------
# 兜底数据源:akshare
# ----------------------------------------------------------------------
def _fetch_akshare(kind: str) -> dict[str, float]:
    """东财直连失败时用 akshare。列名随版本变,这里做模糊匹配。"""
    try:
        import akshare as ak
    except Exception as e:
        raise DataSourceError(f"akshare 未安装: {e}")

    sector_type = config.AK_SECTOR_TYPE[kind]
    df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type=sector_type)
    if df is None or df.empty:
        raise DataSourceError("akshare 返回空")

    cols = list(df.columns)
    name_col = next((c for c in cols if "名称" in str(c)), None)
    # 优先「今日主力净流入-净额」,模糊匹配「主力净流入」且含「净额」
    amt_col = next((c for c in cols if "主力净流入" in str(c) and "净额" in str(c)), None)
    if name_col is None or amt_col is None:
        raise DataSourceError(f"akshare 列名不识别: {cols}")

    out: dict[str, float] = {}
    for _, row in df.iterrows():
        name = row[name_col]
        try:
            out[str(name)] = float(row[amt_col]) / 1e8    # 元 -> 亿元
        except (TypeError, ValueError):
            continue
    if not out:
        raise DataSourceError("akshare 解析后为空")
    log.info("akshare 兜底成功 板块数=%d", len(out))
    return out


# ----------------------------------------------------------------------
# 主数据源:同花顺(海外可达)
# ----------------------------------------------------------------------
def _fetch_ths(kind: str) -> dict[str, float]:
    """同花顺 概念/行业 资金流(即时)→ {名称: 净额(亿)}。
    净额 = 流入资金 − 流出资金,单位已是「亿元」(无需换算)。"""
    try:
        import akshare as ak
    except Exception as e:
        raise DataSourceError(f"akshare 未安装: {e}")
    fn = ak.stock_fund_flow_concept if kind == "concept" else ak.stock_fund_flow_industry
    df = fn(symbol="即时")
    if df is None or df.empty:
        raise DataSourceError("同花顺返回空")
    cols = list(df.columns)
    # 同花顺把概念/行业名放在「行业」列;净额列名含「净额」
    name_col = next((c for c in cols if any(k in str(c) for k in ("名称", "行业", "概念"))), None)
    net_col = next((c for c in cols if "净额" in str(c)), None)
    if name_col is None or net_col is None:
        raise DataSourceError(f"同花顺列名不识别: {cols}")
    out: dict[str, float] = {}
    for _, r in df.iterrows():
        try:
            out[str(r[name_col])] = float(r[net_col])   # 已是亿元
        except (TypeError, ValueError):
            continue
    if not out:
        raise DataSourceError("同花顺解析后为空")
    return out


# ----------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------
def fetch_snapshot(kind: str | None = None) -> dict[str, float]:
    """抓一次板块资金流快照 {名称: 净流入(亿)}。按 PREFER_SOURCE 决定优先级,
    逐个尝试 同花顺→东财直连→akshare东财,全失败抛 DataSourceError。"""
    kind = kind or config.SECTOR_KIND
    sources = [
        ("同花顺", lambda: _fetch_ths(kind)),
        ("东财直连", lambda: _fetch_eastmoney(kind)),
        ("akshare东财", lambda: _fetch_akshare(kind)),
    ]
    if config.PREFER_SOURCE == "em":          # 国内想要东财"主力净流入"口径
        sources = sources[1:] + sources[:1]
    errs = []
    for name, fn in sources:
        try:
            data = fn()
            if data:
                log.info("数据源=%s 板块=%d", name, len(data))
                return data
        except Exception as e:
            log.warning("数据源[%s]失败: %s", name, e)
            errs.append(f"{name}: {e}")
    raise DataSourceError("所有数据源均失败 -> " + " | ".join(errs))


# ----------------------------------------------------------------------
# 全市场氛围条:涨跌家数 + 两市成交额
# ----------------------------------------------------------------------
def _fetch_breadth() -> tuple[int, int]:
    """全市场上涨/下跌家数(akshare 乐咕)。"""
    try:
        import akshare as ak
    except Exception as e:
        raise DataSourceError(f"akshare 未安装: {e}")
    df = ak.stock_market_activity_legu()
    m = {str(r.iloc[0]): r.iloc[1] for _, r in df.iterrows()}

    def pick(key):
        for k, v in m.items():
            if key in k:
                try:
                    return int(float(v))
                except (TypeError, ValueError):
                    return None
        return None

    up, down = pick("上涨"), pick("下跌")
    if up is None or down is None:
        raise DataSourceError(f"涨跌家数解析失败: {list(m)[:6]}")
    return up, down


def _fetch_turnover() -> float:
    """两市总成交额(亿元):新浪指数(海外可达)求和。
    不依赖字段位置——指数行里最大的数就是「成交额(元)」(点位~1e3、成交量~1e8、成交额~1e11)。"""
    url = "https://hq.sinajs.cn/list=" + ",".join(config.MARKET_TURNOVER_SINA)
    headers = {**config.HTTP_HEADERS, "Referer": "https://finance.sina.com.cn/"}
    r = requests.get(url, headers=headers, timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    total = 0.0
    for line in r.text.strip().splitlines():
        parts = line.split('"')
        if len(parts) < 2:
            continue
        nums = []
        for x in parts[1].split(","):
            try:
                nums.append(float(x))
            except ValueError:
                pass
        biggest = max(nums) if nums else 0.0
        if biggest > 1e9:          # 过滤只有点位/成交量的异常行,>10亿元才算成交额
            total += biggest
    if total <= 0:
        raise DataSourceError("新浪成交额解析为 0")
    return total / 1e8


def fetch_market_overview() -> dict:
    """全市场:{up, down, turnover(亿)}。涨跌家数必需;成交额可选(拿不到记 0,
    氛围条只显示涨跌家数,不影响主图)。"""
    up, down = _fetch_breadth()
    try:
        turnover = _fetch_turnover()
    except Exception as e:
        log.warning("成交额抓取失败(氛围条省略成交/量变): %s", e)
        turnover = 0.0
    return {"up": up, "down": down, "turnover": turnover}


if __name__ == "__main__":
    # 手动单测:python datasource.py
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    snap = fetch_snapshot()
    top = sorted(snap.items(), key=lambda kv: kv[1], reverse=True)
    print(f"\n共 {len(snap)} 个板块。主力净流入 Top10(亿元):")
    for n, v in top[:10]:
        print(f"  {n:<12} {v:+.2f}")
    print("流出 Top5(亿元):")
    for n, v in sorted(snap.items(), key=lambda kv: kv[1])[:5]:
        print(f"  {n:<12} {v:+.2f}")
