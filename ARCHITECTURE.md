# 架构说明

升级到 FastAPI 工程结构后,后端按以下边界分层。每次新增功能优先放在对应层里,不要把业务逻辑写回路由。

## 目录结构

```text
knowledge_map/
├── pyproject.toml                # 依赖与脚本入口
├── alembic.ini                   # 迁移配置
├── alembic/
│   ├── env.py                    # async engine + Base.metadata
│   └── versions/                 # 迁移脚本
├── docker-compose.yml            # 本地 Postgres
├── server.py                     # 兼容入口,真实启动走 uvicorn
├── app/
│   ├── main.py                   # FastAPI 应用工厂、lifespan、static mount
│   ├── core/
│   │   ├── config.py             # Pydantic Settings
│   │   └── sse.py                # SSE 切片与编码
│   ├── db/
│   │   ├── base.py               # AsyncEngine、AsyncSession 工厂
│   │   └── models.py             # ORM 模型
│   ├── schemas/                  # Pydantic 请求/响应
│   ├── services/
│   │   ├── ai.py                 # DeepSeek 异步客户端 + prompt
│   │   ├── topics.py             # 离线 fallback 主题
│   │   └── knowledge.py          # 核心业务服务
│   └── routers/
│       ├── deps.py               # 共享依赖
│       ├── health.py
│       ├── sessions.py
│       ├── messages.py           # SSE 流式接口
│       └── nodes.py
├── static/                       # 前端原生 HTML/CSS/JS
├── tests/                        # pytest + httpx,SQLite in-memory
├── scripts/
│   ├── smoke_test.sh             # 端到端冒烟
│   └── migrate_sqlite_to_postgres.py
└── data/                         # 运行时数据
```

## 分层职责

- **routers** 只关心 HTTP:解析路径、注入依赖、序列化 Pydantic、转 HTTPException。不要在这里写业务逻辑。
- **services** 承载业务流。`KnowledgeMapService` 拿 `AsyncSession` 做一切持久化和 AI 调用,失败回退到 fallback。
- **db/models** 只是 ORM。schema 变更 → 先改 model → `alembic revision --autogenerate` → 手动 review → upgrade。
- **schemas** 是对外契约。改字段时先动这里,然后才改前端。
- **core/config** 用 Pydantic Settings 读 `.env`。新增可配置项加到 `Settings` 类。
- **core/sse** 把 SSE 切片、间隔、事件名收敛在一处。

## 数据库

- 默认 Postgres + asyncpg,迁移用 Alembic
- SQLite + aiosqlite 在测试和本地速跑模式可用,模型保持 dialect 无关
- 主要表:`learning_sessions / knowledge_nodes / messages / node_events`
- `knowledge_nodes.collapsed`:每个节点的 XMind 折叠状态,服务端持久化

## 测试

- `tests/conftest.py` 用 SQLite in-memory 注入独立 session 工厂,禁用 DeepSeek key 走本地 fallback
- 不连出网、不依赖 Postgres、不需要任何 fixture 数据
- 想跑真实 Postgres,设置 `DATABASE_URL` 后跑 `./scripts/smoke_test.sh`

## 添加新功能的标准动作

1. 在 `app/db/models.py` 加字段 → `alembic revision --autogenerate -m "..."`,review 生成结果
2. 在 `app/schemas/` 暴露给外部
3. 在 `app/services/` 写业务,操作 `AsyncSession`
4. 在 `app/routers/` 加路由,只做参数校验与序列化
5. `pytest` 跑回归,再考虑前端联调
