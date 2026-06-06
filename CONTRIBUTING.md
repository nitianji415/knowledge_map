# 贡献指南 / Contributing

感谢你对 TJ Sylva (知识之林) 的关注!欢迎 issue、PR 和讨论。

## 开发环境

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 起本地依赖(Postgres;也可在 .env 把 DATABASE_URL 切到 SQLite)
docker compose up -d postgres

# 跑迁移 + 启动
python3 app.py
```

详细配置见 [README.md](README.md)。所有密钥走 `.env`(参考 `.env.example`),**切勿把真实 key 或数据库提交进仓库**。

## 提交前请确保

```bash
ruff check .          # 代码风格 / lint
ruff format .         # 自动格式化
pytest -q             # 全部测试通过
```

- 新功能/修复请尽量带测试(`tests/` 下,pytest + asyncio)。
- 提交信息用祈使句,说清「做了什么、为什么」。
- 改动 LLM 调用、prompt、上下文拼装时,留意 token 成本与缓存命中。

## Pull Request

1. Fork & 新建分支(`feat/xxx`、`fix/xxx`)。
2. 保持改动聚焦,一个 PR 做一件事。
3. PR 描述里说明动机、做法、测试方式。
4. CI(ruff + pytest)需通过。

## 报告问题

- Bug / 功能建议:走 GitHub Issues(有模板)。
- **安全漏洞请勿公开提 issue**,见 [SECURITY.md](SECURITY.md)。

## 行为准则

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
