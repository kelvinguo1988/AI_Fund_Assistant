# ============================================================================
# 基金量化交易系统 — 前端 Dockerfile (多阶段构建)
# 阶段1: Node 构建
# 阶段2: Nginx 生产服务
# ============================================================================

# ── 阶段1: 构建前端 ────────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

# 先复制 package*.json 利用 Docker 缓存层
COPY package.json ./

# 安装依赖
RUN npm install

# 复制源代码
COPY . .

# 构建生产版本
RUN npm run build

# ── 阶段2: Nginx 生产服务 ─────────────────────────────────────────────
FROM nginx:alpine AS serve

# 移除默认 nginx 配置
RUN rm /etc/nginx/conf.d/default.conf

# 复制自定义 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 从构建阶段复制产物到 nginx 默认静态文件目录
COPY --from=build /app/dist /usr/share/nginx/html

# 添加启动前等待脚本（QNAP Container Station 兼容）
COPY docker-entrypoint.d/ /docker-entrypoint.d/

# 暴露 HTTP 端口
EXPOSE 80

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost/ || exit 1

# 使用 nginx 默认前台启动
CMD ["nginx", "-g", "daemon off;"]
