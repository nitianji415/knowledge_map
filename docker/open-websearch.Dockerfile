# 本镜像构建 open-webSearch HTTP daemon,供主 app 通过 SEARCH_PROVIDER=open 调用。
# 源码从上游 GitHub 在 build 阶段 clone,无需 submodule,也无需 external/ 目录。
#
# 上游: https://github.com/Aas-ee/open-webSearch
# 固定版本: v2.1.11 (commit 3094fa5),如需升级修改下面 OPEN_WEBSEARCH_REF。

FROM node:20-alpine AS build

ARG OPEN_WEBSEARCH_REPO=https://github.com/Aas-ee/open-webSearch.git
ARG OPEN_WEBSEARCH_REF=v2.1.11

WORKDIR /app

# linux/arm64 Alpine 上 koffi 没预编译包,需要临时工具链从源码编 native 模块
RUN apk add --no-cache git libstdc++ && \
    apk add --no-cache --virtual .native-build-deps python3 make g++ cmake && \
    git clone --depth 1 --branch "${OPEN_WEBSEARCH_REF}" "${OPEN_WEBSEARCH_REPO}" . && \
    (npm ci || npm install) && \
    npm run build && \
    npm cache clean --force && \
    apk del .native-build-deps

# ----------------------------------------------------------------------
# Runtime: 只带 build 产物和 node_modules,不带 git 和工具链
FROM node:20-alpine

WORKDIR /app
RUN apk add --no-cache libstdc++

COPY --from=build /app/build /app/build
COPY --from=build /app/node_modules /app/node_modules
COPY --from=build /app/package.json /app/package.json

RUN addgroup -g 1001 -S nodejs && \
    adduser -S nodejs -u 1001 && \
    chown -R nodejs:nodejs /app
USER nodejs

ENV NODE_ENV=production
# serve 子命令默认绑 127.0.0.1,容器化时必须改成 0.0.0.0 否则别的容器进不来
ENV OPEN_WEBSEARCH_DAEMON_HOST=0.0.0.0
ENV OPEN_WEBSEARCH_DAEMON_PORT=3210

EXPOSE 3210

# serve 子命令启 HTTP daemon
CMD ["node", "build/index.js", "serve"]
