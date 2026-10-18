# DeepSeek 不可达问题专项报告

> 2026-10-17 · quant.study3d.com（QuantMindLite）· 状态：**已解决，进入跟踪期**

## 一、问题定性

生产环境（IPv6 VPS，地址已脱敏）圆桌「LLM Key 自动」通道无法使用 DeepSeek：
用户已配置 `llm.deepseek.*`（Key + `deepseek-flash`），但点击生成无结果。

**这是拓扑性不可达，不是配置错误**：DeepSeek API 走 CloudFront 分发、不提供 IPv6；
本机无 IPv4 默认路由 → 不存在任何直连路径。重试、换 header、改超时均无效。

## 二、根因与取证（2026-10-17 现场复核）

| 检查项 | 结果 |
|---|---|
| `getent ahostsv6 api.deepseek.com` | 仅返回 IPv4-mapped `::ffff:3.173.21.63`（= 无 AAAA，CloudFront） |
| `ip -4 route show default` | 空（无 IPv4 默认路由） |
| TCP 443 直连 | `Network is unreachable`（Errno 101） |
| `curl -6 https://api.deepseek.com/` | `Could not resolve host`（DNS 阶段早失败，符合预期） |
| 中继 `relay.kapibala.icu` | HTTP 200 可达（当时仅 GET，白名单无 deepseek） |
| 同类不可达 | `api.moonshot.cn`（同样无 AAAA） |

`llm.py` 导入时的全局 `socket.getaddrinfo → AF_INET6` patch 让无 AAAA 域名在 DNS 阶段
明确早失败，行为正确，**保留不删**（它对可达服务商本就是必需的解析路径）。

## 三、方案对比与选型

| 方案 | 改动量 | 风险 | 结论 |
|---|---|---|---|
| 换可达服务商（智谱/通义/豆包/硅基流动/OpenRouter/Gemini） | 零 | 零 | 兜底可用，但用不了 DeepSeek |
| **扩 rss-relay 支持 POST 转发（本方案）** | 小 | 低（纯应用层） | ✅ 选中：复用现有基础设施，顺带救活 Moonshot |
| WARP 代理模式（本地 SOCKS5，不动默认路由） | 中 | 低-中 | 备选，等第二轮需求再评估 |
| 经自有双栈 VPS（地址脱敏）建隧道（tinyproxy/WireGuard） | 中 | 中 | 备选 |
| 公共 NAT64/DNS64 | 极小 | **高**（全局解析改道免费公共网关） | 否决 |
| 系统级 WARP 全接管 / 默认路由改道 | 大 | 高（动生产机网络，违反稳定性纪律） | 否决 |

## 四、实施记录（2026-10-17）

### 1. rss-relay 扩 POST 通道（`relay.kapibala.icu`）

- `src/index.js`：新增 `POST /post?url=` 通道——透传 body 与
  `Authorization`/`Content-Type` 两个头，**禁缓存**（GET `/fetch` 通道的 5 分钟
  边缘缓存保持不变）；密钥头 + 域名白名单校验两通道共用；非 POST 请求 405。
- `wrangler.toml`：`ALLOW_HOSTS` 追加 `api.deepseek.com,api.moonshot.cn`
  （后缀匹配只放行该主机及子域，不会误放整域）。
- 部署：`wrangler deploy`，版本 `002ab94b-cc86-49ec-ba4a-4b7db0befb34`。

### 2. quant-web 接入中继路由

- `llm.py`：`chat_complete()` 新增 `via_relay` / `relay_key` 参数。非空时请求改发
  `{via_relay}/post?url={原URL}`（URL encode），`x-relay-key` 头带中继密钥；
  源站鉴权仍靠原 `api_key`（Worker 透传 Authorization）。本模块不判断可达性。
- `app.py`：新增 `_llm_route(db, base_url)`——目标主机经 `_host_reachable`
  （与 chat_complete 同一解析路径，5 分钟缓存）判定不可达、且 `newsfetch.relay_conf()`
  已配置中继时，返回中继参数；否则直连。**只在主线程、有 db 处调用**
  （内部读 settings 表）。
- `rt_generate`：席位 conf 在主线程统一 `c.update(_llm_route(...))` 后传入工作线程，
  `chat_complete(..., via_relay=..., relay_key=...)`。

### 3. 验证结果（全部通过）

| 测试 | 结果 |
|---|---|
| 中继安全闸：假 DeepSeek Key / 非白名单域名 / 错中继密钥 / GET 走 /post | 401(源站) / 403 / 403 / 405 ✅ |
| 本机全链路：正确中继密钥 + 假 DeepSeek Key | DeepSeek 官方 401 原样透传回来（链路透明）✅ |
| `_llm_route`（本机有 IPv4） | 返回 `{}`，不绕中继 ✅ |
| **生产端到端**：VPS 上真实 Key 经 `_llm_route` → 中继 → `deepseek-flash` | 返回正常中文内容 ✅ |
| 部署健康检查 | `deploy.sh deploy` 成功，`{"status":"healthy"}` ✅ |

## 五、影响面

- **变更**：`rss-relay/src/index.js`、`rss-relay/wrangler.toml`、
  `quant-web/llm.py`、`quant-web/app.py`。前端零改动，无静态资源版本号变化。
- **中继语义**：仍是不解析/不存储的透明管道；POST 通道只透传两个头，白名单外一律 403。
- **可达性徽标**：设置页的「不可达」徽标仍反映**直连**可达性（DeepSeek 仍显示不可达）——
  这是诚实的；中继路由是传输层自动兜底，无需用户感知。

## 六、遗留与跟踪项（后续优化）

1. **圆桌自动生成实战验证**：席位需 `mode=auto` + 已配 Key，建议用户实际点一次
   「生成今日讨论」确认全链路（本轮只验证了 `chat_complete` 层）。
2. **Moonshot 接入**：白名单已放行 `api.moonshot.cn`，有 Key 即可用，未实测。
3. **设置页徽标语义**（可选优化）：对走中继的服务商，徽标可显示「中继可达」，
   避免用户误以为仍不可用。
4. **`reachable` 探测与 `_llm_route` 判定的一致性**：两者共用 `_host_reachable`
   缓存，当前一致；若未来中继通道增加健康检查，考虑让徽标直接反映「最终可用性」。
5. **中继为单点**：relay.kapibala.icu 挂在 Cloudflare 免费额度上；GET/POST 合并计量，
   当前量级（资讯 + 低频 LLM）远无忧，但若圆桌高频使用需关注请求数。
6. **设置页「不可达」徽标语义**（与第 3 条同义，2026-10-17 复核）：徽标反映**直连**可达性，
   走中继的服务商（DeepSeek / Moonshot）会显示「不可达」但经中继实际可用——这是诚实的，
   优化方向是显示「中继可达」。⚠️ 复核更正：`rt_prompt` 手动通道**不调用 LLM**
   （只拼提示词供用户复制到外部 AI 网页咨询，见函数 docstring），不存在「未接中继」问题，
   早前跟踪记录表述有误，以此条为准。

## 七、架构备忘

```
quant-web (IPv6-only VPS)
   │  llm.chat_complete(via_relay=...)
   ▼
relay.kapibala.icu (Cloudflare Worker, 双栈)
   │  POST 透传 body + Authorization（白名单: api.deepseek.com / api.moonshot.cn）
   ▼
api.deepseek.com (CloudFront, 仅 A 记录)
```

密钥链：源站 Key 存 `settings.llm.deepseek.api_key`（不回显）；中继密钥复用
`news.relay_key`（Worker secret `RELAY_KEY`），两把钥匙各管一段。
