# QuantMind 接入评估（2026-10-15 实测）

> **一句话结论：暂不接入。**
> 不是因为技术上接不上——接口活得好好的、还是同机直连；而是**它的前置数据（QuantDB 全量约 60 GB）
> 在这台 VPS 上既下不来、也放不下**，而 quant-web 实际只需要几个标的的日线。
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

## 四、将来若要接入：条件与路径

**好消息：唯独不缺的障碍是网络。** QuantMind 与 quant-web **同机**，
走 `http://127.0.0.1:8000` 是回环调用，**完全绕开 IPv6 出站限制**——比抓腾讯行情还稳。

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

## 五、关于"要不要停掉空转的容器"

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
```
