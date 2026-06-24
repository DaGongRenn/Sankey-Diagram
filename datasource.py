# -*- coding: utf-8 -*-
"""
datasource.py —— 板块「主力净流入」快照数据源(单位:亿元)。

优先:东方财富 push2 clist 接口(多 host 轮换 + 重试 + 头部伪装)。
兜底:akshare stock_sector_fund_flow_rank(东财直连全失败时)。

对外只暴露一个函数:
    fetch_snapshot(kind="concept") -> dict[str, float]
返回 {板块名: 当日累计主力净流入(亿元)};失败抛 DataSourceError。

注意:主力净流入是「当日累计」值——每次拿到的是截至此刻的累计净额,
把不同时刻的快照按时间拼起来,就是盘中演变曲线。
"""
from __future__ import annotations
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
# 对外入口
# ----------------------------------------------------------------------
def fetch_snapshot(kind: str | None = None) -> dict[str, float]:
    """抓一次快照。先东财直连,失败再 akshare;都失败抛 DataSourceError。"""
    kind = kind or config.SECTOR_KIND
    try:
        return _fetch_eastmoney(kind)
    except DataSourceError as e:
        log.warning("东财直连失败,转 akshare 兜底: %s", e)
        return _fetch_akshare(kind)


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
    """两市总成交额(亿元):东财 ulist 取上证/深证/北证指数 f6(元)求和。"""
    secids = ",".join(config.MARKET_TURNOVER_SECIDS)
    last_err = None
    for attempt in range(config.HTTP_RETRIES):
        host = config.EM_HOSTS[attempt % len(config.EM_HOSTS)]
        url = f"https://{host}/api/qt/ulist.np/get"
        params = {"secids": secids, "fields": "f6", "fltt": 2, "invt": 2,
                  "ut": config.EM_UT, "_": int(time.time() * 1000)}
        try:
            r = requests.get(url, params=params, headers=config.HTTP_HEADERS,
                             timeout=config.HTTP_TIMEOUT)
            r.raise_for_status()
            diff = (r.json().get("data") or {}).get("diff") or []
            items = diff.values() if isinstance(diff, dict) else diff
            total = 0.0
            for it in items:
                v = it.get("f6")
                if v not in (None, "-", ""):
                    total += float(v)
            if total > 0:
                return total / 1e8
            last_err = DataSourceError("成交额为 0")
        except Exception as e:
            last_err = e
        time.sleep(config.HTTP_BACKOFF ** attempt)
    raise DataSourceError(f"成交额抓取失败: {last_err}")


def fetch_market_overview() -> dict:
    """全市场:{up, down, turnover(亿)}。任一失败抛 DataSourceError(采集端 best-effort)。"""
    up, down = _fetch_breadth()
    turnover = _fetch_turnover()
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
