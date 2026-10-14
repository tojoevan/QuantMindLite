"""行情数据源（按品种路由 + 多源顺序回退，任一可用即用）。

**品种是第一层路由**（market 字段），决定代码怎么解析、走哪个源：

    A     股票      600519.SH / 000002.SZ      腾讯日线（前复权）
    etf   场内基金  510300 / 159915 / 161725   腾讯日线（前复权）
    bond  债券      113056（可转债）/ 019547（国债）  腾讯日线（不复权）
    fund  场外基金  012969 / 206006            东财历史单位净值（无 K 线）

**可达性约束（实测，务必遵守）**：线上 VPS 只有 IPv6 默认路由、无 IPv4 路由，
所以只有带 AAAA 记录的域名能连上。实测结论：
  ✅ qt.gtimg.cn（实时/名称）、proxy.finance.qq.com（日线 newfqkline、代码检索 smartbox）、
     api.fund.eastmoney.com（基金净值，须带 Referer）
  ❌ web.ifzq.gtimg.cn（腾讯**旧**日线地址，只有 A 记录）、money.finance.sina.com.cn、
     fund.eastmoney.com、smartbox.gtimg.cn —— 无 AAAA，直连必然 ENETUNREACH
  ⚠️ push2his.eastmoney.com 有 AAAA 但服务端拒答（RemoteDisconnected）
因此腾讯日线**必须走 proxy.finance.qq.com**，旧地址仅作其他网络环境下的兜底。

返回统一结构：[(date_str "YYYY-MM-DD", close_float), ...] 升序。
（场外基金为当日单位净值，含义与收盘价一致，下游按同一结构消费。）
"""
import re
import json
import time
import logging
import urllib.parse
import urllib.request
import ssl
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("datasource")

try:
    import akshare as ak
    HAS_AKSHARE = True
except Exception as e:  # pragma: no cover
    ak = None
    HAS_AKSHARE = False
    logger.warning("akshare 不可用：%s", e)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


# ---------------- 品种（market）与代码解析 ----------------
MARKET_LABELS = {"A": "股票", "etf": "场内基金", "fund": "场外基金", "bond": "债券"}
MARKET_HINTS = {
    "A": "如 600519.SH / 000002.SZ",
    "etf": "如 510300（沪深300ETF）/ 159915（创业板ETF）/ 161725（LOF）",
    "bond": "如 113056（可转债）/ 127045（深市转债）/ 019547（国债）",
    "fund": "如 012969 / 206006（场外基金，6 位数字）",
}
_MARKET_ALIAS = {
    "a": "A", "stock": "A", "gp": "A", "股票": "A",
    "etf": "etf", "lof": "etf", "场内基金": "etf", "基金": "etf",
    "fund": "fund", "otc": "fund", "otcfund": "fund", "场外基金": "fund",
    "bond": "bond", "zq": "bond", "债券": "bond",
}
# 腾讯 smartbox 的品种码 → 中文（仅用于展示，未收录的原样显示）
KIND_LABELS = {
    "GP-A": "股票 · A股", "GP-B": "股票 · B股", "GP-KCB": "股票 · 科创板",
    "GP-CYB": "股票 · 创业板", "GP-BJ": "股票 · 北交所",
    "ETF": "场内基金 · ETF", "LOF": "场内基金 · LOF",
    "ZQ-KZZ": "债券 · 可转债", "ZQ-GZ": "债券 · 国债", "ZQ-QYZ": "债券 · 企业债",
    # 场外基金（smartbox 用 jj 前缀 + 子类码）
    "KJ": "场外基金 · 联接", "QDII": "场外基金 · QDII",
    "HH": "场外基金 · 混合", "GP": "场外基金 · 股票型",
    "ZQ": "场外基金 · 债券型", "HB": "场外基金 · 货币",
}


def normalize_market(market: str) -> str:
    """把各种写法（A/股票/etf/fund/债券…）归一到四种取值之一，未知按 A 处理。"""
    return _MARKET_ALIAS.get((market or "").strip().lower(), "A")


def _suffix_tag(code: str) -> Optional[str]:
    """从显式后缀/前缀取市场标签：600519.SH / sh600519 / 113056.SZ / jj012969。"""
    s = (code or "").strip().lower()
    for suf, tag in ((".sh", "sh"), (".sz", "sz"), (".bj", "bj"), (".of", "jj")):
        if s.endswith(suf):
            return tag
    for pre, tag in (("sh", "sh"), ("sz", "sz"), ("bj", "bj"), ("jj", "jj")):
        if s.startswith(pre):
            return tag
    return None


def _guess_tag(digits: str, market: str) -> str:
    """按品种规则猜市场标签。猜错也不要紧：调用方会拿另一个市场再试一次。"""
    if market == "fund":
        return "jj"
    if market == "etf":
        # 场内基金：沪市 5xxxxx（ETF/LOF），深市 15xxxx/16xxxx/18xxxx
        return "sh" if digits.startswith("5") else "sz"
    if market == "bond":
        p3 = digits[:3]
        # 沪市：110/111/113/118 转债、132 可交换债、122 企业债、019/020/018/010 国债
        if p3 in ("110", "111", "113", "118", "122", "132", "019", "020", "018", "010"):
            return "sh"
        # 深市：123/127/128 转债、120 可交换债、112 企业债、100-108 国债
        if p3 in ("120", "123", "127", "128", "112", "100", "101", "102", "103", "108"):
            return "sz"
        return "sh"
    # A 股：沿用原有规则（6/9 → 沪，0/2/3 → 深，其余按沪处理）
    if digits.startswith(("6", "9")):
        return "sh"
    if digits.startswith(("0", "2", "3")):
        return "sz"
    return "sh"


def resolve_symbol(code: str, market: str = "A"):
    """把各种写法解析成 (digits, tag)，tag ∈ sh/sz/bj/jj。解析不出返回 None。
    显式后缀优先；无后缀时按品种规则猜。场外基金恒用 jj（与场内代码书写可能撞车）。"""
    market = normalize_market(market)
    m = re.search(r"(\d{6})", code or "")
    if not m:
        return None
    digits = m.group(1)
    tag = _suffix_tag(code) or _guess_tag(digits, market)
    if market == "fund":
        tag = "jj"
    elif tag == "jj":
        tag = _guess_tag(digits, market)
    return digits, tag


def parse_symbol(code: str, market: str = "A"):
    """兼容旧签名：返回 (digits, secid_prefix)，secid_prefix 1=沪 0=深（东财 secid 用）。"""
    got = resolve_symbol(code, market)
    if not got:
        return None
    digits, tag = got
    return digits, ("1" if tag == "sh" else "0")


def _symbol_of(digits: str, tag: str) -> str:
    return f"{tag}{digits}"


def _fallback_tags(digits: str, market: str, tag: str):
    """场内品种（ETF/债券）代码段有交叉，首选市场取不到数据时再试另一个。"""
    if market in ("etf", "bond") and tag in ("sh", "sz"):
        return [tag, "sz" if tag == "sh" else "sh"]
    return [tag]


def _urlopen(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return resp.read().decode("utf-8", "ignore")


# 腾讯日线：**主机顺序即优先级**。proxy.finance.qq.com 有 AAAA，是本 VPS 唯一可达的口；
# web.ifzq.gtimg.cn 只有 A 记录（本 VPS 不可达），保留它只为其他网络环境下兜底。
# 两个地址的路径与参数不同，但返回结构一致：data.{symbol}.qfqday 或 .day。
_TENCENT_KLINE = (
    ("proxy.finance.qq.com", "/ifzqgtimg/appstock/app/newfqkline/get"),
    ("web.ifzq.gtimg.cn", "/appstock/app/fqkline/get"),
)


def _fetch_tencent(tag, digits, days):
    """腾讯日线。qfqday=前复权（股票/ETF），day=不复权（债券无复权数据）。
    返回 [(date, close), ...] 升序。"""
    symbol = _symbol_of(digits, tag)
    count = max(120, int(days) + 20)
    errors = []
    for host, path in _TENCENT_KLINE:
        url = f"https://{host}{path}?param={symbol},day,,,{count},qfq"
        try:
            j = json.loads(_urlopen(url))
        except Exception as e:
            errors.append(f"{host}:{e}")
            continue
        data = (j or {}).get("data", {}) or {}
        node = data.get(symbol) or (next(iter(data.values())) if data else {})
        klines = node.get("qfqday") or node.get("day") or []
        out = []
        for k in klines:
            try:
                d = str(k[0])
                c = float(k[2])  # [日期, 开, 收, 高, 低, 量] -> 索引2为收盘
            except Exception:
                continue
            if c > 0:
                out.append((d, c))
        if out:
            out.sort(key=lambda x: x[0])
            return out
        errors.append(f"{host}:无数据")
    raise ValueError("腾讯未返回数据 -> " + " | ".join(errors))


def _fetch_akshare(digits, start_s, end_s, days):
    if not HAS_AKSHARE:
        raise RuntimeError("akshare 未安装")
    df = ak.stock_zh_a_hist(
        symbol=digits, period="daily",
        start_date=start_s, end_date=end_s, adjust="qfq",
    )
    out = []
    for _, row in df.iterrows():
        d = str(row["日期"])
        try:
            c = float(row["收盘"])
        except Exception:
            continue
        if c > 0:
            out.append((d, c))
    out.sort(key=lambda x: x[0])
    if not out:
        raise ValueError("akshare 未返回数据")
    return out


# ---------------- 场外基金净值（东财） ----------------
# ⚠️ 必须带 Referer（fundf10.eastmoney.com），否则接口返回 ErrCode -999（实测）；
#    且每页 20 条最稳，pageSize 放大到几百会返回空列表。
_FUND_NAV_API = "https://api.fund.eastmoney.com/f10/lsjz"
_FUND_PAGE_SIZE = 20
_FUND_MAX_PAGES = 300
_FUND_HEADERS = {**_HEADERS, "Referer": "https://fundf10.eastmoney.com/"}


def _fetch_fund_nav(digits, days):
    """场外基金历史单位净值（DWJZ）→ [(date, nav), ...] 升序。
    分红/拆分等无净值日期跳过。"""
    end = datetime.now()
    start = end - timedelta(days=max(30, int(days)))
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    out = []
    for page in range(1, _FUND_MAX_PAGES + 1):
        url = (f"{_FUND_NAV_API}?fundCode={digits}&pageIndex={page}"
               f"&pageSize={_FUND_PAGE_SIZE}&startDate={start_s}&endDate={end_s}")
        lst, last_err = None, None
        for attempt in range(1, 4):          # 东财偶发限速，重试 3 次
            try:
                req = urllib.request.Request(url, headers=_FUND_HEADERS)
                with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
                    j = json.loads(resp.read().decode("utf-8", "ignore"))
                lst = (j.get("Data") or {}).get("LSJZList") or []
                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(1.0 * attempt)
        if last_err is not None:
            raise RuntimeError(f"东财基金净值请求失败：{last_err}")
        if not lst:
            break
        for r in lst:
            d = (r.get("FSRQ") or "").strip()
            try:
                c = float((r.get("DWJZ") or "").strip())
            except ValueError:
                continue                      # 分红/拆分等无净值日期
            if d and c > 0:
                out.append((d, c))
        if len(lst) < _FUND_PAGE_SIZE:
            break
        time.sleep(0.3)                       # 页间小延迟，避免被限速
    out.sort(key=lambda x: x[0])
    if not out:
        raise ValueError(f"东财未返回基金净值（{digits}）")
    return out


def _fetch_eastmoney(digits, secid_prefix, start_s, end_s):
    secid = f"{secid_prefix}.{digits}"
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?fields1=f1,f2,f3&fields2=f51,f53&klt=101&fqt=1&secid={secid}"
        f"&beg={start_s}&end={end_s}&ut=7eea3edcaed734bea9cbfc24409ed989"
    )
    raw = _urlopen(url)
    j = json.loads(raw)
    if not j.get("data") or not j["data"].get("klines"):
        raise ValueError("东方财富未返回数据")
    out = []
    for k in j["data"]["klines"]:
        parts = k.split(",")
        try:
            d = parts[0]
            c = float(parts[2])
        except Exception:
            continue
        if c > 0:
            out.append((d, c))
    out.sort(key=lambda x: x[0])
    if not out:
        raise ValueError("东方财富返回为空")
    return out


def _decode_bytes(raw: bytes) -> str:
    """把行情/名称接口的响应字节按编码解码：优先 utf-8，失败回退 gbk/gb18030（中文接口多为 GBK）。"""
    try:
        return raw.decode("utf-8")
    except Exception:
        try:
            return raw.decode("gb18030")
        except Exception:
            return raw.decode("latin-1", "ignore")


def _tencent_name(symbol: str) -> Optional[str]:
    """腾讯实时报价里取名称（v_sh600519="1~贵州茅台~600519~..."，~ 分隔，第 2 段为名称）。
    该接口为 GBK 编码，必须按字节读取后再以 gbk 解码。场外基金用 jj 前缀同样适用。"""
    try:
        req = urllib.request.Request(f"https://qt.gtimg.cn/q={symbol}", headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            raw = resp.read()
        m = re.search(r'"([^"]*)"', raw.decode("gbk", "ignore"))
        if m:
            fields = m.group(1).split("~")
            if len(fields) > 1 and fields[1].strip():
                return fields[1].strip()
    except Exception as e:
        logger.warning("腾讯名称查询失败(%s)：%s", symbol, e)
    return None


def fetch_symbol_name(code: str, market: str = "A") -> Optional[str]:
    """按品种查询标的中文名称（证券缩写）。腾讯实时接口 → smartbox 检索 → akshare。
    查询失败返回 None（调用方应忽略，保留原有项目名）。"""
    market = normalize_market(market)
    got = resolve_symbol(code, market)
    if not got:
        return None
    digits, tag = got

    for tg in _fallback_tags(digits, market, tag):
        nm = _tencent_name(_symbol_of(digits, tg))
        if nm:
            return nm

    # smartbox 检索兜底（可覆盖代码前缀猜错的情况）
    for cand in lookup_symbol(digits):
        if cand.get("code") == digits and cand.get("name"):
            return cand["name"]

    # A 股再兜一层 akshare
    if market == "A" and HAS_AKSHARE:
        try:
            info = ak.stock_individual_info_em(symbol=digits)
            nm = info[info["item"] == "股票简称"]["value"].iloc[0]
            if nm:
                return str(nm).strip()
        except Exception as e:
            logger.warning("akshare 名称查询失败：%s", e)
    return None


def fetch_stock_name(code: str, market: str = "A") -> Optional[str]:
    """兼容旧名。"""
    return fetch_symbol_name(code, market)


def fetch_hist(code: str, market: str = "A", days: int = 250):
    """按品种拉取历史序列，返回 (rows, source)。
    rows 为 [(date, close), ...] 升序（场外基金为当日单位净值）；
    source 为数据源中文名。全部失败抛出 RuntimeError。"""
    market = normalize_market(market)
    got = resolve_symbol(code, market)
    if not got:
        raise ValueError(
            f"无法识别代码「{code}」—— {MARKET_LABELS[market]}的写法：{MARKET_HINTS[market]}")
    digits, tag = got
    errors = []

    # 场外基金：走东财历史净值（无 K 线）
    if market == "fund":
        try:
            return _fetch_fund_nav(digits, max(int(days), 365)), "东财基金净值"
        except Exception as e:
            errors.append(f"东财基金净值:{e}")

    # 场内品种（股票 / ETF / LOF / 债券）：腾讯日线，必要时换市场再试一次
    for tg in _fallback_tags(digits, market, tag):
        try:
            return _fetch_tencent(tg, digits, days), "腾讯行情"
        except Exception as e:
            errors.append(f"腾讯行情({tg}):{e}")

    # A 股兜底：akshare → 东方财富直连（后者在本 VPS 会被拒答，仅作其他环境兜底）
    if market == "A":
        end = datetime.now()
        start = end - timedelta(days=int(days) * 2 + 10)
        start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        try:
            return _fetch_akshare(digits, start_s, end_s, days), "akshare"
        except Exception as e:
            errors.append(f"akshare:{e}")
        try:
            prefix = "1" if tag == "sh" else "0"
            return _fetch_eastmoney(digits, prefix, start_s, end_s), "东方财富"
        except Exception as e:
            errors.append(f"东方财富:{e}")

    raise RuntimeError("所有行情数据源均失败 -> " + " | ".join(errors))


def fetch_a_hist(code: str, days: int = 250):
    """兼容旧名：等同 fetch_hist(code, "A", days)。"""
    return fetch_hist(code, "A", days)


# ---------------- 代码检索（新建标的时自动带出名称与品种） ----------------
_SMARTBOX_API = "https://proxy.finance.qq.com/ifzqgtimg/appstock/smartbox/search/get?q="
_EM_SUGGEST_API = "https://searchadapter.eastmoney.com/api/suggest/get?input="


def _mk_candidate(tag: str, code: str, name: str, kind: str) -> dict:
    """把「市场标签 + 代码 + 名称 + 品种码」整理成统一候选结构。"""
    tag = (tag or "").lower()
    if tag == "jj":
        market = "fund"
    elif kind.startswith("GP"):
        market = "A"
    elif kind.startswith("ZQ"):
        market = "bond"
    elif kind in ("ETF", "LOF", "JJ"):
        market = "etf"
    else:
        market = "A"
    suffix = {"sh": ".SH", "sz": ".SZ", "bj": ".BJ"}.get(tag, "")
    # 品种码未收录时退回品种名，不把英文码直接甩给用户
    kind_label = KIND_LABELS.get(kind) or MARKET_LABELS[market]
    return {
        "code": code,
        "name": name,
        "market": market,
        "market_label": MARKET_LABELS[market],
        "tag": tag,
        "kind": kind,
        "kind_label": kind_label,
        "display": f"{code}{suffix}",
    }


def _lookup_eastmoney(q: str, limit: int) -> list:
    """东财检索兜底（实测本 VPS 可达，返回 Classify / MktNum / QuoteID）。"""
    url = f"{_EM_SUGGEST_API}{urllib.parse.quote(q)}&type=14&count=10"
    try:
        rows = ((json.loads(_urlopen(url, timeout=12)).get("QuotationCodeTable") or {})
                .get("Data")) or []
    except Exception as e:
        logger.warning("东财代码检索失败：%s", e)
        return []
    out = []
    for r in rows[:limit]:
        classify = (r.get("Classify") or "").strip()
        market = {"Stock": "A", "Fund": "etf", "OTCFUND": "fund",
                  "Bond": "bond"}.get(classify, "A")
        tag = {"1": "sh", "0": "sz", "150": "jj"}.get(str(r.get("MktNum") or ""), "")
        if market == "fund":
            tag = "jj"
        out.append(_mk_candidate(tag, r.get("Code") or "", r.get("Name") or "",
                                 classify or (r.get("SecurityTypeName") or "")))
    return out


def lookup_symbol(q: str, limit: int = 8) -> list:
    """按代码或名称检索标的，返回候选列表（含**权威的市场前缀与品种**）。
    主源腾讯 smartbox（实测本 VPS 可达），失败回退东财检索；两者都失败返回 []。

    用途：新建标的时自动带出名称与品种；同一代码有歧义时（如 019547 既是沪市国债
    又是某场外基金、161725 既是深市 LOF 又是场外基金）把候选全部列出让用户选。
    """
    q = (q or "").strip()
    if not q:
        return []
    try:
        j = json.loads(_urlopen(_SMARTBOX_API + urllib.parse.quote(q), timeout=12))
        stocks = (j.get("data") or {}).get("stock") or []
    except Exception as e:
        logger.warning("腾讯 smartbox 检索失败：%s", e)
        return _lookup_eastmoney(q, limit)
    out = []
    for item in stocks[:limit]:
        try:
            tag, code, name = item[0], item[1], item[2]
            kind = item[4] if len(item) > 4 else ""
        except Exception:
            continue
        if code:
            out.append(_mk_candidate(tag, code, name, kind))
    return out or _lookup_eastmoney(q, limit)


# ---------------- 大盘指数（圆桌讨论用，独立于个股逻辑） ----------------
# key -> (显示名, 东财 secid)
INDEX_SYMBOLS = {
    "sh000001": ("上证指数", "1.000001"),
    "sz399001": ("深证成指", "0.399001"),
    "sz399006": ("创业板指", "0.399006"),
}


def _tencent_quote_parse(raw: bytes) -> dict:
    """解析 qt.gtimg.cn 实时报价响应（GBK），返回 {symbol: (name, price, prev_close, date)}。

    字段布局（腾讯行情通用约定）：[1]名称 [3]现价 [4]昨收 [30]时间(YYYYMMDDHHMMSS)。
    注意：[4] 是昨收而非涨跌额，涨跌需自行按 (现价-昨收)/昨收 计算。
    """
    out = {}
    for line in raw.decode("gbk", "ignore").strip().splitlines():
        if "=" not in line:
            continue
        var, val = line.split("=", 1)
        var = var.strip()
        if not var.startswith("v_"):
            continue
        symbol = var[2:]
        f = val.strip().strip('"').split("~")
        if len(f) < 33:
            continue
        try:
            price = float(f[3])
            prev = float(f[4])
        except (ValueError, IndexError):
            continue
        if price <= 0 or prev <= 0:
            continue
        ts = f[30] if len(f) > 30 else ""
        date = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 and ts[:8].isdigit() else ""
        out[symbol] = (f[1], price, prev, date)
    return out


def _fetch_indices_tencent() -> dict:
    """腾讯实时报价一次拉全部指数；批量失败时逐个重试。返回 {symbol: (...)}。"""
    url = f"https://qt.gtimg.cn/q={','.join(INDEX_SYMBOLS.keys())}"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
        out = _tencent_quote_parse(resp.read())
    if len(out) < len(INDEX_SYMBOLS):
        for sym in INDEX_SYMBOLS:
            if sym in out:
                continue
            try:
                req = urllib.request.Request(f"https://qt.gtimg.cn/q={sym}", headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
                    out.update(_tencent_quote_parse(resp.read()))
            except Exception as e:
                logger.warning("腾讯指数 %s 单个重试失败：%s", sym, e)
    return out


def _fetch_index_eastmoney(symbol, secid):
    """东方财富日线（指数，兜底源）。返回 [(date, close), ...] 升序。"""
    end = datetime.now()
    start = end - timedelta(days=15)
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?fields1=f1,f2,f3&fields2=f51,f53&klt=101&fqt=1&secid={secid}"
        f"&beg={start.strftime('%Y%m%d')}&end={end.strftime('%Y%m%d')}"
        "&ut=7eea3edcaed734bea9cbfc24409ed989"
    )
    raw = _urlopen(url)
    j = json.loads(raw)
    if not j.get("data") or not j["data"].get("klines"):
        raise ValueError("东方财富未返回指数数据")
    out = []
    for k in j["data"]["klines"]:
        parts = k.split(",")
        try:
            c = float(parts[2])
        except Exception:
            continue
        if c > 0:
            out.append((parts[0], c))
    out.sort(key=lambda x: x[0])
    if not out:
        raise ValueError("东方财富指数返回为空")
    return out


def fetch_index_quotes():
    """三大指数最新快照。返回 [{key,name,date,close,prev_close,chg_pct,source}, ...]。
    主源：腾讯实时报价（qt.gtimg.cn，现价+昨现算涨跌，本 VPS IPv6 可达）；
    兜底：东方财富日线。单个指数失败不影响其余（close=None，调用方展示“数据获取失败”）。"""
    quotes = {}
    try:
        quotes = _fetch_indices_tencent()
    except Exception as e:
        logger.warning("腾讯指数报价失败：%s", e)
    out = []
    for symbol, (name, secid) in INDEX_SYMBOLS.items():
        item = {"key": symbol, "name": name, "date": None, "close": None,
                "prev_close": None, "chg_pct": None, "source": None}
        q = quotes.get(symbol)
        if q:
            _, price, prev, date = q
            item.update(date=date, close=price, prev_close=prev,
                        chg_pct=round((price - prev) / prev * 100, 2), source="腾讯")
        else:
            try:
                rows = _fetch_index_eastmoney(symbol, secid)
                item["date"], item["close"] = rows[-1]
                if len(rows) >= 2 and rows[-2][1]:
                    item["prev_close"] = rows[-2][1]
                    item["chg_pct"] = round((rows[-1][1] - rows[-2][1]) / rows[-2][1] * 100, 2)
                item["source"] = "东方财富"
            except Exception as e:
                logger.warning("指数 %s 东财兜底失败：%s", symbol, e)
        out.append(item)
    return out
