"""LLM 客户端（OpenAI 兼容 /chat/completions 协议，纯标准库）。

适配 DeepSeek / 智谱 / Moonshot / OpenAI 等：只需 base_url + api_key + model。
本机 VPS 出站仅 IPv6 可达（IPv4 不可达），强制 getaddrinfo 走 AF_INET6。

中继通道（via_relay，2026-10-17）：无 AAAA 的服务商（DeepSeek / Moonshot）在本机
没有任何直连路径，改经 rss-relay（relay.kapibala.icu）的 POST 通道转发：
请求发到 `{via_relay}/post?url={原URL}`，原 Authorization 由 Worker 透传给源站。
调用方在目标主机不可达且中继已配置时才启用（见 app._llm_route），本模块不判断可达性。
"""
import json
import socket
import ssl
import time
import urllib.parse
import urllib.request
import urllib.error

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
                  disable_thinking: bool = False) -> str:
    """调用 OpenAI 兼容聊天接口，返回文本内容。失败抛 RuntimeError。

    via_relay：中继基址（如 https://relay.kapibala.icu）。非空时请求改走
    `{via_relay}/post?url=<base_url>/chat/completions`，relay_key 作为
    x-relay-key 头；源站鉴权仍靠原 api_key（Worker 透传 Authorization）。
    disable_thinking：推理模型的思考（reasoning_content）与正文**共享**
    max_tokens 预算——长提示词下思考可能耗尽预算、正文为空（finish=length）。
    传 True 时附带 `thinking: {"type": "disabled"}` 关闭思考
    （2026-10-17 对 deepseek-flash 实测有效；其他厂商未验证，勿盲开）。
    4xx（Key 无效 / 参数错误）不重试直接抛错；429 与 5xx、网络异常按 retries 重试。
    证书校验失败时自动降级为不验证重试一次（本机 CA 链可能不全）。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if disable_thinking:
        payload["thinking"] = {"type": "disabled"}
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
        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
        if not content.strip():
            # 推理模型（如 deepseek-flash）的思考与正文共享 max_tokens：
            # finish=length 且正文空 = 预算被 reasoning_content 烧光，不是没生成。
            fr = choices[0].get("finish_reason")
            hint = ("推理模型的思考耗尽了 max_tokens（正文未开始即被截断），"
                    "可关思考或加大 max_tokens" if fr == "length"
                    else f"finish_reason={fr}，请检查模型名与参数")
            raise RuntimeError(f"模型返回为空（finish_reason={fr}）：{hint}")
        return content

    last_err = None
    for attempt in range(retries + 1):
        try:
            return _post(verify=True)
        except ssl.SSLError:
            # 证书链问题：降级不验证再试一次（仍走 HTTPS 加密通道）
            try:
                return _post(verify=False)
            except Exception as e:
                last_err = e
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(f"LLM HTTP {e.code}: {body}")
            last_err = RuntimeError(f"LLM HTTP {e.code}: {body}")
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last_err if last_err else RuntimeError("LLM 调用失败")
