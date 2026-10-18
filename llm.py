"""LLM 客户端（OpenAI 兼容 /chat/completions 协议，纯标准库）。

适配 DeepSeek / 智谱 / Moonshot / OpenAI 等：只需 base_url + api_key + model。
本机 VPS 出站仅 IPv6 可达（IPv4 不可达），强制 getaddrinfo 走 AF_INET6。

中继通道（via_relay，2026-10-17）：无 AAAA 的服务商（DeepSeek / Moonshot）在本机
没有任何直连路径，改经 rss-relay（relay.kapibala.icu）的 POST 通道转发：
请求发到 `{via_relay}/post?url={原URL}`，原 Authorization 由 Worker 透传给源站。
调用方在目标主机不可达且中继已配置时才启用（见 app._llm_route），本模块不判断可达性。

推理模型的思考预算（2026-10-17）：deepseek-flash 等推理模型的
reasoning_content 与正文**共享 max_tokens**。长提示词下思考会发散、把预算烧光，
表现为 finish=length 且正文为空（圆桌提示词实测：900 烧 1585 字思考、
2000 烧 5249 字，reasoning_effort=low 也压不住）。三种应对：
  - disable_thinking=True：请求带 thinking:{type:"disabled"}（对 DeepSeek 实测有效）；
  - reasoning_effort：部分厂商支持，可压低思考量（未全覆盖验证）；
  - 自动兜底：思考未关却超预算时，自动降级重试一次（关思考），防呆不依赖配置。
    兜底成功会打 warning，调用方从 stats["thinking_fallback"] 得知。

TypeSafe 判断协议（2026-10-18）：systemone_judge() 走 /v1/systemone
（小海马 Jev），只输出结构化判断概率（~20 tokens/次），不生成自然语言。
"""
import json
import logging
import socket
import ssl
import time
import urllib.parse
import urllib.request
import urllib.error

log = logging.getLogger("quant-web.llm")

# 强制 IPv6 解析（与 scripts/fetch_fund_nav.py 同一原因：本机 IPv4 出口不可达）
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_v6(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET6, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_v6


def _ssl_ctx(verify: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def chat_complete(base_url: str, api_key: str, model: str, messages: list,
                  timeout: int = 40, max_tokens: int = 900,
                  temperature: float = 0.4, retries: int = 1,
                  via_relay: str = "", relay_key: str = "",
                  disable_thinking: bool = False, reasoning_effort: str = "",
                  stats: dict = None) -> str:
    """调用 OpenAI 兼容聊天接口，返回文本内容。失败抛 RuntimeError。

    via_relay：中继基址（如 https://relay.kapibala.icu）。非空时请求改走
    `{via_relay}/post?url=<base_url>/chat/completions`，relay_key 作为
    x-relay-key 头；源站鉴权仍靠原 api_key（Worker 透传 Authorization）。
    disable_thinking：关闭推理模型的思考（请求带 thinking:{type:"disabled"}）。
    reasoning_effort：可选 low/medium/high，压思考档位（厂商支持情况不一）。
    stats：可选出参 dict，回填本次调用的诊断信息（供 app 层落 llm_call_log）：
      ok, finish_reason, prompt_tokens, completion_tokens, reasoning_tokens,
      duration_ms, via_relay(0/1), thinking("off"/"on"), thinking_fallback(0/1),
      error（失败时的错误串）。异常路径上也会尽力回填（ok=0 + error）。
    4xx（Key 无效 / 参数错误）不重试直接抛错；429 与 5xx、网络异常按 retries 重试。
    证书校验失败时自动降级为不验证重试一次（本机 CA 链可能不全）。
    """
    t0 = time.time()
    if stats is None:
        stats = {}
    stats.setdefault("model", model)
    stats.setdefault("via_relay", 1 if via_relay else 0)
    stats["thinking"] = "off" if disable_thinking else "on"

    def _finish(ok: bool, err: str = ""):
        stats["ok"] = 1 if ok else 0
        if err:
            stats["error"] = err[:500]
        stats["duration_ms"] = int((time.time() - t0) * 1000)
        log.info("llm call model=%s ok=%s finish=%s completion=%s dur=%sms "
                 "relay=%s thinking=%s fallback=%s err=%s",
                 model, ok, stats.get("finish_reason"),
                 stats.get("completion_tokens"), stats["duration_ms"],
                 stats["via_relay"], stats["thinking"],
                 stats.get("thinking_fallback", 0), err[:120])

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if disable_thinking:
        payload["thinking"] = {"type": "disabled"}
    elif reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + (api_key or ""),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
    }
    if via_relay:
        url = via_relay.rstrip("/") + "/post?url=" + urllib.parse.quote(url, safe="")
        headers["x-relay-key"] = relay_key or ""

    def _post(verify: bool) -> str:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(verify)) as resp:
            j = json.loads(resp.read().decode("utf-8", "ignore"))
        choices = j.get("choices") or []
        if not choices:
            raise RuntimeError("LLM 返回无 choices")
        ch = choices[0]
        msg = ch.get("message") or {}
        content = msg.get("content") or ""
        usage = j.get("usage") or {}
        stats["finish_reason"] = ch.get("finish_reason") or ""
        stats["prompt_tokens"] = usage.get("prompt_tokens") or 0
        stats["completion_tokens"] = usage.get("completion_tokens") or 0
        stats["reasoning_tokens"] = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        if not content.strip():
            # 推理模型的思考与正文共享 max_tokens：finish=length 且正文空
            # = 预算被 reasoning_content 烧光，不是没生成（2026-10-17 圆桌实测）。
            fr = ch.get("finish_reason")
            hint = ("推理模型的思考耗尽了 max_tokens（正文未开始即被截断），"
                    "可关思考或加大 max_tokens" if fr == "length"
                    else f"finish_reason={fr}，请检查模型名与参数")
            raise _EmptyContent(f"模型返回为空（finish_reason={fr}）：{hint}")
        return content

    class _EmptyContent(RuntimeError):
        """正文为空（多为推理模型超预算），供自动兜底识别。"""

    last_err = None
    for attempt in range(retries + 1):
        try:
            content = _post(verify=True)
            _finish(True)
            return content
        except ssl.SSLError:
            # 证书链问题：降级不验证再试一次（仍走 HTTPS 加密通道）
            try:
                content = _post(verify=False)
                _finish(True)
                return content
            except Exception as e:
                last_err = e
        except _EmptyContent as e:
            # 思考没关却超预算 → 自动降级：关思考重试一次（配置防呆）。
            # 已关过还空 → 是真的没内容，按普通错误抛出。
            if not disable_thinking:
                log.warning("llm 超预算兜底：%s → 关思考重试（model=%s）", e, model)
                stats["thinking_fallback"] = 1
                disable_thinking = True
                payload["thinking"] = {"type": "disabled"}
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                stats["thinking"] = "off"
                try:
                    content = _post(verify=True)
                    stats["thinking_fallback"] = 1
                    _finish(True)
                    return content
                except Exception as e2:
                    _finish(False, str(e2))
                    raise RuntimeError(str(e2))
            _finish(False, str(e))
            raise
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            if 400 <= e.code < 500 and e.code != 429:
                _finish(False, f"LLM HTTP {e.code}: {body}")
                raise RuntimeError(f"LLM HTTP {e.code}: {body}")
            last_err = RuntimeError(f"LLM HTTP {e.code}: {body}")
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    _finish(False, str(last_err))
    raise last_err if last_err else RuntimeError("LLM 调用失败")


def systemone_judge(base_url: str, api_key: str, model: str, state: str,
                    questions: dict, timeout: int = 60, retries: int = 1,
                    via_relay: str = "", relay_key: str = "",
                    stats: dict = None) -> dict:
    """TypeSafe 结构化判断（/v1/systemone 协议，小海马 Jev，非 chat completions）。

    questions: {问题 key: instructions 字符串}，如
        {"importance": "这条资讯对A股投资者是否重要？"}。
    返回 {问题 key: 0~1 概率 float}。失败抛 RuntimeError。

    与 chat_complete 的分工：本协议只做判断（输出 20 tokens 级的概率），
    不生成自然语言——reason/tags 等生成物由调用方本地规则补齐（见 newsai）。
    stats：可选出参 dict，回填 ok/prompt_tokens/completion_tokens/duration_ms/
        via_relay/error（键位与 chat_complete 对齐，便于统一落库）。
    本机实测：ai.xiaohaima-edu.com 有 AAAA 可直连；via_relay 分支仅为协议一致性保留。
    """
    t0 = time.time()
    if stats is None:
        stats = {}
    stats.setdefault("model", model)
    stats.setdefault("via_relay", 1 if via_relay else 0)
    stats["thinking"] = "n/a"

    def _finish(ok: bool, err: str = ""):
        stats["ok"] = 1 if ok else 0
        if err:
            stats["error"] = err[:500]
        stats["duration_ms"] = int((time.time() - t0) * 1000)
        log.info("systemone model=%s ok=%s dur=%sms relay=%s err=%s",
                 model, ok, stats["duration_ms"], stats["via_relay"], err[:120])

    url = base_url.rstrip("/") + "/v1/systemone"
    payload = {
        "state": state,
        "model": model,
        "questions": {k: {"type": "noul", "instructions": str(v)}
                      for k, v in (questions or {}).items()},
    }
    if not payload["questions"]:
        raise RuntimeError("systemone 调用缺少 questions")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + (api_key or ""),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
    }
    if via_relay:
        url = via_relay.rstrip("/") + "/post?url=" + urllib.parse.quote(url, safe="")
        headers["x-relay-key"] = relay_key or ""

    def _post(verify: bool) -> dict:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(verify)) as resp:
            j = json.loads(resp.read().decode("utf-8", "ignore"))
        answers = j.get("answers") or {}
        if not answers:
            err = j.get("error") or {}
            raise RuntimeError(f"systemone 返回无 answers：{err.get('message') or str(j)[:200]}")
        usage = j.get("usage") or {}
        stats["prompt_tokens"] = usage.get("input_tokens") or 0
        stats["completion_tokens"] = usage.get("output_tokens") or 0
        out = {}
        for k, v in answers.items():
            if isinstance(v, dict) and v.get("noul") is not None:
                try:
                    out[k] = float(v["noul"])
                except (TypeError, ValueError):
                    raise RuntimeError(f"systemone 答案 {k} 的概率无法解析：{v}")
            else:
                raise RuntimeError(f"systemone 答案 {k} 形态异常：{v}")
        return out

    last_err = None
    for attempt in range(retries + 1):
        try:
            result = _post(verify=True)
            _finish(True)
            return result
        except ssl.SSLError:
            try:
                result = _post(verify=False)
                _finish(True)
                return result
            except Exception as e:
                last_err = e
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            if 400 <= e.code < 500 and e.code != 429:
                _finish(False, f"LLM HTTP {e.code}: {body}")
                raise RuntimeError(f"LLM HTTP {e.code}: {body}")
            last_err = RuntimeError(f"LLM HTTP {e.code}: {body}")
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    _finish(False, str(last_err))
    raise last_err if last_err else RuntimeError("systemone 调用失败")
