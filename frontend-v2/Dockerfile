ARG PNPM_STORE_PATH=/root/.local/share/pnpm/store

FROM node:22-alpine AS base
ARG PNPM_STORE_PATH
RUN corepack enable && corepack install -g pnpm@10.31.0
RUN pnpm config set store-dir ${PNPM_STORE_PATH}
WORKDIR /app
COPY frontend-v2 ./frontend-v2

FROM base AS dev
ENV CI=true
ENV CHOKIDAR_USEPOLLING=true
ENV CHOKIDAR_INTERVAL=250
RUN cd /app/frontend-v2 && pnpm install --frozen-lockfile
EXPOSE 5173
CMD ["sh", "-c", "cd /app/frontend-v2 && pnpm dev"]

FROM base AS build
ARG VITE_APP_BASE_PATH=/v2/
ENV CI=true
ENV VITE_APP_BASE_PATH=${VITE_APP_BASE_PATH}
RUN cd /app/frontend-v2 && pnpm install --frozen-lockfile && pnpm build

FROM nginx:alpine AS release
COPY frontend-v2/nginx.release.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend-v2/dist /usr/share/nginx/html/v2
EXPOSE 4173
