# 慢量化操作台 · QuantMindLite

> 面向 A 股的「慢量化」个人操作台：系统基于策略给出操作建议，由你手动执行并回填，再据此继续预测 —— **人机协作、不自动下单**。

[![License: 自用/自部署](https://img.shields.io/badge/license-自部署用途-blue.svg)](./README.md)
[![Stack: FastAPI + SQLite](https://img.shields.io/badge/stack-FastAPI%20%7C%20SQLite-009688.svg)](./README.md)
[![Frontend: HTML/JS + ECharts](https://img.shields.io/badge/frontend-HTML%2FJS%20%7C%20ECharts-orange.svg)](./README.md)

---

## 一、定位

「慢量化」的核心思想是**低频、可解释、人在回路**：

- 系统用既定策略计算买卖建议（预测价 + 区间 + 信号），**不直接下单**；
- 由你自己判断是否执行、以什么价格成交，并把实际结果回填系统；
- 系统依据你的反馈滚动重算预测，形成「**建议 → 反馈 → 再预测**」的闭环；
- 支持**多项目并行**，适合从模拟仓位逐步过渡到实仓跟踪。

---

## 二、功能特性

| 模块 | 说明 |
| --- | --- |
| 📈 多项目跟踪 | 每个标的一个项目，独立记录行情、预测、反馈与持仓。 |
| 🧭 四套策略 | `momentum` 动量 / `meanreversion` 均值回归 / `breakout` 通道突破 / `baseline` 基线演示。 |
| 🎚 策略在线调参 | 参数在「策略设置」面板即时调整，预测线随之重算。 |
| 🔮 滚动预测 | 对历史每个交易日用截至当日数据计算信号，并外推未来 **5 个交易日**。 |
| 📊 预测 vs 实际 | 金色虚线 = 策略预测价 + 上下界区间带；蓝线 = 实际行情。 |
| 🚦 信号标记 | 红▲买 / 蓝▼卖 / 绿◆HOLD；悬停任意一天查看各策略当日建议。 |
| 💼 持仓盈亏回放 | 录入当前持仓（数量 / 成本），自动回放盈亏。 |
| 👥 多用户登录 | 邮箱即用户名，首个注册用户自动成为管理员；普通用户仅见自己的项目。 |
| 📌 项目置顶 | 左侧菜单点击 📌 将项目置顶，置顶项始终排在列表最前。 |
| 🔓 游客只读模式 | 无需登录即可浏览指定标的（`000002.SZ`）的预测与行情，不可增删 / 编辑。 |
| 🕒 图表时间范围 | 支持「全部 / 30 / 60 / 90 / 180 / 365 天」切换，默认 30 天。 |
| 🌗 浅 / 深色主题 | 右上角一键切换并记忆，图表配色随主题重绘，无闪烁。 |
| 🔄 行情限频拉取 | 游客模式每天仅实际调用一次数据源；当日数据已存在则提示无需重复获取。写入按品种分：场内覆盖式重写、场外合并写入（见 §4）。 |
| 📰 资讯工作台（首页） | RSS / Atom 订阅驱动的资讯流：日期分组、区间与分类筛选、搜索、收藏；订阅源可增删启停、单个抓取，并支持 **OPML 批量导入**。抓取统一走 Cloudflare 中继（见 §8）。 |
| 🗣 圆桌讨论 | 多 AI 席位（手动回填提示词 / LLM Key 自动）各自给出每日观点；**观点保存时自动在论坛建档一条存档帖**，方便后期回看历史与修订过程（见 §8）。 |
| 📮 每日持仓日报 | 每交易日 22:00 自动把「总览 / 大盘指数 / 持仓明细 / 近期操作」总结成一篇帖子发到论坛（署名「系统日报」，`author_type=system`）；仪表台页头可手动补发。每天固定一帖，内容没变不重复写（见 §8）。 |

---

## 三、技术框架

- **前端**：原生 HTML / JS + [ECharts](https://echarts.apache.org/)（CDN 引入），**无构建步骤**，静态文件直出，改完即生效。
- **后端**：[FastAPI](https://fastapi.tiangolo.com/) + [SQLite](https://www.sqlite.org/)，独立本地库，不依赖其它服务数据库。
- **部署**：IPv6 VPS，`uvicorn` 仅监听 `127.0.0.1:8090`（本地回环），`systemd` 托管，对外由 **nginx 反向代理**（80/443）暴露；静态资源经中间件禁用缓存。
- **数据源**：行情接入**多源回退** —— 腾讯 gtimg → 新浪 → akshare → 东方财富，单源限流自动切换；**仅支持 A 股（含前复权处理）**。
- **预测框架（QuantMind）**：设计理念源自开源框架 [**QuantMind**](https://gitee.com/qusong0627/quantmind)（基于微软 [Qlib](https://github.com/microsoft/qlib) 的 A 股量化预测框架）。本项目借鉴其「预测 → 执行 → 反馈」闭环思想自建轻量 Web 层，**运行不依赖 QuantMind / Qlib 代码**；预测引擎 `predictor.run_strategy()` 为纯函数，默认使用四套可解释的技术指标策略（动量 / 均值回归 / 通道突破 / 基线），引擎设计上可替换——如需接入 Qlib ML 预测可新增实现替换 `run_strategy()`。

---

## 四、项目架构设计

```
┌───────────────────────────── 浏览器（SPA） ─────────────────────────────┐
│  登录 / 游客双入口 · 项目面板 · 预测 vs 实际图表 · 策略设置 · 用户管理      │
│  时间范围选择 · 浅/深主题 · 置顶排序（原生 HTML/JS + ECharts，无构建）      │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ REST（Bearer token；游客接口免鉴权）
┌──────────────────────────────────▼──────────────────────────────────────┐
│                         API 层（FastAPI · app.py）                       │
│  鉴权(pbkdf2+会话) → 项目 CRUD/置顶 → 行情 → 预测 → 反馈 → 图表/共识 → 游客 │
└───────┬───────────────────────────────────────┬─────────────────────────┘
        │                                        │
┌───────▼───────────────┐          ┌─────────────▼──────────────────────┐
│   服务层               │          │ 数据层（独立 SQLite · db.py）        │
│ datasource.py         │          │ projects / market_prices           │
│   行情多源回退抓取      │          │ predictions / feedbacks            │
│ predictor.py          │          │ users / sessions / password_reset  │
│   四策略滚动预测+外推   │          │ 多用户按 owner_id 隔离，迁移自动补列  │
│ （可插拔 QuantMind）   │          │                                     │
└───────────────────────┘          └─────────────────────────────────────┘
```

**分层与闭环要点**

- **前端**：单页应用，登录态与游客只读态双入口；「预测线 vs 实际线」图叠加历史信号标记，悬停查看各策略当日建议。
- **API 层**：统一鉴权中间件 + `get_current_user` 依赖注入，按 `owner_id` 隔离数据；管理员可见全部项目。
- **服务层**：行情 `datasource.py` 多源回退（腾讯 → 新浪 → akshare → 东方财富，GBK 解码处理）；预测 `predictor.py` 对每个历史交易日用截至当日数据滚动计算信号并外推未来 5 个交易日，策略/参数在线可调。
- **数据层**：独立 SQLite 文件，`create_all` 自动建表 + `ALTER` 兼容迁移（如 `strategy_type` / `owner_id` / `last_fetch_date` / `pinned` 等后加列）。
- **核心闭环**：建议 → 反馈 → 再预测；多项目并行、行情限频拉取（每天一次）、游客只读、置顶排序。

---

## 五、目录结构

```
quant-web/
├── app.py            # FastAPI 后端：鉴权、项目、行情、预测、反馈、图表、游客接口
├── db.py             # SQLite 数据层（自动建表 + 兼容列迁移）
├── models.py         # Pydantic 请求 / 响应模型
├── predictor.py      # 策略引擎：rolling_predict / forecast_future / 四策略（纯函数、可替换）
├── datasource.py     # 行情多源回退抓取（前复权）
├── requirements.txt  # 依赖：fastapi / uvicorn / sqlalchemy / pydantic / akshare
├── deploy.sh         # 部署 / 运维工具：本地验证 → 线上备份 → 同步 → 重启 → 健康检查（失败自动回退）
├── scripts/          # 运维脚本：db_backup.sh / fetch_fund_nav.py（基金补数）/ sync_roundtable_forum.py（圆桌观点回填论坛存档）/ daily_report.py（每日持仓日报）
└── static/
    ├── index.html    # 单页应用骨架（顶栏：关于 / 用户管理 / GitHub / 主题切换）
    ├── app.js        # 前端逻辑：登录、项目、图表、游客、主题
    └── style.css     # CSS 变量主题（[data-theme="light"] 浅色）
```

> 数据库文件 `quantweb.db` 与 `.venv/` 已在 `.gitignore` 中排除，**不会**进入版本库，确保用户数据不外泄。

---

## 六、本地快速开始

### 1. 环境要求

- Python 3.10+
- 可访问外网的行情数据源（用于首次拉取标的行情）

### 2. 安装与启动

```bash
# 进入项目目录
cd quant-web

# 创建虚拟环境（可选但推荐）
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 启动（默认 8090 端口）
uvicorn app:app --host 0.0.0.0 --port 8090 --reload
```

启动后浏览器访问 <http://localhost:8090>。

### 3. 首次使用

1. 在登录页点击 **「注册」**，使用邮箱作为用户名完成注册 —— **第一个注册用户自动成为管理员**。
2. 进入后新建项目，填写标的代码（如 `600519.SH`）并选择策略。
3. 点击「拉取行情」同步历史数据，系统将自动生成预测线与信号标记。
4. 实际买卖后在「反馈」中回填，系统据以继续预测。

### 4. 游客模式

在登录页点击 **「游客访问」**，即进入只读视图：

- 仅展示指定标的（`000002.SZ`）的预测与实际走势；
- 默认 30 天视图，可切换时间范围（全部 / 30 / 60 / 90 / 180 / 365 天）；
- 行情每日最多自动拉取一次，避免频繁调用数据源；
- 不支持增删项目、录入反馈或编辑任何数据。

**行情写入语义（游客与登录两条入口完全一致）** —— 见 `app.py` 的 `_resolve_days` / `_store_rows`：

| 品种 | 写入方式 | 原因 |
| --- | --- | --- |
| 场内（股票 / 场内基金 / 债券） | **覆盖式重写**（先删后写） | 前复权价会随除权整体平移，只补新日期会让除权点两侧混着两套复权基准，曲线出现假跳空。窗口自动放宽到至少覆盖存量条数，且数据源返回条数少于存量时**跳过写入**，避免截短历史。 |
| 场外基金 | **合并写入**（upsert 补缺 / 改值） | 净值接口一次只回一段窗口，先删后写会把窗口外的历史（可能上千条）整段丢掉。 |

---

## 七、配置说明

| 配置项 | 位置 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 游客标的代码 | `app.py` → `GUEST_STOCK_CODE` | `000002.SZ` | 游客模式公开只读的标的。 |
| 服务端口 | `uvicorn` 启动参数 / `systemd` | `8090` | 可按需修改。 |
| 数据库路径 | `db.py` → `DB_PATH` | `./quantweb.db` | 独立 SQLite 文件。 |
| 行情回退顺序 | `datasource.py` | 腾讯（proxy.finance → web.ifzq）→ akshare → 东方财富 | 品种是**第一层路由**：场内走腾讯日线、场外基金走东财净值。A 股的 akshare/东财兜底在本 VPS 必然失败，已由 `QW_DEAD_FALLBACK` 默认短路（换到有 IPv4 的机器设 `=1` 恢复）。详见《A股行情失效复盘.md》。 |
| 主题记忆 | 浏览器 `localStorage` → `qw_theme` | `dark` | 支持 `light` / `dark`。 |

---

## 八、API 概览

所有受保护接口需在请求头携带 `Authorization: Bearer <token>`（游客接口除外）。

**鉴权**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/register` | 注册（首个用户为管理员） |
| POST | `/api/auth/login` | 登录获取 token |
| POST | `/api/auth/logout` | 登出 |
| GET | `/api/auth/me` | 当前用户信息 |
| POST | `/api/auth/forgot-password` | 提交「忘记密码」申请 |
| GET | `/api/auth/admin/users` | 管理员：用户列表 |
| GET | `/api/auth/admin/reset-requests` | 管理员：重置申请列表 |
| POST | `/api/auth/admin/reset-password` | 管理员：重置为随机密码 |

**项目与数据**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/projects` | 当前用户的项目列表 |
| POST | `/api/projects` | 新建项目 |
| GET / DELETE | `/api/projects/{pid}` | 项目详情 / 删除 |
| POST / GET | `/api/projects/{pid}/prices` | 录入 / 查询行情 |
| POST | `/api/projects/{pid}/fetch` | 拉取行情（多源回退） |
| POST | `/api/projects/{pid}/predict` | 生成预测建议 |
| POST | `/api/projects/{pid}/feedback` | 回填实际操作 |
| GET | `/api/projects/{pid}/chart` | 图表数据（预测 vs 实际） |
| GET | `/api/projects/{pid}/consensus` | 多策略共识 |

**策略与游客**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/strategies` | 可用策略目录 |
| GET | `/api/guest/projects` | 游客可见项目（仅公开标的） |
| GET | `/api/guest/projects/{pid}` | 游客项目详情 |
| GET | `/api/guest/projects/{pid}/chart` | 游客图表数据 |
| GET | `/api/guest/projects/{pid}/consensus` | 游客策略共识 |
| GET | `/api/guest/projects/{pid}/refresh` | 游客行情限频刷新（每日一次）。⚠️ 是 GET 但**会写库**（写入语义同 `/api/projects/{pid}/fetch`），且免鉴权——仅对 `GUEST_STOCK_CODE` 项目生效。 |

**AI 席位（圆桌）**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `/api/roundtable/seats` | 席位列表 / 新增（管理员） |
| PATCH / DELETE | `/api/roundtable/seats/{sid}` | 改席位（名称 / 人格 / 通道 / 角色 / 启停）/ 删除（连带其历史观点） |
| GET | `/api/roundtable/snapshot` | 今日快照（大盘指数 + 当前持仓） |
| GET | `/api/roundtable/history` | 历史标的与仓位操作记录 |
| GET | `/api/roundtable/prompt?seat_id=` | 生成该席位今日提示词（手动通道复制用） |
| GET / POST | `/api/roundtable/posts` | 当日各席位观点 / **手动粘贴回填**（覆盖当日该席位观点） |
| GET | `/api/roundtable/settings` | 管理员：LLM 服务商配置状态（含 `group` 分组、`reachable` 本机可达性） |
| PUT | `/api/roundtable/settings` | 管理员：保存某服务商配置。`api_key: ""` = 清 Key；`delete: true` = 删自定义服务商（内置项拒绝） |
| POST | `/api/roundtable/generate` | 管理员：对 `mode=auto` 的席位并发调 LLM 生成今日观点 |

> **观点 → 论坛存档（2026-10-17 起）**：圆桌的两个写入口（`POST /api/roundtable/posts`
> 手动粘贴、`POST /api/roundtable/generate` 自动生成）在保存观点后，会**自动**把这条观点
> 同步成一条论坛存档帖，方便后期回看历史信息。规则：
>
> - 每个「**席位 × 日期**」固定一个主题帖，标题 `【圆桌】YYYY-MM-DD · 席位名`，
>   作者身份为 `author_type=agent` 的该席位（显示名 / AI 角色实时解析，席位改名即同步）；
> - 内容与帖内最后一层**相同 → 不写**（反复点「生成」不会把论坛刷满）；
> - 内容**有变化 → 在同一帖内追加一层跟帖**并标注 `（第 N 次更新 · 北京时间）`，
>   保留修订过程；圆桌侧本身的语义不变（仍是「每席位每天一条、覆盖更新」）；
> - 存档帖被手动删除后再提交，会自动**重建**并改写关联（`roundtable_posts.thread_id`）；
> - 存档失败**不回滚观点**（观点已入库，只记 warning），圆桌写入口照常返回 200；
> - 接口返回体带 `thread_id`，席位卡片据此出现「论坛存档」按钮，一键跳到对应主题帖。
>
> 历史观点回填（幂等，只补 `thread_id` 为空的观点）：
> ```bash
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/sync_roundtable_forum.py --dry-run
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/sync_roundtable_forum.py
> ```

**每日持仓日报**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/report/daily` | 管理员：生成当日持仓日报并发布到论坛；返回 `created` / `unchanged` / `skipped` 三态与 `thread_id` |

> **日报 → 论坛（2026-10-17 起）**：把「仓位、收益变化 + 大盘指数」算成一篇帖子发到论坛，
> 方便日后回看。与圆桌存档帖的分工：圆桌存档是**搬运**（席位观点原样落到论坛），
> 日报是**算出来的新内容**；与交易仪表台的分工：仪表台看「此刻持仓」，日报看「较上一期变了多少」。
>
> - **身份**：`author_type=system`（署名「系统日报」+「自动」徽章），不占用任何 AI 席位，
>   也不计入论坛的「Agent 参与数」；只有管理员能删。
> - **节奏**：定时任务每交易日 22:00（北京时间）自动发布 —— 之所以不是收盘就发，是因为
>   持仓 13/16 是场外基金，净值要到 19:00–22:00 才由基金公司陆续披露（QDII 更是 T+1/T+2）。
> - **幂等**：`daily_reports` 表以日期为主键，**每天固定一个主题帖**（标题 `【日报】YYYY-MM-DD · 持仓与收益`）。
>   正文与帖内最后一层一致 → **不写**；有变化 → 在**同一帖追加一层**并标注 `（第 N 次更新 · 北京时间）`。
>   帖子被删后再发布会自动重建。所以定时任务重跑、当天补数后再跑都不会刷屏，同时保留修订痕迹。
> - **收益口径**：`equity = 已实现盈亏 + 持仓浮盈`，与项目页盈亏曲线**同源**
>   （唯一实现 `app._pnl_curve`，两处共用，避免数字对不上）。买入不计入收益、卖出把浮盈转为已实现，
>   因此「本期变化」**不含新增投入的本金**；各标的最新行情日期不一致时基准标「按各标的最新一期行情」。
> - **无持仓时不发布**（返回 `skipped`），避免每天刷一条空帖。
> - 手动补发 / 重发：仪表台页头「发布日报到论坛」按钮，或下面的脚本。

> 日报脚本（定时任务与人工共用；`--no-fetch` 离线调试、`--date` 补发指定日期）：
> ```bash
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/daily_report.py --dry-run --no-fetch
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/daily_report.py            # 先补行情再发布
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/daily_report.py --date 2026-10-16
> ```
> 补数**直接复用网页端 `app.fetch_prices`**（同一函数），窗口口径、覆盖式/合并式写入语义、
> 缩水守卫、`last_fetch_date` 限频全部一致 —— 不另造一套拉取实现。单只失败只记 warn，不阻断日报。

> **服务商清单的唯一数据源**是 `app.py → PROVIDER_DEFAULTS`（内置 19 家），由
> `GET /api/roundtable/settings` 一并下发（前端不再硬编码），每项带 `group`
> （国内 / 海外 / 本地 · 自建 / 自定义），设置页据此分组。除内置项，还可用
> `PUT` 自建任意 OpenAI 兼容服务商：名称须匹配 `[A-Za-z0-9_-]{1,32}` 且**必须自带 `base_url`**
> （自定义项没有内置默认值）。自定义项固定归入「自定义」分组，入口固定在设置页顶部。
>
> 内置分组：**国内 12 家**（智谱 GLM、通义千问、火山豆包、DeepSeek、Kimi、硅基流动、
> 百度千帆·文心、腾讯混元、讯飞星火、阶跃星辰、零一万物、百川）· **海外 6 家**
> （OpenAI、OpenRouter、Google Gemini、xAI Grok、Mistral、Groq）· **本地 1 家**（Ollama）。
> `model` 只是**默认值**，各厂商改版较快，设置页里可随时改；协议一律是 OpenAI 兼容的
> `/chat/completions`（所以 Anthropic 原生接口这类不在此列，要用得自己套一层网关）。
>
> ⚠️ **本 VPS 只有 IPv6 出口**，所以只有域名带真 AAAA 的服务商才连得上。`GET` 返回的
> `reachable` 即本机到该 **base_url 对应 host:port** 的 TCP 连通性（带 5 分钟缓存），
> 设置页据此标「可达 / 不可达」。按 base_url 取端口是刻意的：本地自建（如 Ollama 的
> `http://127.0.0.1:11434/v1`）写死 443 会恒判不可达。
> 2026-10-17 在本机实测连通性：**可达** `qianfan` / `stepfun` / `yi` / `baichuan` /
> `gemini` / `grok`（api.x.ai）/ `mistral` / `groq` / 以及原有的 `zhipu` / `qwen` / `doubao` /
> `siliconflow` / `openrouter` / `openai`；**不可达（无 AAAA）** `deepseek` / `moonshot` /
> `hunyuan` / `spark`。另注：`openai` 虽然连得上，但被**按地区拒绝**
> （403 `unsupported_country_region_territory`），中国大陆出口实际用不了。

**资讯工作台**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/news/items` | 资讯流（区间 / 分类 / 搜索 / 只看收藏），返回条目、分类 chips 与统计 |
| GET | `/api/guest/news/items` | 游客只读的同一份资讯流（不暴露订阅源地址与中继配置） |
| GET / POST | `/api/news/feeds` | 订阅源列表 / 新增单个源 |
| PATCH / DELETE | `/api/news/feeds/{fid}` | 改名分类、启停 / 删除（删除会**连带删除**该源已入库条目） |
| POST | `/api/news/feeds/import-opml` | 管理员：批量导入 OPML（详见下） |
| POST | `/api/news/fetch` | 管理员：抓取入库；带 `feed_id` 则只抓该源 |
| GET / PUT | `/api/news/settings` | 管理员：中继地址与密钥（密钥只回 `has_key`，永不回显） |

> **OPML 导入**：请求体 `{content, category?, fetch?}`，`content` 是 OPML **原文**
> （前端读文件或粘贴后上传）。刻意**不支持**「把地址交给服务端去取」——本机只有 IPv6
> 出站，而 OPML 所在主机未必在中继白名单里。导入后默认**立即实抓一次**并逐条回报结果：
> OPML 只是一份地址清单，它对「这些源还活不活」一无所知。

> **源状态三档**：`ok` 正常 / `empty` **空源**（可访问但 0 条目）/ `error` 抓取失败。
> 「空源」必须单列一档：HTTP 200 却不含任何 `<item>` 的僵尸源并不罕见
> （典型样本：新浪财经 RSS，`rss.sina.com.cn/*` 全部源自 2018-09-23 起冻结，只剩一个空壳），
> 记成「成功」会让人以为一切正常。

> ⚠️ **新增订阅源前，先确认域名在中继白名单里** —— `rss-relay/wrangler.toml` 的
> `ALLOW_HOSTS`（逗号分隔、按域名后缀匹配，写 `wallstreetcn.com` 即放行其子域），
> 改完须 `wrangler deploy` 才生效，否则取回的是 `403 host not allowed`。
> 白名单只解决「本机连不上」，**不解决「源是空的」**——两者是独立的坑。
>
> 当前白名单：`aihot.news`、`chinanews.com.cn`、`wallstreetcn.com`、`jiemian.com`、`eastmoney.com`。
> 其中华尔街见闻的 RSS 末尾被其 CDN 追加了一段统计 `<script>`（源站把 Content-Type 标成
> `text/html` 触发 Cloudflare HTML 改写），严格 XML 解析会抛 `junk after document element`；
> `newsfetch._xml_root` 对这一种错法做了截断容错。

---

## 九、部署（IPv6 VPS + systemd）

运维脚本 `deploy.sh`（本地终端运行，`ssh -6` 直连 VPS）覆盖「本地验证 → 线上部署 → 故障回退」全流程：

```bash
bash deploy.sh verify                # 本地验证：语法 + 导入 + 冒烟启动（临时副本，不污染本地 db）
bash deploy.sh status                # 生产体检：SSH / systemd / health / 磁盘 / db / 备份列表
bash deploy.sh deploy --dry-run      # 只看将要同步的文件，不动线上
bash deploy.sh deploy                # 完整部署（默认）
bash deploy.sh deploy --skip-verify  # 跳过本地验证（紧急修复）
bash deploy.sh backup                # 只打一份线上代码快照
bash deploy.sh backups               # 列出线上备份
bash deploy.sh rollback [TS]         # 回退到指定备份（默认最近一份）
```

`deploy` 流程：

1. **本地验证**：`py_compile` 语法检查 → `import app` → 随机端口拉起 uvicorn 冒烟（校验 `/api/health` 与首页 200），任一失败即终止，不碰线上；
2. **生产预检**：SSH 连通性 + 部署前 `systemctl is-active` 状态；
3. **差异比对**：md5 清单比对（`scripts/file_manifest.py`，本地/远端各算一份再 diff）打印将要变更的文件；
4. **备份**：远端把当前版本快照到 `/opt/quant-web-releases/pre-<时间戳>`（排除 `.venv` / `quantweb.db` / `__pycache__` / `._*`，保留最近 5 份）；
5. **同步 + 装依赖 + 重启**：`tar` over `ssh` 覆盖源码（macOS 自带 openrsync 不支持 IPv6 remote 目标语法，故不用 rsync），`pip install -r requirements.txt`（阿里云镜像优先），`systemctl restart quant-web`；
6. **健康检查 + 自动回退**：轮询 `/api/health` 最多 30 秒，失败则自动解压本次 `pre-` 快照覆盖回运行目录、重启并复检，同时打印 `journalctl` 尾部。

> 备份与回退**只覆盖源码与静态资源，不动数据库**。涉及建表 / 迁移的改动，请先自行备份 `quantweb.db`（`bash deploy.sh db-backup`）。

> 静态文件（HTML / JS / CSS）免重启，改完即生效；后端改动需 `systemctl restart quant-web`。
> 资源版本号以 `index.html` 中 `?v=N` 控制，部署后建议浏览器**硬刷新**（Cmd/Ctrl + Shift + R）。

### 定时任务（VPS crontab）

| 时间 | 任务 | 说明 |
| --- | --- | --- |
| `0 14 * * 1-5` | `/opt/quant-web/.venv/bin/python /opt/quant-web/scripts/daily_report.py` | 每**交易日 22:00**（北京时间）自动发布「每日持仓日报」到论坛。先补最新行情（复用 `app.fetch_prices`），再生成并发布；每天固定一帖、内容没变不重写、无持仓则跳过。日志 `/var/log/quant-daily-report.log`。 |
| `30 13 * * 1-5` | `/usr/bin/python3 /opt/quant-web/scripts/fetch_fund_nav.py` | 交易日收盘后自动补**场外基金**净值。窗口 365 自然日、与主系统 `fund` 口径一致；复用 `datasource.fetch_hist`（不再自带第二套取数实现）；**合并写入**不删历史，并回写 `projects.last_fetch_date` 以免网页端当天重复拉取。日志 `/var/log/quant-fundnav.log`。 |
| `0 12 * * *` | `/opt/quant-web/scripts/db_backup.sh` | 线上库热备（`sqlite3 .backup` + `integrity_check`）。日志 `/var/log/quant-db-backup.log`。 |

> ⚠️ **crontab 表达式的时区是服务器时区 `Etc/UTC`**（不是北京时间）：`30 13 * * 1-5` = 北京 21:30、
> `0 14 * * 1-5` = 北京 22:00。写定时任务时用 `date -u` 对齐，别按本机时间直接填。

> **场内品种（股票 / 场内基金 / 债券）没有定时任务**，只在打开项目页时自动拉取一次（限频每日一次）。
> 这是已知留白：无人访问则不会更新，且失败无告警。

### nginx 反向代理（服务默认仅监听 127.0.0.1:8090）

```nginx
server {
    listen 80;                      # 或 443 ssl（配证书）
    server_name 你的域名或IP;       # 如 <生产服务器 IPv6（已脱敏）> 或域名

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        client_max_body_size 10m;
    }
}
```

`nginx -t` 通过后 `systemctl reload nginx` 生效。服务健康检查请用 `curl -s http://127.0.0.1:8090/api/health`（`::1` 未监听，勿再用 `localhost` 的 IPv6 形式）。

---

## 十、安全与隐私

- 密码使用 `pbkdf2_sha256`（10 万次迭代）哈希存储，无明文。
- 多用户数据隔离：普通用户仅能访问 `owner_id` 属于自己的项目。
- 会话采用 Bearer token（7 天有效期），支持登出吊销。
- 管理员可处理用户的「忘记密码」申请并重置为随机密码。
- 数据库文件 `quantweb.db` 已通过 `.gitignore` 排除，**不上传**至代码仓库，用户数据不外泄。

---

## 十一、数据来源与免责声明

- 行情数据来自第三方公开接口，可能因接口变更 / 限流而中断，系统已做多源回退。
- 本项目所有策略信号**仅供学习与参考**，不构成任何投资建议；据此操作风险自负。
- 系统**不执行任何自动交易**，一切买卖决策与下单均由用户手动完成。
- 仅限**本地 / 自部署**用途。

---

<p align="center">慢量化操作台 · 建议 → 反馈 → 再预测</p>
