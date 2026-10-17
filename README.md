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
| 📰 资讯工作台（首页） | RSS / Atom 订阅驱动的资讯流：日期分组、区间与分类筛选、搜索、收藏；订阅源可增删启停、单个抓取，并支持 **OPML 批量导入**。抓取统一走 Cloudflare 中继（见 §8）。**AI 结构化加工**（2026-10-17）：每日定时把资讯送 LLM 评分（重要度 1-5 / 推荐理由 / 标签 / 关联标的代码），条目卡显示评分徽标与「持仓相关」徽标，筛选栏可**只看相关**；管理员可手动点「AI 加工」立即补跑。 |
| 🧠 资讯 → Agent 注入 | 圆桌席位提示词新增「近期相关资讯」节：近 72 小时、持仓相关（★）优先 + 市场级大事补位，共 10 条；**建议必须引用资讯条目编号**，无依据标「纯技术面判断」。同一份加工数据不再另算。 |
| 🗣 圆桌讨论 | 多 AI 席位（手动回填提示词 / LLM Key 自动）各自给出每日观点；**观点保存时自动在论坛建档一条存档帖**，方便后期回看历史与修订过程。席位卡支持**单卡折叠**与**全部折叠 / 全部展开**（折叠状态按席位 id 记在本地，刷新不丢）；卡片上可一键**查看存档**跳到该帖，万一存档没建成则改为**补建存档**。席位的「人格 / 风格提示词」拆成**独立的风格模板管理项**（内置 8 套 + 自定义，席位选模板、改模板即全局生效，见 §8）。圆桌四个数据面板（今日大盘 / 账户概览 / 持仓总览 / 历史记录）均支持收起 / 展开。 |
| 📮 周期结算帖 | **日 / 周 / 月 / 季 / 半年 / 年**六个周期各自自动把「区间结算 / 大盘指数 / 持仓明细 / 区间操作」算成一篇帖子发到论坛（署名「系统日报」，`author_type=system`）；仪表台页头可选周期手动补发。同周期固定一帖，内容没变不重复写（见 §8）。 |
| 📰 市场概要帖 | 每日一篇论坛主题帖（署名「市场概要」，`author_type=news`）：昨日关注复盘（兑现/落空/进行中）+ 今日大事 3-5 条（带来源）+ 数据看点（指数/账户/持仓真实数据）+ 逐只持仓影响（方向+理由+置信度）+ 明日关注（带跟踪指标），由当日高分资讯 + 账户快照生成，并经同 Key 二次审阅；与结算帖分工 = 一个算账、一个说事。 |
| 📊 账户三指标 | 交易仪表台 KPI 与圆桌页同时展示**总市值 / 浮动盈亏 / 累计实现盈亏**，由 `app._account_totals` 统一计算，两处数字必然一致。 |
| 🎯 观点复盘（G3） | 圆桌「建议 → 结果」闭环：每日观点经 LLM 抽取成「标的+方向+依据资讯」结构化判断，再**机械比对真实行情**判 T+1/T+5 命中率（LLM 不自评对错）。圆桌页新增「观点复盘」面板（席位命中率 / 引用资讯信息量 / 样本明细 + 趋势图），席位卡在样本达标时显示「命中 X%」徽标。 |

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
├── scripts/          # 运维脚本：db_backup.sh / fetch_fund_nav.py（基金补数）/ sync_roundtable_forum.py（圆桌观点回填论坛存档）/ settle_report.py（周期结算帖：日/周/月/季/半年/年）/ news_fetch.py（定时抓取订阅源，早晚各一次）/ news_score.py（资讯 AI 加工：评分/理由/标签/关联标的）/ news_digest.py（市场概要帖）
├── fund_profiles.json  # 持仓行业画像（标的 → 行业 + 关键词）：让「持仓相关」覆盖场外基金
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
| GET / POST | `/api/roundtable/seats` | 席位列表 / 新增（管理员）。席位用 `style_id` 引用风格模板，为空 = 自定义 |
| PATCH / DELETE | `/api/roundtable/seats/{sid}` | 改席位（名称 / 风格模板 / 通道 / 角色 / 启停）/ 删除（连带其历史观点）。`style_id: 0` = 改回自定义 |
| GET / POST | `/api/roundtable/styles` | 风格模板列表（登录可读）/ 新增自定义模板（管理员） |
| PATCH / DELETE | `/api/roundtable/styles/{style_id}` | 改 / 删风格模板（管理员）。内置模板不可删；仍被席位引用的不可删 |
| GET | `/api/roundtable/snapshot` | 今日快照（大盘指数 + 当前持仓 + `totals` 账户三指标，与仪表台 KPI 同源） |
| GET | `/api/roundtable/history` | 历史标的与仓位操作记录 |
| GET | `/api/roundtable/prompt?seat_id=` | 生成该席位今日提示词（手动通道复制用） |
| GET / POST | `/api/roundtable/posts` | 当日各席位观点 / **手动粘贴回填**（覆盖当日该席位观点） |
| GET | `/api/roundtable/settings` | 管理员：LLM 服务商配置状态（含 `group` 分组、`reachable` 本机可达性） |
| PUT | `/api/roundtable/settings` | 管理员：保存某服务商配置。`api_key: ""` = 清 Key；`delete: true` = 删自定义服务商（内置项拒绝） |
| POST | `/api/roundtable/generate` | 管理员：对 `mode=auto` 的席位并发调 LLM 生成今日观点 |
| POST | `/api/roundtable/archive` | 管理员：**补建**某席位某日观点的论坛存档（`{seat_id, date?}`），幂等、可重复调 |
| GET | `/api/roundtable/review/stats` | **观点复盘面板数据**（G3，登录可读）：席位命中率 / 趋势 / 引用资讯信息量 / 样本明细。判定全部机械（方向 × 真实收益），LLM 只做「文本 → 结构化」抽取 |
| POST | `/api/roundtable/review` | 管理员：**手动触发复盘**。`{date}` 复盘单日 / `{days}` 补批历史 / 都不给则跑每日（昨日 + 近窗补算）。抽取复用资讯 AI 的 LLM 配置 |

> **风格模板（2026-10-17 起）**：席位的「人格 / 风格提示词」拆成了**独立管理项**，
> 在圆桌页头与「席位管理」「AI 设置」并列，有一个自己的「风格模板」面板。规则：
>
> - **内置 8 套**（稳健配置 / 趋势跟踪 / 风控优先 / 价值投资 / 成长赛道 / 宏观自上而下 /
>   逆向博弈 / 量化纪律），首次访问圆桌接口时自动播种；**可改名、可改正文，但不可删除**。
>   幂等靠 `roundtable_styles.builtin_key`（稳定标识）而不是名称 —— 所以你把内置模板
>   改名后，下次启动**不会**再播种出一份重复的。支持随时新增**自定义模板**。
> - **席位选模板**：席位上的 `style_id` 指向模板；改模板正文 → **所有引用它的席位即时生效**，
>   不必逐个席位改。这是把风格拆出来管理的全部意义。
> - **自定义分支**：`style_id` 为空时用席位自身的 `persona`（历史数据与「不套用模板」的席位）。
>   席位的 `persona` 字段保留不清空 —— 切回自定义时原文还在。
> - **生效值的解析只有一处**：`app._resolve_persona(seat, db)`。任何拼提示词的地方都必须走它
>   （手动通道 `GET /api/roundtable/prompt`、自动通道 `POST /api/roundtable/generate` 共用），
>   **不要直接读 `seat.persona`**，否则模板对这个通道就不生效了。
> - **删除保护**：内置模板不能删；仍被席位引用的模板不能删（提示还有几个席位在用），
>   避免席位静默退化成空风格。

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
> - 存档失败**不回滚观点**（观点经 `_upsert_post` 独立提交，只记 warning），圆桌写入口照常返回成功；
> - 接口返回体带 `thread_id`，席位卡片据此出现「**查看存档**」按钮，一键跳到对应主题帖
>   （卡片只显示最新一层观点，论坛帖才留有历次修订，这是该按钮的独特价值）。
>
> ⚠️ **上面那条「不回滚观点」的代价是：存档失败变成静默失败** —— 观点在库里、论坛却没有帖、
> `thread_id` 为空，而写入口仍报成功。所以补了两道：
> - **提示如实**：保存后按返回的 `thread_id` 判断，未同步时提示「已保存到席位，但论坛存档未同步」
>   而不是谎报「并同步到论坛存档」（自动生成路径同理，按 `results[].thread_id` 逐条判断）；
> - **卡片上给补救入口**：`thread_id` 为空时按钮从「查看存档」改为「**补建存档**」，
>   点它调 `POST /api/roundtable/archive`，与自动同步**共用同一个 `_sync_roundtable_forum`**，
>   语义完全一致（幂等：已有存档且内容未变 → 秒返回现有 `thread_id`，不重复写）。
>   这个接口管**单条补救**，下面那个脚本管**历史全量回填**。
>   注意它是唯一会**如实报错**的存档入口（失败返回 500）—— 用户是主动来补的，再静默等于骗第二次。
>
> 历史观点回填（幂等，只补 `thread_id` 为空的观点）：
> ```bash
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/sync_roundtable_forum.py --dry-run
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/sync_roundtable_forum.py
> ```

> **席位卡折叠（2026-10-17 起）**：席位一多，整页观点正文很难扫读，所以每张席位卡都可独立折叠，
> 面板标题另有「全部折叠 / 全部展开」。
>
> - **折叠后只留标题行**：AI 名称、接入方式、AI 角色、`已生成 / 待生成` 状态徽标、以及该卡的操作按钮
>   （复制提示词 / 粘贴反馈 / 生成 / 查看存档 · 补建存档）全部保留 —— 折叠的作用是「一次扫完所有席位的状态」，
>   而不是把它们藏起来。正文与「更新于…」元信息才收起。
> - **状态按席位 id 记在 `localStorage.qw_rt_folded`**，不用下标：生成后刷新、改名后刷新都会重渲染卡片，
>   状态只放 DOM 的话每次都会被重置回全部展开。换设备/清缓存后回到默认的「全部展开」。
> - **点折叠卡上的「生成」会自动把它展开**：否则点完看不到任何结果，像个哑按钮。
>   「生成今日讨论」（全部席位）刻意不展开 —— 那是用户主动选择只看概览，状态徽标已经能反映结果。
> - 箭头是 CSS 三角（不是 `▸`/`▾` 字形），不受字体回退影响，任何 DPI 都清晰，并随主题色走。

**周期结算帖（日 / 周 / 月 / 季 / 半年 / 年）**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/report/settle` | 管理员：按周期生成结算帖并发布到论坛；body `{period, key?}`，返回 `created` / `unchanged` / `skipped` 三态与 `thread_id` |
| POST | `/api/report/daily` | 兼容入口，等价于 `period=day` |
| POST | `/api/report/news-digest` | 管理员：生成当日「市场概要」帖并发布到论坛；body `{date?, dry_run?}`，每天固定一帖、内容有变化才追加更新层 |
| POST | `/api/news/score` | 管理员：立即对未加工资讯跑一遍 AI 结构化加工；body `{limit?}`（默认 40），大批量补跑用 `scripts/news_score.py` |

> **结算 → 论坛（2026-10-17 起）**：把「仓位、收益变化 + 大盘指数」算成一篇帖子发到论坛，
> 方便日后回看。与圆桌存档帖的分工：圆桌存档是**搬运**（席位观点原样落到论坛），
> 结算是**算出来的新内容**；与交易仪表台的分工：仪表台看「此刻持仓」，结算是「较上一期变了多少」。
>
> - **六个周期**：`day` / `week` / `month` / `quarter` / `half` / `year`，各自一个主题帖，
>   标题 `【日报】2026-10-16 · 持仓与收益`、`【周报】2026-W41（10-05 ~ 10-11）· 持仓与收益`、
>   `【月报】2026-09 · 持仓与收益`、`【季报】2026-Q3 · 持仓与收益`、`【半年报】2026-H1 · 持仓与收益`、
>   `【年报】2025 · 持仓与收益`。
> - **周期键（`pkey`）**：`day=2026-10-17` / `week=2026-W42`（ISO 周，周一~周日）/ `month=2026-10` /
>   `quarter=2026-Q4` / `half=2026-H2` / `year=2026`。**键本身即「同周期只认一个帖」的唯一依据**，
>   周报的窗口按 ISO 周算出（`W42 = 2026-10-12 ~ 10-18`），跨年周沿用 ISO 归属年，不会切错。
> - **发的是「最近一个已结束的周期」**：`day` 是特例（当天发即当天语义）；其余五个周期一律取**上一个**
>   走完的周期。这样定时任务即便被误跑、或手动补发时，也绝不会把半截数据当成完整周期发出去。
> - **身份**：`author_type=system`（署名「系统日报」+「自动」徽章），不占用任何 AI 席位，
>   也不计入论坛的「Agent 参与数」；只有管理员能删。
> - **节奏**：定时任务在每交易日 / 每周日 / 每月 1 日的 22:00（北京时间）自动发布 —— 之所以不是收盘就发，
>   是因为持仓 13/16 是场外基金，净值要到 19:00–22:00 才由基金公司陆续披露（QDII 更是 T+1/T+2）。
> - **幂等**：`settle_reports` 表以 `(period, pkey)` 唯一约束锚定主题帖。正文与帖内最后一层一致 →
>   **不写**；有变化 → 在**同一帖追加一层**并标注 `（第 N 次更新 · 北京时间）`。帖子被删后再发布
>   会自动重建。所以定时任务重跑、当天补数后再跑都不会刷屏，同时保留修订痕迹。
>   判重前会先剥掉 `（第 N 次更新 · …）` 前缀再比，否则每跑一次都会多追加一层。
> - **收益口径**：`equity = 已实现盈亏 + 持仓浮盈`，与项目页盈亏曲线**同源**
>   （唯一实现 `app._pnl_curve`，两处共用，避免数字对不上）。买入不计入收益、卖出把浮盈转为已实现，
>   因此「区间变化」**不含期间新增投入的本金**；各标的最新行情日期不一致时基准标「按各标的最新一期行情」。
> - **账户三指标口径唯一**：总市值 / 浮动盈亏 / 累计实现盈亏由 `app._account_totals` 统一计算，
>   交易仪表台 KPI、圆桌快照 `totals`、结算帖正文三处共用同一份实现，数字必然一致。
> - **期末持仓要回放**：历史窗口展示的是**当时的仓位**，不是当前仓位 —— 由 `_holding_initial()`
>   反推建仓起点、`_replay_raw()` 只回放到窗口末。窗口结束后又调过仓也不会把今天的仓位算进昨天的报表。
> - **无持仓且窗口内无操作时不发布**（返回 `skipped`），避免刷空帖。
> - 手动补发 / 重发：仪表台页头「结算周期下拉 + 发布结算到论坛」按钮，或下面的脚本。

> 结算脚本（定时任务与人工共用；`--periods` 选周期、`--key` 补发指定周期、`--no-fetch` 离线调试、`--date` 回溯）：
> ```bash
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/settle_report.py --dry-run --no-fetch
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/settle_report.py                  # 日结（默认），先补行情再发布
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/settle_report.py --periods week,month,quarter,half,year
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/settle_report.py --periods day --date 2026-10-16
> /opt/quant-web/.venv/bin/python /opt/quant-web/scripts/settle_report.py --periods week --key 2026-W40
> ```
> 补数**直接复用网页端 `app.fetch_prices`**（同一函数），窗口口径、覆盖式/合并式写入语义、
> 缩水守卫、`last_fetch_date` 限频全部一致 —— 不另造一套拉取实现；多周期共用**一次**补数。
> 单只失败只记 warn，不阻断结算。脚本用 `fcntl.flock`（默认 `/tmp/quant-settle.lock`，等 300 秒）互斥：
> 每月 1 日若恰逢周一~五，日结与月结任务会在同一分钟启动，靠这把锁避免抢 SQLite 写锁。

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
| GET | `/api/news/feeds/export-opml` | 管理员：导出现有订阅源为 OPML 附件（按分类分组，只含启用的源） |
| POST | `/api/news/fetch` | 管理员：抓取入库；带 `feed_id` 则只抓该源 |
| GET / PUT | `/api/news/settings` | 管理员：中继地址与密钥（密钥只回 `has_key`，永不回显） |

> **OPML 导入**：请求体 `{content, category?, fetch?}`，`content` 是 OPML **原文**
> （前端读文件或粘贴后上传）。刻意**不支持**「把地址交给服务端去取」——本机只有 IPv6
> 出站，而 OPML 所在主机未必在中继白名单里。导入后默认**立即实抓一次**并逐条回报结果：
> OPML 只是一份地址清单，它对「这些源还活不活」一无所知。
>
> **OPML 导出**：`GET /api/news/feeds/export-opml` 返回 OPML 2.0 附件
> （`Content-Disposition: attachment`），按 category 分组、**只含启用的源**
> （停用说明源有问题，导出去再导入只会得到一批待体检的僵尸源）。与导入对称，
> 换机 / 备份 / 分享订阅清单用；导出文件可直接被本接口的导入端解析（闭环已验证）。

> **源状态三档**：`ok` 正常 / `empty` **空源**（可访问但 0 条目）/ `error` 抓取失败。
> 「空源」必须单列一档：HTTP 200 却不含任何 `<item>` 的僵尸源并不罕见
> （典型样本：新浪财经 RSS，`rss.sina.com.cn/*` 全部源自 2018-09-23 起冻结，只剩一个空壳），
> 记成「成功」会让人以为一切正常。

> ⚠️ **新增订阅源前，先确认域名在中继白名单里** —— `rss-relay/wrangler.toml` 的
> `ALLOW_HOSTS`（逗号分隔、按域名后缀匹配，写 `wallstreetcn.com` 即放行其子域），
> 改完须 `wrangler deploy` 才生效，否则取回的是 `403 host not allowed`。
> 白名单只解决「本机连不上」，**不解决「源是空的」**——两者是独立的坑。
>
> 当前白名单（2026-10-18）：`aihot.news`、`chinanews.com.cn`、`wallstreetcn.com`、
> `jiemian.com`、`eastmoney.com`（5 个资讯源，均 IPv4-only）+ `qbitai.com`（量子位，AI 第 2 源，
> 已加入 `news_feeds`）+ `api.deepseek.com`/`api.moonshot.cn`（LLM POST 通道）+ `generativelanguage.googleapis.com`
> （LLM POST 通道基础设施；Gemini 对中国 Key 返 400 地域拒，经中继亦不绕过，仅作预留）。
> ⚠️ 死候选已剔除并记入 `rss-relay/wrangler.toml` 注释：财联社/机器之心/36氪 官方 RSS 失效或 403、
> 人民日报时政僵尸源(2025)、RSSHub 公共实例 403、Hacker News 英文不纳入、澎湃 anyfeeder 镜像陈旧(全 10-09)。
> 其中华尔街见闻的 RSS 末尾被其 CDN 追加了一段统计 `<script>`（源站把 Content-Type 标成
> `text/html` 触发 Cloudflare HTML 改写），严格 XML 解析会抛 `junk after document element`；
> `newsfetch._xml_root` 对这一种错法做了截断容错。

**资讯 AI 加工层（2026-10-17）——一份数据，两个投影**

资讯不再是「只给人看的页面」：`newsai.py` 定时把条目送 LLM 结构化加工，回写
`ai_score`（重要度 1-5）/ `ai_reason`（一句话理由）/ `ai_tags`（标签）/
`ai_codes`（关联标的代码，已归一化）/ `ai_dup_of`（同题主条目 id，多源报道合并）。
加工结果三处消费，**同一份数据、不另算**：

| 消费方 | 用什么 | 出口 |
| --- | --- | --- |
| 资讯页（人读） | 评分徽标 / 推荐理由 / 标签 / 「持仓相关」徽标 / 「只看相关」筛选 | 页面（`item_out` 直接下发，`related` 由后端算：① `ai_codes ∩ 持仓代码` ② 行业画像关键词命中） |
| 圆桌席位（agent 读） | `select_news_for_prompt` 选条 → 提示词「近期相关资讯」节 | `_snapshot_prompt_text`（自动/手动两个通道都带） |
| 论坛（编辑台） | `build_digest` 从当日高分资讯生成正文 | `_publish_news_digest` → 「市场概要」主题帖（`author_type=news`） |

> **选条规则**：近 72h 窗口、重要度 ≥ 3、同题副本跳过；**持仓相关排最前并标 ★**，
> 其余按重要度补市场级大事，合计最多 10 条。**市场级保底 3 条**（候选不够就有多少给
> 多少）——持仓偏科技时行业新闻能占满 10 个注入位，没这条规则大盘大事一条都进不去
> （2026-10-17 实测：72h 候选池 128 条 = ★85 + 市场级 43，旧行为选出的 10 条全是 ★）。
> `news_inject_log` 记录每日注入过哪些条目：
> **上次注入是更早日期才排除**（同日重跑/试跑仍带同一批），避免一个政策连读三天。
>
> **「建议须引资讯依据」是硬约束**：任务规格要求逐条持仓建议引用资讯条目编号，无依据标
> 「纯技术面判断」——否则资讯塞进提示词也会被模型当背景噪音忽略。
> 另开「新建标的观察」节：agent 可指出新方向，但只描述方向与理由，**不构成建仓建议**。
>
> **LLM 出站自动选路**：加工用的服务商读 `Setting → news.llm_provider`（默认 `deepseek`），
> 配置走 AI 设置里同一套 `llm.*`；`app._llm_route` 探测 base_url 不可达（无 AAAA）时
> **自动改走 rss-relay 的 POST 通道**（白名单已含 `api.deepseek.com`），调用方无感。
> 2026-10-17 实测：VPS 上 274 条 backlog 经中继一次跑通（10 块 0 失败）。
>
> **持仓行业画像（`fund_profiles.json`）**：新闻几乎不出现场外基金代码，`ai_codes`
> 精确匹配对基金恒不命中。用一份「标的 → 行业 + 关键词」画像把「持仓相关」扩展到
> 关键词层：新闻标题/摘要/标签命中某持仓的行业词即算相关。**只收录当前持仓**；
> 宽基（沪深300/A500）刻意只收指数全名，不收「A股/大盘」这类泛词（市场级噪音由
> 选条的「市场大事补位」覆盖，不占 ★）。文件与代码同目录、随部署同步，改完即生效
> （按 mtime 缓存，无需重启）；文件缺失/损坏时自动降级为纯代码匹配。
> **2026-10-17 已按实盘校准一轮**：删泛词（260108 的「消费」）、裸「加息/降息」收紧为
> 「美联储加息/降息 / 美国加息/降息」（否则央行降息会误伤两只 QDII 债基）。
>
> **市场概要的五处加深**（2026-10-17，动机「三段式资讯复读对复杂金融市场太单薄」，
> 旧版实测仅 755 字）：① **数据速览节**——指数收盘涨跌 / 账户三指标 / 持仓今日涨跌
> （涨跌家数+领涨领跌）/ 当日入库条数均分，确定性数据纯字符串拼装、提示词禁模型
> 改写，快照取数失败优雅降级；② **昨日关注复盘**——昨日「明日关注」逐条对 today
> （兑现/落空/进行中+依据），概要从孤品日报变连续剧（正文走 NewsDigest.thread_id →
> ForumPost 最后一层，剥更新层前缀解析；首次运行自动跳过）；③ **时点语境**——写明
> 「A 股收盘后、美股开盘前」，美股内容须注「隔夜」；④ 素材 6→10 条且持仓相关优先，
> 规格加严（逐持仓方向+理由+置信度、明日关注带跟踪指标、总长 1000-1400 字）；
> ⑤ **同 Key 二次审阅**（交叉验证，不依赖第二个 provider）——六项校对（过度推断/
> 时点/遗漏/持仓覆盖/数字一致/复盘完整），调用失败一律降级原稿，**审阅是增强不是闸门**。

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
| `0 14 * * 1-5` | `/opt/quant-web/.venv/bin/python /opt/quant-web/scripts/settle_report.py --periods day` | 每**交易日 22:00**（北京时间）自动发布「日报」到论坛。先补最新行情（复用 `app.fetch_prices`），再生成并发布；同日固定一帖、内容没变不重写、无持仓且无操作则跳过。 |
| `0 14 * * 0` | `/opt/quant-web/.venv/bin/python /opt/quant-web/scripts/settle_report.py --periods week` | 每**周日 22:00**（北京时间）发布「周报」，结算**上一个已走完的 ISO 周**。 |
| `0 14 1 * *` | `/opt/quant-web/.venv/bin/python /opt/quant-web/scripts/settle_report.py --periods month,quarter,half,year` | 每**月 1 日 22:00**（北京时间）发布「月报 / 季报 / 半年报 / 年报」，各自结算上一个已走完的周期；非季末/半年末/年末月份只是判重后不重复写。四条任务共写日志 `/var/log/quant-settle.log`。 |
| `30 13 * * 1-5` | `/usr/bin/python3 /opt/quant-web/scripts/fetch_fund_nav.py` | 交易日收盘后自动补**场外基金**净值。窗口 365 自然日、与主系统 `fund` 口径一致；复用 `datasource.fetch_hist`（不再自带第二套取数实现）；**合并写入**不删历史，并回写 `projects.last_fetch_date` 以免网页端当天重复拉取。日志 `/var/log/quant-fundnav.log`。 |
| `50 12 * * *` | `/opt/quant-web/.venv/bin/python /opt/quant-web/scripts/news_score.py --limit 80` | **每日 20:50**（北京时间）资讯 AI 结构化加工：把未评分条目批量送 LLM（30 条/块），回写重要度 / 理由 / 标签 / 关联标的 / 同题合并。日志 `/var/log/quant-news-score.log`。 |
| `20 13 * * *` | `/opt/quant-web/.venv/bin/python /opt/quant-web/scripts/news_digest.py` | **每日 21:20**（北京时间）发布「市场概要」帖到论坛（昨日复盘 / 今日大事 / 数据看点 / 持仓影响 / 明日关注）。必须晚于评分任务；两者共用 `/tmp/quant-news.lock` 串行，并错开 21:30 的基金补数。日志 `/var/log/quant-news-digest.log`。 |
| `0 4 * * *` / `30 12 * * *` | `/opt/quant-web/.venv/bin/python /opt/quant-web/scripts/news_fetch.py` | **每日 12:00 / 20:30**（北京时间）定时抓取订阅源入库（早晚各一次）。晚间档早于 20:50 评分 20 分钟、错开 21:30 基金补数；不调 LLM 无峰谷问题。抓取原先只能靠管理员开页面点「立即抓取」——资讯→Agent 注入链路要无人值守运转就不能依赖人工。与评分/概要共用 `/tmp/quant-news.lock`。日志 `/var/log/quant-news-fetch.log`。 |
| `0 12 * * *` | `/opt/quant-web/scripts/db_backup.sh` | 线上库热备（`sqlite3 .backup` + `integrity_check`）。日志 `/var/log/quant-db-backup.log`。 |
| `10 14 * * *` | `/opt/quant-web/.venv/bin/python /opt/quant-web/scripts/rt_review.py` | **每日 22:10**（北京时间）观点复盘（G3）：复盘昨日观点（LLM 抽取「标的+方向」→ 机械比对真实行情判 T+1/T+5）+ 补算近 5 个交易日未满窗记录。抽取用资讯 AI 的 LLM 配置（`news_llm_conf`），判定机械、不依赖模型自评。日志 `/var/log/quant-rt-review.log`。 |

> **观点复盘（G3，2026-10-17 起）** —— 圆桌「建议 → 结果」的最后一环，闭环资讯侧的命中率：
>
> - **数据层**：`RtReview` 表（每日观点经 LLM 抽取成 `{标的, 方向, 依据资讯编号, 置信度}` 结构化判断，再机械比对 `MarketPrice` 真实行情得出 `ret_t1/ret_t5` 与 `verdict_t1/verdict_t5`）。
> - **判定纪律（不可妥协）**：LLM 只做「文本 → 结构化」抽取，**判定全部机械**（方向 × 真实收益）。让模型事后自评「对错」等于再编一个故事，违背「可解释、人在回路」底线。
> - **口径**：基准日 = 观点日期起首个有行情日；T+N = 基准后第 N 个交易日（与策略外推 5 交易日同尺度）。`|收益| < 0.5%` 判无效波动（不计命中率分母）；`hold/observe` 无法用收益判对错 → 一律 `flat`。基金净值当晚/次日晚才入库，未到期一律 `pending`，由每日任务渐进补算。
> - **依据溯源**：只追溯「自动生成」且观点明确写「资讯 N」的判断；经 `NewsInjectLog.pos` 映射成真实条目 id（`manual` 帖从外部 AI 复制、编号体系不同，恒不溯源、如实留空）。
> - **可视化**：圆桌页新增第五个可折叠面板「观点复盘」（席位命中率表 / 引用资讯信息量表 / 样本明细 + ECharts 逐日命中率趋势）；席位卡在有效样本 ≥ 3 时显示「命中 X%」徽标。
> - **补批 / 手动**：首次上线用 `rt_review.py --backfill 14` 补批历史；管理员可在页内「AI 设置」相邻入口（实为 `POST /api/roundtable/review`）指定 `date` 复盘单日或 `days` 补批。

> ⚠️ **crontab 表达式的时区是服务器时区 `Etc/UTC`**（不是北京时间）：`30 13 * * 1-5` = 北京 21:30、
> `0 14 * * 1-5` = 北京 22:00。写定时任务时用 `date -u` 对齐，别按本机时间直接填。

> 💰 **调 LLM 的定时任务一律排在 DeepSeek 闲时**：官方峰谷定价（2026-08-17 起生效）高峰时段为
> 北京时间 9:00–12:00 / 14:00–18:00，**其余全部闲时、价格减半**。现有两个资讯任务（20:50 / 21:20）
> 与结算帖（22:00）都天然落在闲时；今后新增任何调 LLM 的定时任务，先对照高峰窗口再定时。

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
