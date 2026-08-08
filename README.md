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
| 🔄 行情限频拉取 | 游客模式每天仅实际调用一次数据源；当日数据已存在则提示无需重复获取。 |

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
├── deploy.sh         # 一键同步到 VPS 并重启服务
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

---

## 七、配置说明

| 配置项 | 位置 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 游客标的代码 | `app.py` → `GUEST_STOCK_CODE` | `000002.SZ` | 游客模式公开只读的标的。 |
| 服务端口 | `uvicorn` 启动参数 / `systemd` | `8090` | 可按需修改。 |
| 数据库路径 | `db.py` → `DB_PATH` | `./quantweb.db` | 独立 SQLite 文件。 |
| 行情回退顺序 | `datasource.py` | 腾讯 → 新浪 → akshare → 东方财富 | 单源失败自动切换。 |
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
| GET | `/api/guest/projects/{pid}/refresh` | 游客行情限频刷新（每日一次） |

---

## 九、部署（IPv6 VPS + systemd）

项目已具备一键部署脚本 `deploy.sh`（本地终端运行，通过 `ssh -6` 直连 VPS）：

```bash
# 1. 编辑 deploy.sh，确认 LOCAL_DIR / VPS / REMOTE_DIR 正确
# 2. 执行部署（保留远端 .venv 与 quantweb.db，仅同步源码）
bash deploy.sh
```

脚本流程：

1. `rsync` 同步源码到 VPS（排除 `.venv` / `quantweb.db` / `__pycache__`）；
2. 远端安装 / 更新 `akshare`；
3. `systemctl restart quant-web` 重启服务并校验 `health`。

> 静态文件（HTML / JS / CSS）免重启，改完即生效；后端改动需 `systemctl restart quant-web`。
> 资源版本号以 `index.html` 中 `?v=N` 控制，部署后建议浏览器**硬刷新**（Cmd/Ctrl + Shift + R）。

### nginx 反向代理（服务默认仅监听 127.0.0.1:8090）

```nginx
server {
    listen 80;                      # 或 443 ssl（配证书）
    server_name 你的域名或IP;       # 如 2404:8c80:82:1057::47 或域名

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
