"""资讯工作台 · RSS/Atom 抓取与解析（纯标准库）。

为什么要中继：quant-web 所在 VPS（<生产服务器 IPv6（已脱敏）>）只有 IPv6 出站、无 IPv4
路由，而绝大多数资讯源只有 A 记录，直连必然失败。故统一经 Cloudflare 中继
（relay.kapibala.icu，见 ../rss-relay）取回原文。

边界：中继只做「带密钥与白名单的管道」，解析、去重、入库全部在本模块完成。
注意 llm.py 在导入时把 socket.getaddrinfo 强制成 AF_INET6（本机 IPv4 出口不可达），
中继域名有 AAAA，因此不受影响。
"""
import html
import hashlib
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from db import NewsFeed, NewsItem, Setting

CN_TZ = timedelta(hours=8)
USER_AGENT = "quant-web-news/1.0 (+https://quant.study3d.com)"

_SSL_CTX = ssl.create_default_context()

RELAY_URL_KEY = "news.relay_url"
RELAY_KEY_KEY = "news.relay_key"


# ---------------- 设置读取 ----------------
def get_setting(db, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return (row.value if row and row.value is not None else default)


def put_setting(db, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def relay_conf(db) -> dict:
    return {
        "url": (get_setting(db, RELAY_URL_KEY) or "").strip().rstrip("/"),
        "key": (get_setting(db, RELAY_KEY_KEY) or "").strip(),
    }


# ---------------- 网络 ----------------
def http_get(target: str, conf: dict, timeout: int = 20) -> bytes:
    """取回 Feed 原文。

    配置了中继则走中继（本机唯一可行路径）；未配置时直连——仅对确实有 AAAA
    记录的源有效，用于本地开发或个别境外源。
    """
    relay_url = conf.get("url") or ""
    if relay_url:
        full = f"{relay_url}/fetch?url={urllib.parse.quote(target, safe='')}"
        headers = {"User-Agent": USER_AGENT, "x-relay-key": conf.get("key") or ""}
    else:
        full = target
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        }
    req = urllib.request.Request(full, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return resp.read()


# ---------------- XML 解析（RSS 2.0 / Atom，纯标准库） ----------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# 各格式的根元素闭合标签。用于下面「文档元素之后有杂质」的容错截断。
_ROOT_CLOSERS = (b"</rss>", b"</feed>", b"</rdf:RDF>", b"</opml>")


def _xml_root(raw: bytes):
    """解析出 XML 根元素，对「文档元素之后还有内容」这一种错误做容错。

    为什么要容错：部分源经过 CDN 后，正文末尾会被追加一段统计脚本。
    实测样本 —— 华尔街见闻 `dedicated.wallstreetcn.com/rss.xml`：
    源站把这份 RSS 的 Content-Type 标成 `text/html`，经 Cloudflare 时触发了
    「HTML 改写」，在 `</rss>` 之后插入了 `beacon.min.js` 的 <script>。
    浏览器容忍，XML 解析器不容忍，`ET.fromstring` 直接抛
    `ParseError: junk after document element` —— 于是源明明有 62 条内容却一条都拿不到。

    只在这一种错法上兜底，其余解析错误原样抛出，不掩盖真正的问题。
    截断依据：根标签只有固定的几种，而追加进来的杂质里不会出现它们，
    所以最后一个 `</rss>` 就是根元素的结束位置。
    """
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        if "junk after document element" not in str(e):
            raise
        for closer in _ROOT_CLOSERS:
            i = raw.rfind(closer)
            if i != -1:
                return ET.fromstring(raw[:i + len(closer)])
        raise


def _first(el, name: str):
    """按「去命名空间后的标签名」找第一个直接子节点（RSS 与 Atom 通用）。"""
    if el is None:
        return None
    for child in el:
        if _local(child.tag) == name:
            return child
    return None


def _first_text(el, *names) -> str:
    for n in names:
        child = _first(el, n)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def _all_text(el, name: str) -> str:
    """取所有同名子节点的文本，逗号拼接（如多个 category）。"""
    if el is None:
        return ""
    out = []
    for child in el:
        if _local(child.tag) == name and child.text and child.text.strip():
            out.append(child.text.strip())
    return ",".join(out)


_BLOCK_RE = re.compile(r"(?is)<\s*(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
# 源站用来引导回站的尾注行：🔗 阅读原文 / via AIHOT · <自身条目链接> / source: …
_DROP_LINE_RE = re.compile(r"^(🔗|via\s|source\s*:|原文链接|查看原文|阅读原文)", re.I)
# 整行只是一条 URL（或「来源名 · URL」），正文里没有这种写法
_PURE_URL_RE = re.compile(r"^(?:https?://\S+|[^·]{1,20}\s*·\s*https?://\S+)\s*$")


def strip_html(s: str) -> str:
    """HTML → 纯文本：保留段落，丢掉源站用来引流的尾部链接行。"""
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = _BLOCK_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    lines = []
    for ln in s.split("\n"):
        ln = ln.strip()
        if not ln or _DROP_LINE_RE.match(ln) or _PURE_URL_RE.match(ln):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def parse_date(s: str):
    """RSS pubDate（RFC822）/ Atom（ISO8601）→ UTC naive datetime。"""
    if not s:
        return None
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
    except Exception:
        pass
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


_PAREN_RE = re.compile(r"\(([^()]*)\)\s*$")


def source_name_of(author: str, link: str = "") -> str:
    """从 author 里提取原始来源名：'noreply@aihot.news (MIT News)' → 'MIT News'。"""
    author = (author or "").strip()
    if author:
        m = _PAREN_RE.search(author)
        if m and m.group(1).strip():
            return m.group(1).strip()
        return author
    if link:
        try:
            return urllib.parse.urlparse(link).hostname or ""
        except Exception:
            return ""
    return ""


def parse_feed(raw: bytes):
    """解析 RSS 2.0 / Atom，返回 (meta, entries)。条目字段已归一化。"""
    root = _xml_root(raw)
    kind = _local(root.tag)
    entries = []
    meta = {"title": "", "site_url": ""}

    if kind == "rss":
        ch = _first(root, "channel")
        if ch is None:
            raise ValueError("RSS 缺少 channel")
        meta["title"] = _first_text(ch, "title")
        meta["site_url"] = _first_text(ch, "link")
        nodes = [c for c in ch if _local(c.tag) == "item"]
        for it in nodes:
            link = _first_text(it, "link")
            guid = _first_text(it, "guid") or link
            title = _first_text(it, "title")
            raw_desc = _first_text(it, "description", "encoded")
            if not raw_desc:
                ce = _first(it, "content")
                raw_desc = (ce.text or "") if ce is not None else ""
            published = parse_date(_first_text(it, "pubDate", "date", "published", "updated"))
            author = _first_text(it, "author", "creator")
            entries.append(_norm(title, link, guid, raw_desc, published, author,
                                 _all_text(it, "category")))
    elif kind == "feed":  # Atom
        meta["title"] = _first_text(root, "title")
        for ln in root:
            if _local(ln.tag) == "link":
                rel = ln.get("rel") or "alternate"
                if rel == "alternate" and ln.get("href"):
                    meta["site_url"] = ln.get("href")
                    break
        nodes = [c for c in root if _local(c.tag) == "entry"]
        for en in nodes:
            link = ""
            for ln in en:
                if _local(ln.tag) == "link" and (ln.get("rel") or "alternate") == "alternate":
                    link = ln.get("href") or ""
                    break
            if not link:
                for ln in en:
                    if _local(ln.tag) == "link" and ln.get("href"):
                        link = ln.get("href")
                        break
            guid = _first_text(en, "id") or link
            title = _first_text(en, "title")
            raw_desc = _first_text(en, "content", "summary")
            published = parse_date(_first_text(en, "published", "updated"))
            a = _first(en, "author")
            author = _first_text(a, "name") if a is not None else ""
            cats = []
            for c in en:
                if _local(c.tag) == "category" and c.get("term"):
                    cats.append(c.get("term"))
            entries.append(_norm(title, link, guid, raw_desc, published, author, ",".join(cats)))
    else:
        raise ValueError(f"无法识别的 Feed 格式：{kind}")

    # 抽掉解析不到任何内容的条目（有些源会喂占位项）
    entries = [e for e in entries if e["title"] or e["link"]]
    return meta, entries


def parse_opml(raw: bytes):
    """解析 OPML，抽出全部订阅源。

    只认带 `xmlUrl` 的 `<outline>`（OPML 里分组节点没有这个属性，自然被跳过），
    嵌套分组也能取到（用 iter 而非只扫一层）。属性名按规范是 `xmlUrl`，
    但部分生成器写成全小写，两种都接。

    返回 `[{"title": …, "url": …}]`，顺序即文档顺序，已去重。
    """
    root = _xml_root(raw)
    if _local(root.tag).lower() != "opml":
        raise ValueError(f"不是 OPML 文件（根节点是 {_local(root.tag)}）")
    out, seen = [], set()
    for el in root.iter():
        if _local(el.tag).lower() != "outline":
            continue
        url = (el.get("xmlUrl") or el.get("xmlurl") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({
            "title": (el.get("title") or el.get("text") or "").strip()[:200],
            "url": url[:600],
        })
    return out


def _norm(title, link, guid, raw_desc, published, author, category) -> dict:
    link = (link or "").strip()
    guid = (guid or "").strip()
    if not guid:
        guid = hashlib.md5(
            ((title or "") + (published.isoformat() if published else "")).encode("utf-8")
        ).hexdigest()
    return {
        "title": (title or "").strip(),
        "link": link,
        "guid": guid[:255],
        "summary": strip_html(raw_desc or ""),
        "content": raw_desc or "",
        "published_at": published,
        "author": (author or "").strip()[:200],
        "source_name": source_name_of(author, link)[:200],
        "category": (category or "").strip()[:64],
    }


# ---------------- 入库 ----------------
def refresh_feed(db, feed: NewsFeed, limit: int = 60) -> dict:
    """抓取单个源并入库。返回统计；异常不抛出，写进 last_error 后返回。"""
    conf = relay_conf(db)
    now = datetime.utcnow()
    try:
        raw = http_get(feed.url, conf)
        meta, entries = parse_feed(raw)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:400]
        feed.last_fetched = now
        feed.last_status = "error"
        feed.last_error = msg
        db.commit()
        return {"feed_id": feed.id, "ok": False, "error": msg, "added": 0, "updated": 0}

    if not (feed.title or "").strip() and meta.get("title"):
        feed.title = meta["title"][:200]
    if not (feed.site_url or "").strip() and meta.get("site_url"):
        feed.site_url = meta["site_url"][:600]

    added = updated = 0
    for e in entries[:max(1, limit)]:
        obj = (db.query(NewsItem)
               .filter(NewsItem.feed_id == feed.id, NewsItem.guid == e["guid"])
               .first())
        if obj is None:
            db.add(NewsItem(
                feed_id=feed.id, guid=e["guid"], title=e["title"][:500], link=e["link"][:900],
                author=e["author"], source_name=e["source_name"], category=e["category"],
                summary=e["summary"], content=e["content"], published_at=e["published_at"],
            ))
            added += 1
        else:
            # 只更新源站可变字段；star / ai_* 属于本地状态，绝不覆盖
            obj.title = e["title"][:500] or obj.title
            obj.link = e["link"][:900] or obj.link
            obj.summary = e["summary"] or obj.summary
            obj.content = e["content"] or obj.content
            obj.source_name = e["source_name"] or obj.source_name
            obj.category = e["category"] or obj.category
            if e["published_at"]:
                obj.published_at = e["published_at"]
            updated += 1

    feed.last_fetched = now
    if entries:
        feed.last_status = "ok"
        feed.last_error = ""
    else:
        # 解析成功但一条内容都没有 = **僵尸源**，不能记 ok。
        # 典型样本：新浪财经 RSS（rss.sina.com.cn/*）—— HTTP 200、Content-Type text/xml、
        # 但 channel 里 pubDate 冻结在 2018-09-23 且不含任何 <item>。
        # 只看状态码会以为「抓取成功」，于是白名单、订阅、调度全都配对了却永远没内容。
        feed.last_status = "empty"
        feed.last_error = "源可访问但不含任何条目（疑似已停止更新的僵尸源）"
    db.commit()
    return {"feed_id": feed.id, "ok": bool(entries), "empty": not entries,
            "status": feed.last_status, "error": feed.last_error,
            "added": added, "updated": updated, "parsed": len(entries)}


def refresh_all(db, feed_id=None, limit: int = 60) -> dict:
    q = db.query(NewsFeed)
    if feed_id:
        q = q.filter(NewsFeed.id == feed_id)
    else:
        q = q.filter(NewsFeed.enabled == 1)
    feeds = q.order_by(NewsFeed.sort, NewsFeed.id).all()
    results = [refresh_feed(db, f, limit) for f in feeds]
    return {
        "feeds": len(results),
        "ok": sum(1 for r in results if r.get("ok")),
        "empty": sum(1 for r in results if r.get("empty")),
        "added": sum(r.get("added", 0) for r in results),
        "updated": sum(r.get("updated", 0) for r in results),
        "results": results,
    }


# ---------------- 展示用转换 ----------------
def item_out(it: NewsItem, feed_title: str = ""):
    """转成前端直接可用的形状：日期/时间在服务端算成北京时间，前端不做时区换算。"""
    dt = it.published_at
    cn = dt + CN_TZ if dt else None
    return {
        "id": it.id,
        "feed_id": it.feed_id,
        "feed_title": feed_title or "",
        "title": it.title or "",
        "link": it.link or "",
        "source_name": it.source_name or "",
        "category": it.category or "",
        "summary": it.summary or "",
        "date": cn.strftime("%Y-%m-%d") if cn else "",
        "time": cn.strftime("%H:%M") if cn else "",
        "published_at": dt.isoformat() if dt else None,
        "star": 1 if it.star else 0,
        "ai_score": it.ai_score,
        "ai_reason": it.ai_reason or "",
        "ai_tags": it.ai_tags or "",
    }
