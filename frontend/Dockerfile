# =============================================================================
# CodeRadar Frontend — 多阶段构建
# 阶段 1 : 用 Node 编译 Vue 3 应用
# 阶段 2 : 用 nginx:alpine 提供静态文件 + 反向代理 API
# =============================================================================

# ---- 阶段 1: builder ----
FROM node:20-alpine AS builder

WORKDIR /build

# 先复制依赖文件，利用 Docker 缓存层
COPY package.json package-lock.json ./
RUN npm ci

# 复制源码并构建
COPY tsconfig.json vite.config.ts index.html ./
COPY src/ src/
RUN npm run build

# ---- 阶段 2: production ----
FROM nginx:alpine

# 关闭 nginx 版本 banner（安全加固）
RUN echo "server_tokens off;" > /etc/nginx/conf.d/server_tokens.conf

# 复制构建产物
COPY --from=builder /build/dist/ /usr/share/nginx/html/

# 复制自定义 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
