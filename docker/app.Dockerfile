# AI 知识地图 - FastAPI 后端镜像
#
# 设计:
#   - python:3.11-slim 基础(比 alpine 兼容性好,asyncpg 不踩坑)
#   - 两段式 build:第一段装 deps,第二段只带 site-packages + 源码
#   - entrypoint 会先跑 alembic upgrade head 再起 uvicorn
#   - 不打包 .env / data/:都通过 compose 的 volume + env 注入

FROM python:3.11-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# build-essential 留着,asyncpg 在某些 arch 上会编 native
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
# 只装 deps,不装项目本身——构建阶段还没拷源码,setuptools 包发现会失败。
# pyproject 的 [project].dependencies 抽出来当 requirements.txt 用。
RUN pip install --upgrade pip && \
    python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/req.txt && \
    pip install --prefix=/install -r /tmp/req.txt

# ----------------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    KNOWLEDGE_MAP_HOST=0.0.0.0 \
    KNOWLEDGE_MAP_PORT=8765 \
    OPEN_WEBSEARCH_AUTOSTART=false

WORKDIR /app

# tini 做 PID 1,让 ctrl-c / docker stop 能干净退出
RUN apt-get update && \
    apt-get install -y --no-install-recommends tini && \
    rm -rf /var/lib/apt/lists/*

COPY --from=build /install /usr/local

# 源码
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY server.py ./
COPY static ./static
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh && \
    mkdir -p /app/data && \
    useradd --uid 1001 --user-group --create-home appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8765

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
