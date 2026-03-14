ARG PNPM_STORE_PATH=/root/.local/share/pnpm/store

FROM node:22-alpine AS base
ARG PNPM_STORE_PATH
RUN corepack enable && corepack install -g pnpm@10.31.0
RUN pnpm config set store-dir ${PNPM_STORE_PATH}
WORKDIR /app
COPY frontend-v2 ./frontend-v2

FROM base AS dev
ENV CI=true
RUN cd /app/frontend-v2 && pnpm install --frozen-lockfile
EXPOSE 5173
CMD ["sh", "-c", "cd /app/frontend-v2 && pnpm dev"]
