"""A股行情数据源（多源顺序回退，任一可用即用）。

优先级：腾讯 gtimg（前复权，最稳） → 新浪日线 → akshare → 东方财富直连（兜底）。
返回统一结构：[(date_str "YYYY-MM-DD", close_float), ...] 升序。

注意：东方财富 / akshare 在部分云服务器出口 IP 会被反爬断连，因此把
腾讯、新浪这类“对服务器 IP 友好”的源放在前面。
"""
import re
import json
import logging
import urllib.request
import ssl
from datetime import datetime, timedelta

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


def parse_symbol(code: str):
    """识别常见写法：600519.SH / sh600519 / 600519。
    返回 (digits, secid_prefix) ，secid_prefix: 1=上交所 0=深交所。"""
    code = (code or "").strip()
    if not code:
        return None
    m = re.search(r"(\d{6})", code)
    if not m:
        return None
    digits = m.group(1)
    head = code.lower().replace(digits, "")
    if head.startswith("sh") or digits.startswith(("6", "9")):
        secid_prefix = "1"
    elif head.startswith("sz") or digits.startswith(("0", "2", "3")):
        secid_prefix = "0"
    else:
        secid_prefix = "1"
    return digits, secid_prefix


def _market_tag(secid_prefix: str) -> str:
    return "sh" if secid_prefix == "1" else "sz"


def _urlopen(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return resp.read().decode("utf-8", "ignore")


def _fetch_tencent(digits, secid_prefix, days):
    """腾讯 gtimg 日线（前复权 qfqday）。返回 [(date, close), ...]。"""
    symbol = f"{_market_tag(secid_prefix)}{digits}"
    count = max(120, int(days) + 20)
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,{count},qfq"
    )
    raw = _urlopen(url)
    j = json.loads(raw)
    data = (j or {}).get("data", {}) or {}
    node = next(iter(data.values())) if data else {}
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
    out.sort(key=lambda x: x[0])
    if not out:
        raise ValueError("腾讯未返回数据")
    return out


def _fetch_sina(digits, secid_prefix, days):
    """新浪日线（不复权）。返回 [(date, close), ...]。"""
    symbol = f"{_market_tag(secid_prefix)}{digits}"
    count = max(120, int(days) + 20)
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&datalen={count}"
    )
    raw = _urlopen(url)
    arr = json.loads(raw)
    out = []
    for it in arr:
        try:
            d = str(it["day"])
            c = float(it["close"])
        except Exception:
            continue
        if c > 0:
            out.append((d, c))
    out.sort(key=lambda x: x[0])
    if not out:
        raise ValueError("新浪未返回数据")
    return out


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


def fetch_a_hist(code: str, days: int = 250):
    """拉取 A股日线（前复权优先），返回 (rows, source)。
    rows 为 [(date, close), ...] 升序；source 为实际命中的源名。
    多源顺序尝试：腾讯 → 新浪 → akshare → 东方财富。全失败抛出 RuntimeError。"""
    parsed = parse_symbol(code)
    if not parsed:
        raise ValueError("无法识别股票代码，示例：600519.SH / sh600519 / 600519")
    digits, secid_prefix = parsed
    end = datetime.now()
    start = end - timedelta(days=int(days) * 2 + 10)
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    errors = []

    # 1) 腾讯（最稳，前复权）
    try:
        return _fetch_tencent(digits, secid_prefix, days), "tencent"
    except Exception as e:
        errors.append(f"腾讯:{e}")

    # 2) 新浪
    try:
        return _fetch_sina(digits, secid_prefix, days), "sina"
    except Exception as e:
        errors.append(f"新浪:{e}")

    # 3) akshare
    try:
        return _fetch_akshare(digits, start_s, end_s, days), "akshare"
    except Exception as e:
        errors.append(f"akshare:{e}")

    # 4) 东方财富直连兜底
    try:
        return _fetch_eastmoney(digits, secid_prefix, start_s, end_s), "eastmoney"
    except Exception as e:
        errors.append(f"东方财富:{e}")

    raise RuntimeError("所有行情数据源均失败 -> " + " | ".join(errors))
