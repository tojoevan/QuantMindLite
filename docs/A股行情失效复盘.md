# A股行情失效复盘（2026-10-15）

> 触发：P9「新建标的支持基金/债券」实施时发现线上 A 股行情拉取全线失败。
> 本文只回答一个问题：**为什么挂了、根因是什么**。修复方案见 `界面重构方案.md` §3.6。

## 0. 一句话结论

取数链名义上有 4 个源，实际**只有 1 个源可能连通**（东方财富）。而这个唯一的活口
**对这台阿里云 VPS 的 IPv6 出口做了定向应用层拦截**——TCP/TLS 都通，HTTP 请求被无响应断开。
活口一断，整条链归零。

## 1. 事实基础：这台机器的网络底座

```
$ ip -4 route
172.17.0.0/16 dev docker0 ...
172.18.0.0/16 dev br-b86373b79594 ...
172.20.0.0/16 dev br-f6eac0876257 ...
# ↑ 只有 docker 网桥的链路路由，没有 IPv4 默认路由
$ ip -6 route
<自有网段> dev eth0 ...
```

**没有任何 IPv4 默认路由**（不是「被墙」，是从物理上发不出 IPv4 包）。
所以：**目标域名必须有真 AAAA 才可能连通**。

⚠️ 判断陷阱：`getent ahostsv6` 返回 `::ffff:101.226.30.221` 这种 **IPv4-mapped 地址等于没有 IPv6**，
必须看是不是 `2400::/4` 段的真地址。

## 2. 四个源逐个实测（把**修复前**的代码原样搬到服务器上跑）

```
FAIL 1) 腾讯 web.ifzq.gtimg.cn/appstock/app/fqkline/get
       URLError: <urlopen error [Errno 101] Network is unreachable>（0.3s）
FAIL 2) 新浪 money.finance.sina.com.cn/.../CN_MarketData.getKLineData
       URLError: <urlopen error [Errno 101] Network is unreachable>（0.3s）
FAIL 3) akshare stock_zh_a_hist
       ConnectionError: RemoteDisconnected(...)（1.5s）
FAIL 4) 东财 push2his.eastmoney.com/api/qt/stock/kline/get
       RemoteDisconnected: Remote end closed connection without response（1.2s）
```

逐个定性：

| 顺序 | 源 | 主机 | DNS | 真实死因 | 性质 |
|---|---|---|---|---|---|
| 1 | 腾讯 gtimg | `web.ifzq.gtimg.cn` | 只有 A（`43.154.254.185/.89`） | `Errno 101 Network is unreachable` | **结构性死**——从本机永远不可能通 |
| 2 | 新浪 | `money.finance.sina.com.cn` | 只有 A（`106.63.15.52` 等） | 同上 | **结构性死** |
| 3 | akshare | 底层就是 `7.push2his.eastmoney.com` | 同 #4 | 与 #4 同命 | **#4 的包装，不是独立源** |
| 4 | 东财直连 | `push2his.eastmoney.com` | **有真 AAAA** | TCP/TLS 通，HTTP 被无响应断开 | **应用层定向拦截** |

> 关键点 1：**#1、#2 是 IPv4-only 域名**，在这台机器上从来没有成功过。所谓「多源冗余」是纸面的。
> 关键点 2：**#3 不是独立源**。akshare 1.18.83 的 `stock_zh_a_hist` 请求
> `http://7.push2his.eastmoney.com/api/qt/stock/kline/get`，与 #4 同门同端点。
> 所以「四源」真正独立的只有 3 个，而其中能连通的只有 1 个。

## 3. 东财拦截的取证（排除法，逐步收紧）

| # | 实验 | 结果 | 排除掉的可能 |
|---|---|---|---|
| 1 | TCP 443 连 `push2his` | ✅ 连上（0.34s）；TLS 1.3 握手成功 | 网络层/DNS/端口不通 |
| 2 | 换 4 种请求头（旧代码原样 / 加 Referer / 完全不带 / 浏览器全套） | ❌ 全部 `RemoteDisconnected`，均 0 字节 | header、Referer 校验 |
| 3 | HTTP/1.1 与 HTTP/2 分别试 | ❌ 都是 `code=000` | 协议版本要求 |
| 4 | 同一台机访问同厂 `api.fund.eastmoney.com` | ✅ **HTTP 200 + 真实净值** | 「东财封了这个 IP」 |
| 5 | 单节点连试 6 次 | ❌ 6/6 全断 | 间歇性/限流抖动 |
| 6 | 解析全部 AAAA 逐节点试 | ❌ 该节点断 | 「解析到坏节点」 |
| 7 | **强制指向另一台 VPS 能正常访问的那个节点** | ❌ 仍然 000 | 「GeoDNS 派错节点」 |
| 8 | 从另一台 VPS（RackNerd）走 **IPv6** 访问同一接口 | ✅ **HTTP 200 + 真实 K 线** | 「东财封了整个 IPv6」 |
| 9 | 本机（IPv4 出口）访问同一接口 | ✅ HTTP 200 + 真实 K 线 | 「接口本身下线」 |

**结论**：不是 DNS、不是 header、不是协议、不是节点、不是间歇抖动、不是全站封 IP，
也不是「东财不服务 IPv6」。**是东财按「来源地址 + 接口」的组合做了定向拦截**——
同一个端点在别的网络里走 IPv6 完全正常，唯独拒绝这台阿里云 VPS 的 IPv6 出口。
实验 7 是这个结论的决定性证据：把边缘节点换成对端能用的那个，依旧被拒。

## 4. 「为什么之前能用、后来才挂」

从 `projects.last_fetch_date` 看，最后一次成功取数是 **2026-10-14**（北京时间），
当时拿到的最后一根日线是 10-13 —— 与「当日 K 线要收盘后才生成」自洽。

日志（journald，服务器时区为 UTC，+8 即北京时间）：

```
Oct 14 15:46:46 (北京 10-14 23:46)  POST /api/projects/2/fetch  → 200
Oct 14 17:32:17 (北京 10-15 01:32)  POST /api/projects/7/fetch  → 502   ← 第一次观测到的失败
Oct 14 17:33:12 (北京 10-15 01:33)  POST /api/projects/2/fetch  → 502
Oct 14 17:35:20 (北京 10-15 01:35)  【新版 datasource.py 落地】
Oct 14 17:43:59 (北京 10-15 01:43)  GET  /api/guest/projects/5/refresh → 200 ← 新版冒烟测试
```

两个必须说清的细节：

- Oct 14 23:46 那条 200 并**不代表源当时还活着**。`fetch_prices` 在
  `last_fetch_date == 今天` 时会**直接返回 skipped（也是 200）**，根本不碰网络。
  #2 的 `last_fetch_date` 本来就是 10-14，所以这条 200 极可能是「跳过」。
- 排查时曾发现 #5 万科A 的 `last_fetch_date` 已是 10-15，一度以为「今天还成功过」。
  实为**我自己的冒烟测试**写入的：`/api/guest/projects/5/refresh` 是**第二条独立的取数通道**
  （游客访问公开标的页即触发，早于 `last_fetch_date` 判断之外），
  那 5 条记录全部发生在新版部署之后（01:43 起），**不是**拦截期内的成功取数。

**结论：拦截究竟从哪一刻开始，无法精确到分钟**——因为这段窗口内没有任何一次真实网络调用。
取数只在「打开标的页」时按需触发（`force=False` 且当天拉过即跳过），没有调度器。
换句话说，故障是**静默发生**的，两次调用之间没有任何一方去碰过这个接口。

## 5. 「为什么没人发现」——两个放大器

1. **没有调度**：不打开页面就不取数，源挂了也不会有人知道。
   圆桌/资讯侧同样没有定时任务（服务器无 systemd timer）。
2. **前端只看到一个笼统失败**：后端把 4 个源的错误拼成一句
   `所有行情数据源均失败 -> 腾讯:… | 新浪:… | akshare:… | 东方财富:…` 返回 502，
   但页面上只是「拉取失败」，不会有人深究是哪一层断的。

## 6. 已实施的修复（P9，2026-10-15 上线）

| 修复 | 内容 |
|---|---|
| 换主源 | `web.ifzq.gtimg.cn/appstock/app/fqkline/get` → `proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get`（**参数与字段完全一致**，只换域名）；该域名有真 AAAA |
| 去掉死源 | 删除新浪源（IPv4-only，永远不可达）；akshare 不再作行情源 |
| 保留可用支路 | 大盘指数继续走 `qt.gtimg.cn`（有真 AAAA，实测正常） |

线上实测（2026-10-15）：`600221.SH` / `600406.SH` / `688728.SH` / `000002.SZ` 各 **270 条**至 2026-10-14。

## 7. 遗留风险（未解决，需记着）

- **仍然是单源**。现在只有腾讯 `proxy.finance.qq.com` 一条命，它出事就是同样的剧本。
  在 IPv6-only 的机器上，「多源冗余」这个设计基本无法成立——要么加中继（参照 `rss-relay`），
  要么接受单源并加**取数失败告警**。
- **没有调度与告警**。这是本次「挂了没人知道」的直接原因，比源本身更值得处理。
- **取数有两个入口**，排查时容易只看一个：
  1. `POST /api/projects/{pid}/fetch`（登录用户进入标的页自动触发 / 手动刷新）；
  2. `GET /api/guest/projects/{pid}/refresh`（**游客**访问公开标的页触发，只对游客标的生效）。
  两者各自维护 `last_fetch_date`，都会静默失败并只回一句笼统错误。
