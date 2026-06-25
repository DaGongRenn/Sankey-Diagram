# -*- coding: utf-8 -*-
"""probe_sources.py —— 在 GitHub 海外 runner 上探测哪些 A股数据源可达。
输出里哪个 OK,就用哪个当数据源。用法:python probe_sources.py
"""
import traceback
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def http(name, url, referer=None):
    h = dict(UA)
    if referer:
        h["Referer"] = referer
    try:
        r = requests.get(url, headers=h, timeout=12)
        print(f"[{name:14}] HTTP {r.status_code}  bytes={len(r.content)}  head={r.text[:70]!r}")
    except Exception as e:
        print(f"[{name:14}] CONN-FAIL: {type(e).__name__}: {e}")


print("########## 1) 原始域名可达性(状态码=连得上,CONN-FAIL=连不上) ##########")
http("东财push2", "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&fs=m:90+t:3&fields=f14&ut=b2884a393a59ad64002292a3e90d46a5", "https://data.eastmoney.com/")
http("东财datacenter", "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DMSK_TS_STOCKNEW&pageSize=1", "https://data.eastmoney.com/")
http("同花顺10jqka", "https://data.10jqka.com.cn/funds/gnzjl/", "https://data.10jqka.com.cn/")
http("乐咕legulegu", "https://www.legulegu.com/stockdata/market-activity")
http("腾讯gtimg", "https://qt.gtimg.cn/q=sh000001")
http("新浪sinajs", "https://hq.sinajs.cn/list=sh000001", "https://finance.sina.com.cn/")


def ak_try(title, fn):
    print(f"\n########## {title} ##########")
    try:
        df = fn()
        print("OK  rows:", len(df), " cols:", list(df.columns))
        print(df.head(5).to_string()[:1600])
    except Exception:
        traceback.print_exc()


import akshare as ak
ak_try("2) 同花顺 概念资金流 即时 (stock_fund_flow_concept)", lambda: ak.stock_fund_flow_concept(symbol="即时"))
ak_try("3) 同花顺 行业资金流 即时 (stock_fund_flow_industry)", lambda: ak.stock_fund_flow_industry(symbol="即时"))
ak_try("4) 乐咕 涨跌家数 (stock_market_activity_legu)", lambda: ak.stock_market_activity_legu())

print("\n########## 5) 东财板块资金流·多 host(realtime board flow = push2,看是否全封) ##########")
_ut = "b2884a393a59ad64002292a3e90d46a5"
_clist = ("/api/qt/clist/get?pn=1&pz=2&po=1&np=1&fltt=2&invt=2&fid=f62"
          "&fs=m:90+t:3&fields=f12,f14,f62&ut=" + _ut)
for _h in ["push2.eastmoney.com", "7.push2.eastmoney.com", "48.push2.eastmoney.com",
           "16.push2.eastmoney.com", "push2delay.eastmoney.com"]:
    http("EM:" + _h, "https://" + _h + _clist, "https://data.eastmoney.com/")

print("\n探测完毕。把整段输出发我,我据此判断东财能否取回主力净流入口径。")
