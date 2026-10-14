# QuantMind 接入评估（2026-10-15 实测）

> **一句话结论：暂不接入 —— 历史数据、实时数据都不接，但两者的原因完全不同。**
>
> - **历史数据**：接口活得好好的、还是同机直连，但它的前置数据（QuantDB 全量约 60 GB）
>   在这台 VPS 上**既下不来、也放不下**；而 quant-web 实际只需要几个标的的日线。
> - **实时数据**（2026-10-15 追问后补测，见 §四）：它**自己不产生实时数据** ——
>   默认源是需要 QMT Agent 从外部推进来的 `remote_redis`，而本机没有任何源在推；
>   且**容器被 bridge 网络困在纯 IPv4 里、零出网能力**，连备选的腾讯适配器都跑不通。
>   它唯一能自给的源（`qt.gtimg.cn`）quant-web 现在**已经在直连**。
>
> 本文固化判断依据，避免以后反复重新考证。

---

## 一、容易混淆的两个「QuantMind」

| 名字 | 指什么 | 与 quant-web 的关系 |
|---|---|---|
| `quantmind/`（本目录下的开源框架） | gitee `qusong0627/quantmind` 克隆，基于微软 Qlib 的 A 股 ML 预测框架，Docker 部署 | **无代码依赖**。仅在同机以容器形式运行 |
| `QuantMindLite`（GitHub `tojoevan/QuantMindLite`） | 就是本项目 `quant-web/` 的仓库名 | 借鉴其「预测→执行→反馈」闭环**思想**自建 |

新手最容易踩的坑：以为仓库叫 QuantMindLite 就等于用了 QuantMind。**不是。**

---

## 二、现状核查（2026-10-15 01:20–02:25 只读实测）

### 2.1 服务：活着，而且就在同一台 VPS 上

```
quantmind         Up 2 weeks (healthy)   0.0.0.0:8000-8003
quantmind-celery  Up 2 weeks (healthy)
quantmind-redis   Up 2 weeks (healthy)   :6379
quantmind-db      Up 2 weeks (healthy)   :5432   ← Postgres
```

- 部署路径 `/opt/quantmind/`，四个服务根路径均正常响应：
  `:8000` `QuantMind API Service V2 is running` / `:8001` Engine / `:8002` Trade / `:8003` Stream
- API 共 **125 个 REST 路由**（`/openapi.json`）

### 2.2 数据：空的

Postgres 共 102 张表，业务表**全部 0 行**：

| 表 | 行数 | 表 | 行数 |
|---|---|---|---|
| `klines` | 0 | `stocks` | 0 |
| `stock_daily_latest` | 0 | `qm_market_calendar_day` | 0 |
| `market_data_daily` | 0 | `qm_user_models` | 0 |
| `index_daily` | 0 | `engine_feature_snapshots` | 0 |
| `quotes` | 0 | `users` | **1**（admin） |

`/opt/quantmind/db/`（容器内 `/app/db`）里只有一份 `README.md`（8 KB）——
**离线数据包从未下载**。

实测接口：

| 请求 | 结果 |
|---|---|
| `GET /api/v1/public/sync/stock-daily?trade_date=2026-10-14` | `{"code":200,"data":[]}` —— 通，但**空** |
| `GET /api/v1/public/sync/calendar?start_date=2026-10-01` | **500** Internal server error |
| `GET /api/v1/research/kline/000002.SZ` | **401**（需鉴权） |
| `GET /api/v1/models/inference/latest` | **401**（需鉴权） |

### 2.3 quant-web 侧：对它的调用为 0

- `predictor.py` 全文只有 `_normalize_config` / `run_strategy` / `_strategy_min_window` /
  `rolling_predict` / `forecast_future` 五个纯函数，只 `import json` + `datetime`，
  **没有任何 HTTP 客户端、没有 QuantMind / qlib 引用，也不存在 `QuantmindPredictor` 桩**。
- 全项目对外网络调用只有两处：`datasource.py`（腾讯/东财行情）、`newsfetch.py`（经 Cloudflare 中继抓 RSS）。
- 关于页（`static/js/04-about-users.js`）与 README §三 均已写明「运行不依赖 QuantMind / Qlib 代码」。

> ⚠️ 部署手册 §八 曾写「`QuantmindPredictor` 已留对接桩」，**与代码事实不符**——从来没有过这个桩，
> 该表述已在 2026-10-15 一并更正。

---

## 三、为什么暂不接入：三条硬约束

### 约束 1：数据下不来（DNS 可达性）

QuantDB 数据集的官方分发入口：

```
oss.quantmindai.cn       AAAA: ❌
modelscope.cn            AAAA: ❌   ← 魔搭社区，官方推荐的「一键下载」通道
www.modelscope.cn        AAAA: ❌
quantmindai.cn           AAAA: ❌
```

这台 VPS 只有 IPv6 出站、无 IPv4 默认路由（见 `A股行情失效复盘.md`），
**以上域名全部连不上**，实测 `curl https://oss.quantmindai.cn/data-download.html` → `code=000`（0.006s 即失败）。

要拿到数据只能**曲线救国**：本机（有 IPv4）下载 → 再上传到服务器。60 GB 走这条路是折磨。

### 约束 2：磁盘放不下

| 项 | 数值 |
|---|---|
| QuantDB 全量 | **约 60 GB** / 约 74,000 个 Parquet 文件 |
| 服务器根分区 | 58 GB 总，**可用 38 GB**（已用 36%） |

**60 GB > 38 GB**，物理上放不下全量。即使只取子集（如只下 `1_kline_data/daily_forward/`），
也要先解决"怎么分批弄上来"的问题。

### 约束 3：量级不匹配

QuantDB 是**研究级全市场数据集**：2016 年至今、约 5500 只标的、
三套日线 + 指数 + 财务 + 融资融券 + 技术指标 + 估值情绪 + L1/L2 因子宽表（最高 329 列）。

而 quant-web 的真实需求是：**用户自建的几个标的，各 250 根日线**。
用 60 GB 的全市场因子库去服务这个需求，是拿卡车送外卖。

### 补充：现状并不缺什么

quant-web 的「建议 → 反馈 → 再预测」闭环已经用本地四套技术指标策略
（动量 / 均值回归 / 通道突破 / 基线）跑通，`predictor.run_strategy()` 是纯函数、零外部依赖、
参数前端可调。在功能未完全稳定的阶段引入一个需要 60 GB 数据 + Docker 全家桶的 ML 依赖，
与既定的小步增量原则相悖。

---

## 四、那实时数据呢？（2026-10-15 追问后补测）

> 「历史数据量大」只解释了 **QuantDB 离线包** 那一条链路。
> **实时行情是另一条完全独立的链路**，必须单独验证——补测后结论同样是**不接**，
> 但原因不同，而且证据更硬。

### 4.1 它确实有实时能力，还是独立服务

```
:8003  QuantMind Stream Service V2        ← 市场数据服务
       容器内 MARKET_DATA_SERVICE_URL=http://127.0.0.1:8003
```

共 14 个路由，实时相关的：

| 路由 | 用途 |
|---|---|
| `GET /api/v1/quotes/{symbol}` | 取实时行情（可带 `?source=`） |
| `GET /api/v1/quotes/{symbol}/latest` | 最新一条（**只查库**） |
| `GET /api/v1/quotes/{symbol}/series` | 时间窗口序列（算日内指标用） |
| `GET /api/v1/klines/{symbol}` | K 线 |
| `WS /ws/bridge` | **接收端** —— 外部 Agent 推数据进来的通道 |
| `GET /health` | → `{"status":"healthy","market_db":"connected","ws_connections":0,"ws_core_started":true}` |

### 4.2 它的实时数据从哪来：默认**不是自己抓**，是等人推

`market_app/services/quote_service.py` 注册了四个适配器：

| 适配器 | `?source=` | 实现 | 角色（README） |
|---|---|---|---|
| `RemoteRedisDataSource` | `remote_redis` | 读**远程 Redis** 的 `market:snapshot:{symbol}` | ✅ **默认 / 主力** |
| `TencentDataSource` | `tencent` | 抓 `https://qt.gtimg.cn/q=` | 备选 |
| `SinaDataSource` | `sina` | 抓 `https://hq.sinajs.cn` | 备选 |
| `IFindDataSource` | `ifind` | 同花顺 iFinD（**商业授权**） | 备选 |

关键在默认那一条：**`remote_redis` 是一份需要外部往里写的快照**。
谁写？就是 `/ws/bridge` 接的 **QMT Agent**（迅投 miniQMT，跑在 Windows + 券商账户上）。

实测：

| 检查 | 结果 |
|---|---|
| `ws_connections` | **0** —— 没有任何 Agent 连进来 |
| Redis `db3`（`REDIS_DB_MARKET=3`） | **0 个键** —— 没有任何快照 |
| `GET /quotes/000002.SZ`（不带 source，走默认） | **404** Quote not found |
| `GET /quotes/000002.SZ?source=tencent` | **404** |
| `GET /quotes/000002.SZ?source=sina` | **404** |
| `GET /quotes/000002.SZ/series?seconds=3600` | **200**，但 `{"count":0,"data":[]}` —— 接口通、无数据 |
| `GET /klines/000002.SZ?limit=5` | **200**，但 `{"total":0,"klines":[]}` |
| `GET /quotes/`（列表）、`GET /symbols/` | **500**（`column stocks.stock_code does not exist`，schema 已漂移） |

### 4.3 为什么强制走腾讯也失败：容器根本没网（最硬的一道锁）

这是补测里最关键的发现，也是**与历史数据那条完全不同的原因**：

```
容器网络模式：bridge（quantmind_quantmind-net）
容器 IP：172.18.0.5      网关：172.18.0.1        ← 纯 IPv4
宿主机：无 IPv4 默认路由（见 A股行情失效复盘.md）
```

容器的出站包从 `172.18.0.1` 出去，而宿主机没有 IPv4 路由 ——
**容器零出网能力**。三次对照实测同一请求 `https://qt.gtimg.cn/q=sz000002`：

| 位置 | 结果 |
|---|---|
| 宿主机直连 | ✅ **200**，真实数据（`v_sz000002="...万科A~000002~3.67...`） |
| 容器内（bridge，**现状**） | ❌ `URLError [Errno 101] Network is unreachable` |
| 容器内（`--network host`，临时实测后销毁） | ✅ **200**，DNS 解析到 IPv6 `240d:c040:1:40::14a` |

→ **不是"机器连不上腾讯"，是容器被 bridge 网络困在 IPv4 里。**

### 4.4 就算解决了出网，也没有增量

QuantMind 唯一"自己能抓"的实时路径是 `qt.gtimg.cn` ——
**而 quant-web 现在已经在直连它**（`datasource.fetch_index_quotes()` 走的就是这个域名）。
经 QuantMind 中转等于多一跳 HTTP + 一次落库，却**不多任何数据**，
还要背上它 market_db 的 schema 漂移（见 4.2 表里的两个 500）。

它的 K 线通道同样是死的 —— 与 quant-web 当初挂掉的是**同一批域名**：

```
web.ifzq.gtimg.cn/appstock/app/fqkline/get   → code=000
ifzq.gtimg.cn/appstock/app/kline/mkline      → code=000（分钟线）
api.quantmind.cloud (A 42.193.190.99，无 AAAA) → Network is unreachable
```

### 4.5 结论

**实时数据同样不用 QuantMind。** 三层原因，层层独立成立：

1. **它不自己产生实时数据** —— 默认源 `remote_redis` 需要 QMT Agent 从外部推，本机没有
2. **它的容器出不了网** —— bridge 纯 IPv4 + 宿主机无 IPv4 路由，连备选的腾讯适配器都跑不通
3. **前两条都解决也没增量** —— 它唯一能自给的源（`qt.gtimg.cn`）我们已在直连

> **有价值的副产物**：`qt.gtimg.cn` 在这台机器上**是通的**（宿主机 IPv6 直连实测 200 + 真实数据）。
> 如果将来 quant-web 要做**盘中/实时行情**，直接用它即可 —— **一行都不需要经过 QuantMind**。

---

## 五、将来若要接入：条件与路径

**好消息：唯独不缺的障碍是网络。** QuantMind 与 quant-web **同机**，
走 `http://127.0.0.1:8000` 是回环调用，**完全绕开 IPv6 出站限制**——比抓腾讯行情还稳。

> ⚠️ 注意方向：这句话说的是「**quant-web → QuantMind**」这一跳（入站，回环，永远通）。
> 而 **QuantMind → 外网** 那一跳是**断的**（容器 bridge 纯 IPv4，见 §4.3）。
> 所以接进来的只是它**库里的存量数据**；指望它去外面实时抓行情，抓不到。

### 前置条件（必须按序完成）

1. **导入 QuantDB 数据**：本机从魔搭下载 → 分批上传 → 落到 `/opt/quantmind/db`（容器内 `/app/db`）；
   或先只下 `1_kline_data/` 子集验证链路。**磁盘需先扩容或清理**。
2. **拿到鉴权 token**：`POST /api/v1/auth/login`，body 需带 `tenant_id`（缺则报 missing tenant_id）；
   默认账号 `admin / admin123`（**上线前务必改密码**）。
3. **确认 Engine 出信号**：数据到位后 `/api/v1/models/inference/latest` 应返回非空。

### 值得接的接口（按价值排序）

| 接口 | 用途 | 鉴权 |
|---|---|---|
| `/api/v1/models/inference/latest` | **Qlib ML 预测**（最初的设计目标，可替换/并联现有四指标策略） | 需 |
| `/api/v1/research/kline/{symbol}` | K 线（可作腾讯单源的冗余） | 需 |
| `/api/v1/market-calendar/is-trading-day` | 交易日历（现在 quant-web 只做「每天拉一次」的粗判断） | 需 |
| `/api/v1/stocks/search` | 代码检索（可作现有 smartbox 的备份） | 需 |
| `/api/v1/public/sync/stock-daily` | 逐日处理后数据 | **免鉴权** |
| `/api/v1/public/sync/feature-snapshots` | 逐日特征快照 | **免鉴权** |
| `/api/v1/public/sync/calendar` | 交易日历（当前 500，数据导入后应恢复） | **免鉴权** |

> 免鉴权的 `/api/v1/public/sync/*` 可以**开箱直连**，是最低成本的链路验证入口——
> 但请注意：**在数据导入前，它们只会返回空数组**（已实测）。

### 落地方式（若届时实施）

- 在 `predictor.py` 旁新增 `quantmind.py` 适配层，不污染现有纯函数引擎；
- 以环境变量开关（如 `QW_QUANTMIND_BASE=http://127.0.0.1:8000`）启用，**默认关闭**；
- 失败时静默回落到本地四指标策略，保证 quant-web 永不因外部依赖不可用而挂掉。

---

## 六、关于"要不要停掉空转的容器"

| 指标 | 数值 |
|---|---|
| 机器内存 | 7751 MB 总 / 可用 4512 MB |
| 4 个容器合计占用 | 约 **1.86 GB**（quantmind 1.22 G + celery 0.54 G + redis 14 M + db 84 M） |
| 负载 | 4 核，load average 0.26 |

**建议：保持运行。** 负载与内存都充裕，停掉省不下多少，反而将来要用时还得重建。
真正值得记的是「它在空转且没有数据」这个事实本身——已写入项目记忆 §1.1。

---

## 附：核查命令备忘

```bash
# 容器与服务
docker ps -a --format "{{.Names}}\t{{.Status}}\t{{.Ports}}"
for p in 8000 8001 8002 8003; do curl -s http://127.0.0.1:$p/; echo; done

# 路由清单
curl -s http://127.0.0.1:8000/openapi.json | python3 -c "import sys,json;print(len(json.load(sys.stdin)['paths']))"

# 数据是否就绪（免鉴权）
curl -s "http://127.0.0.1:8000/api/v1/public/sync/stock-daily?trade_date=2026-10-14&page=1&page_size=3"

# 业务表行数
docker exec quantmind-db psql -U quantmind -d quantmind -t -A -F ' | ' -c "select 'klines',count(*) from klines union all select 'stocks',count(*) from stocks;"

# 数据目录
ls -la /opt/quantmind/db/

# —— 实时链路（§四）——
# Stream 服务健康（看 ws_connections 是否为 0）
curl -s http://127.0.0.1:8003/health

# 实时取数（默认 remote_redis；可强制 ?source=tencent|sina）
curl -s -w "\n[%{http_code}]\n" "http://127.0.0.1:8003/api/v1/quotes/000002.SZ?source=tencent"

# 容器出网能力（决定性判据）
docker inspect quantmind --format "NetworkMode={{.HostConfig.NetworkMode}}"
docker exec quantmind python -c "import urllib.request;print(urllib.request.urlopen('https://qt.gtimg.cn/q=sz000002',timeout=12).status)"
# 对照：用 host 网络跑同一镜像（临时，跑完自动销毁）
IMG=$(docker inspect quantmind --format "{{.Config.Image}}")
docker run --rm --network host "$IMG" python -c "import urllib.request;print(urllib.request.urlopen('https://qt.gtimg.cn/q=sz000002',timeout=12).status)"

# 数据源适配器清单与默认值
docker exec quantmind sh -c "grep -n 'class \|BASE_URL' /app/backend/services/stream/market_app/services/data_source.py"
docker exec quantmind sh -c "grep -n 'data_sources = {' -A 8 /app/backend/services/stream/market_app/services/quote_service.py"

# Redis 行情快照（REDIS_DB_MARKET=3，0 键 = 无源在推）
docker exec quantmind-redis redis-cli -n 3 dbsize
```
