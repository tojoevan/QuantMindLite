"""LLM 客户端（OpenAI 兼容 /chat/completions 协议，纯标准库）。

适配 DeepSeek / 智谱 / Moonshot / OpenAI 等：只需 base_url + api_key + model。
本机 VPS 出站仅 IPv6 可达（IPv4 不可达），强制 getaddrinfo 走 AF_INET6。
"""
import json
import socket
import ssl
import time
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
                  temperature: float = 0.4, retries: int = 1) -> str:
    """调用 OpenAI 兼容聊天接口，返回文本内容。失败抛 RuntimeError。

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
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + (api_key or ""),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
    }

    def _post(verify: bool) -> str:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(verify)) as resp:
            j = json.loads(resp.read().decode("utf-8", "ignore"))
        choices = j.get("choices") or []
        if not choices:
            raise RuntimeError("LLM 返回无 choices")
        return (choices[0].get("message") or {}).get("content") or ""

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
