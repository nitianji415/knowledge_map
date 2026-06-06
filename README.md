<div align="center">

<img src="docs/logo.png" alt="TJ Sylva · 知识之林" width="200" />

# TJ Sylva · 知识之林

**左侧 AI 对话、右侧自动生长的结构化知识树**

简体中文 | [English](README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![CI](https://github.com/nitianji415/TJ-Sylva/actions/workflows/ci.yml/badge.svg)](https://github.com/nitianji415/TJ-Sylva/actions/workflows/ci.yml)
[![Bilibili 演示视频](https://img.shields.io/badge/Bilibili-演示视频-FB7299?logo=bilibili&logoColor=white)](https://www.bilibili.com/video/BV1GcGX6iEUG/)

📺 **[点此观看系统演示视频(Bilibili)](https://www.bilibili.com/video/BV1GcGX6iEUG/)**

</div>

> 带着具体问题进场，AI 先画地图、再当教练带你逐步深入、跳过、回顾，最后沉淀成一棵结构化知识树。

后端 FastAPI + SQLAlchemy + Alembic + Postgres，前端原生 HTML/CSS/JS。LLM 走单一 OpenAI 兼容协议（DeepSeek / Moonshot / OpenRouter / 自建 vLLM …… 改 base_url 就能换）。

> **⚠️ 安全提醒**:生产部署前务必修改 `SETTINGS_SECRET` / `JWT_SECRET` / `ADMIN_PASSWORD`,且**切勿提交真实数据库或 `.env`**。详见 [安全](#安全) 一节。

## ✨ 功能一览

<table>
<tr>
<td width="50%" valign="top">

**🗺️ 对话即地图**

左侧和 AI 教练对话,右侧自动生长出一棵结构化知识树,边学边沉淀。

<img src="docs/screenshots/main.png" alt="主界面:左侧对话 + 右侧知识树" />

</td>
<td width="50%" valign="top">

**🌐 划词联网搜索**

对任意词句划词,一键触发联网搜索,返回带来源出处的结果卡片。

<img src="docs/screenshots/web-search.png" alt="划词联网搜索" />

</td>
</tr>
<tr>
<td width="50%" valign="top">

**🔎 划词速览解释**

划词即时弹出速览解释,看不懂的地方随手就能问,不打断主线学习。

<img src="docs/screenshots/peek.png" alt="划词速览解释" />

</td>
<td width="50%" valign="top">

**🎯 节点定位对话**

点击知识树上任意节点,一键把对话定位到该节点、围绕它继续讲解。

<img src="docs/screenshots/locate.png" alt="节点定位对话" />

</td>
</tr>
</table>

## 一键 Docker 部署（推荐）

```bash
git clone https://github.com/nitianji415/TJ-Sylva.git
cd TJ-Sylva
docker compose up -d          # .env 可选,不填也能起(需 Docker Compose v2.24+)
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765),用 `admin / admin` 登录,在右上角「设置」面板填入 LLM key 即可开始用。

**正式部署**请先配置 `.env`:

```bash
cp .env.example .env
# 编辑 .env:
#   - 填 LLM_API_KEY (默认指向 DeepSeek)
#   - 生产务必改 ADMIN_PASSWORD / JWT_SECRET / SETTINGS_SECRET(见「安全」)
docker compose up -d
```

首次登录会提示改默认密码 —— 强烈建议立刻改。LLM key 也可以登录后从右上角「设置」面板配置（写到 DB 加密存储，不依赖 .env）。

首次启动会拉镜像 + 编 open-webSearch（带 native 模块，3-5 分钟）。后续 `docker compose up -d` 秒起。

三个 service：
- **app** — FastAPI 后端 + 静态前端，监听 `8765`
- **postgres** — 16-alpine，数据持久化
- **open-websearch** — 本地免费网页搜索 daemon（sogou + duckduckgo），从上游 `Aas-ee/open-webSearch` v2.1.11 构建

常用命令：
```bash
docker compose logs -f app          # 看后端日志
docker compose logs -f open-websearch
docker compose down                 # 停掉,保留数据
docker compose down -v              # 停掉并清空 DB (慎用)
```

## 开发模式（不走 Docker）

```bash
python3 app.py       # 启动服务并自动打开网页
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 1. 起 Postgres (或在 .env 里改 DATABASE_URL 切到 SQLite)
docker compose up -d postgres

# 2. (可选) 起 open-webSearch daemon
#    不起的话,SEARCH_PROVIDER 改成 anysearch 或 off
cd external/open-webSearch && npm install && npm run build
cd ../..

# 3. 配置 + 迁移 + 启动
cp .env.example .env  # 填 API key
alembic upgrade head
python3 app.py        # 或 python3 server.py / uvicorn app.main:app --reload
```

默认监听 `http://127.0.0.1:8765`。

## 测试

```bash
pytest                       # SQLite in-memory,禁用外部 LLM
./scripts/smoke_test.sh      # 真起一个 uvicorn 端到端跑核心 API
```

## API

| Method | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（无需登录） |
| POST | `/api/auth/login` | 登录,返回 JWT |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/auth/change-password` | 修改密码 |
| GET | `/api/settings` | 列出运行时配置(admin) |
| PATCH | `/api/settings` | 更新配置(admin) |
| POST | `/api/settings/test` | 用临时 key 试连 LLM |
| GET | `/api/sessions?search=` | 列出最近会话 |
| POST | `/api/sessions` | 新建会话，生成初始知识地图 |
| POST | `/api/sessions/preview-topics` | 预览主干（用户编辑后再确认生成） |
| POST | `/api/sessions/{id}/grow-children` | SSE 流式生长子节点 |
| GET | `/api/sessions/{id}/tree` | 拿当前会话所有节点 |
| GET | `/api/sessions/{id}/messages` | 拿当前会话消息 |
| POST | `/api/sessions/{id}/messages/stream` | SSE 流式回复 |
| PATCH | `/api/nodes/{id}` | 更新节点（含 `collapsed`、状态、三维指标等） |
| POST | `/api/sessions/{sid}/nodes/{nid}/subdivision-options` | 获取拆分角度建议 |
| POST | `/api/sessions/{sid}/nodes/{nid}/multi-angle-subdivide` | 多角度一次性拆分 |
| POST | `/api/messages/{id}/peeks` | 划词速览解释 |
| POST | `/api/messages/{id}/peeks/{pid}/followups` | 速览卡内追问 |

完整字段定义见 [`app/schemas/`](app/schemas/) 和启动后访问 [/docs](http://127.0.0.1:8765/docs)。

## 把旧 SQLite 数据搬到 Postgres

```bash
docker compose up -d postgres
alembic upgrade head
python scripts/migrate_sqlite_to_postgres.py --source data/knowledge_map.sqlite3
```

脚本幂等，已存在的 id 会跳过。

## LLM 配置

全系统走单一 OpenAI Chat Completions 兼容协议。失败时回退本地规则引擎。

```bash
LLM_API_KEY=sk-...                       # 默认 DeepSeek,在 https://platform.deepseek.com 申请
LLM_MODEL=deepseek-chat                  # 也可填 deepseek-reasoner、moonshot-v1-8k 等
LLM_BASE_URL=https://api.deepseek.com/v1 # 换别的 OpenAI 兼容服务把这里改掉
```

## 思维档位

`Lite / Medium / Zen` 三档控制 AI 拆分粒度：

- Lite：一级 6–8 个，深入时 children 4 个
- Medium：一级 8–11 个，深入时 children 7 个
- Zen：一级 10–14 个，深入时 children 12 个

前端顶部档位胶囊里切换，后端读 prompt 后给 LLM。

## 节点折叠（XMind 风格）

每个有子节点的卡片角上有圆形 toggle，点击后整棵子树从画布隐藏并显示 `+N` 徽标。状态通过 `PATCH /api/nodes/{id} {"collapsed": true}` 持久化到服务端。

## 网页检索路由

`SEARCH_PROVIDER` 三选一：
- `open`（默认）— 用本地 open-webSearch daemon，免费，Docker 模式下自动启动
- `anysearch` — AnySearch 聚合搜索（https://api.anysearch.com），填 `ANYSEARCH_API_KEY` 配额更高，失败自动回退到 `open`
- `off` — 完全关闭

## 安全

本项目把敏感配置(LLM API key 等)**加密存进数据库**,加密主密钥来自 `SETTINGS_SECRET`。部署前务必:

- **改掉三个默认密钥/密码**(通过环境变量),否则加密形同虚设:
  - `SETTINGS_SECRET` — 加密 API key 的主密钥,**默认值会让 DB 里的密钥可被任何人解密**
  - `JWT_SECRET` — 登录 token 签名
  - `ADMIN_PASSWORD` — 默认 `admin/admin`,首登会提示改
  - 启动时若仍是默认值,后端会打印醒目警告。
- 生成强随机 secret:`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
- **切勿提交真实数据库**(`data/*.sqlite3`)或 `.env` —— 两者已在 `.gitignore`,里面含用户数据和加密密钥。
- 漏洞上报方式见 [SECURITY.md](SECURITY.md)。

## 参与贡献

欢迎 issue 与 PR,流程见 [CONTRIBUTING.md](CONTRIBUTING.md);参与即视为同意 [行为准则](CODE_OF_CONDUCT.md)。

## 维护入口

- [app/main.py](app/main.py) — FastAPI 应用工厂
- [app/routers/](app/routers/) — 路由层（health / sessions / messages / nodes）
- [app/services/knowledge.py](app/services/knowledge.py) — 会话、消息、节点核心业务流
- [app/services/ai.py](app/services/ai.py) — 统一 LLM 客户端（OpenAI 兼容协议）
- [app/services/topics.py](app/services/topics.py) — LLM 失败时的本地 fallback
- [app/services/web_search.py](app/services/web_search.py) — open-webSearch daemon 客户端
- [app/db/models.py](app/db/models.py) — ORM 表结构
- [alembic/versions/](alembic/versions/) — 数据库迁移版本

任何接口/字段变化都先动 schemas + Alembic，再改 service + router，最后联前端。

## License

[MIT](LICENSE) © 2026 nitianji415

> 网页检索依赖上游 [`Aas-ee/open-webSearch`](https://github.com/Aas-ee/open-webSearch)(Docker 构建时拉取,不随本仓库分发),请遵循其各自许可证。
