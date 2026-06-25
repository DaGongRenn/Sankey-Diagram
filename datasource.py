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
import random
import time
import requests

import config

log = logging.getLogger("datasource")


class DataSourceError(Exception):
    pass


def _filter_boards(data: dict[str, float]) -> dict[str, float]:
    """剔除黑名单里的「非概念/产业」板块(风格/宽基指数/资金属性/风险警示等)。
    东财 t:3、同花顺概念列表里都混了这类伪板块,统一在此过滤。"""
    bl = config.BLOCKLIST_KEYWORDS
    out = {n: v for n, v in (data or {}).items() if not any(k in n for k in bl)}
    removed = len(data or {}) - len(out)
    if removed:
        log.info("黑名单过滤掉 %d 个非概念板块,剩 %d", removed, len(out))
    return out


# ----------------------------------------------------------------------
# 主数据源:东方财富 clist
# ----------------------------------------------------------------------
def _parse_eastmoney(payload: dict) -> tuple[dict[str, float], int | None]:
    """解析 clist 一页。f14=名称, f62=主力净流入(元)->亿元。返回 (boards, total)。"""
    data = (payload or {}).get("data")
    if not data:
        return {}, None
    diff = data.get("diff")
    items = list(diff.values()) if isinstance(diff, dict) else list(diff or [])
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
    return out, data.get("total")


def _em_page(params: dict) -> tuple[dict[str, float], int | None]:
    """抓 clist 一页:打乱节点循环重试(东财对境外时好时坏)。返回 (boards, total)。"""
    hosts = list(config.EM_HOSTS)
    random.shuffle(hosts)
    last_err = None
    for attempt in range(config.EM_MAX_TRIES):
        host = hosts[attempt % len(hosts)]
        try:
            r = requests.get(f"https://{host}/api/qt/clist/get", params=params,
                             headers=config.HTTP_HEADERS, timeout=config.HTTP_TIMEOUT)
            r.raise_for_status()
            boards, total = _parse_eastmoney(r.json())
            if boards:
                return boards, total
            last_err = DataSourceError(f"空结果 (host={host})")
        except Exception as e:
            last_err = e
            log.warning("东财页失败 host=%s 第%d次: %s", host, attempt + 1, str(e)[:80])
        time.sleep(0.4)
    raise DataSourceError(f"东财页 {config.EM_MAX_TRIES} 次全失败: {last_err}")


def _fetch_eastmoney(kind: str) -> dict[str, float]:
    """翻页抓全东财概念/行业板块主力净流入。clist 单页上限 100,需逐页累加到 total。"""
    fs = config.EM_FS[kind]
    base = {"pz": config.EM_PAGE_SIZE, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f62", "fs": fs, "fields": config.EM_FIELDS, "ut": config.EM_UT}
    merged: dict[str, float] = {}
    total = None
    for pn in range(1, config.EM_MAX_PAGES + 1):
        boards, t = _em_page({**base, "pn": pn, "_": int(time.time() * 1000)})
        if t:
            total = t
        merged.update(boards)
        if not boards or (total and len(merged) >= total):
            break
    if not merged:
        raise DataSourceError("东财翻页后仍无数据")
    log.info("东财抓全 %d/%s 板块", len(merged), total)
    return merged


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
def fetch_snapshot(kind: str | None = None) -> tuple[dict[str, float], str]:
    """抓一次板块资金流快照,返回 (data, source)。source ∈ {'em','ths'}。
    默认 em 优先(东财主力净流入,口径准、概念干净→渲染走自动 Top-N);
    东财整体拿不到才回退 ths(同花顺净额→渲染套白名单滤掉宽泛大类)。
    PREFER_SOURCE=ths 则只用同花顺。来源会被渲染端用来决定显示方式。"""
    kind = kind or config.SECTOR_KIND
    em = [("东财直连", lambda: _fetch_eastmoney(kind)),
          ("akshare东财", lambda: _fetch_akshare(kind))]
    ths = [("同花顺", lambda: _fetch_ths(kind))]
    plan = [("ths", ths)] if config.PREFER_SOURCE == "ths" else [("em", em), ("ths", ths)]
    errs = []
    for src, methods in plan:
        for name, fn in methods:
            try:
                data = _filter_boards(fn())
                if data:
                    log.info("数据源=%s(%s) 板块=%d", name, src, len(data))
                    return data, src
            except Exception as e:
                log.warning("数据源[%s]失败: %s", name, str(e)[:120])
                errs.append(f"{name}: {str(e)[:60]}")
    raise DataSourceError("数据源全失败 -> " + " | ".join(errs))


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


def fetch_market_main_net() -> float:
    """全市场(沪深)主力净流入(亿元):东财大盘资金流最新一行。best-effort,失败抛错。
    用于桑基图「其他」节点 = 全市场主力净流入 − 前十净额。"""
    try:
        import akshare as ak
    except Exception as e:
        raise DataSourceError(f"akshare 未安装: {e}")
    df = ak.stock_market_fund_flow()
    if df is None or df.empty:
        raise DataSourceError("大盘资金流为空")
    col = next((c for c in df.columns if "主力净流入" in str(c) and "净额" in str(c)), None)
    if col is None:
        raise DataSourceError(f"大盘资金流列名不识别: {list(df.columns)}")
    raw = float(df.iloc[-1][col])
    val = raw / 1e8 if abs(raw) > 1e5 else raw       # 元→亿(量级 ~1e10 元;已是亿则不除)
    log.info("全市场主力净流入 raw=%s → %.1f亿", raw, val)
    return val


if __name__ == "__main__":
    # 手动单测:python datasource.py
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    snap, src = fetch_snapshot()
    print("来源:", src)
    top = sorted(snap.items(), key=lambda kv: kv[1], reverse=True)
    print(f"\n共 {len(snap)} 个板块。主力净流入 Top10(亿元):")
    for n, v in top[:10]:
        print(f"  {n:<12} {v:+.2f}")
    print("流出 Top5(亿元):")
    for n, v in sorted(snap.items(), key=lambda kv: kv[1])[:5]:
        print(f"  {n:<12} {v:+.2f}")
