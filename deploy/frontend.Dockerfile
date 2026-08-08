ARG FRONTEND_BASE_IMAGE=nginx:1.29-alpine

FROM node:22-alpine AS builder

WORKDIR /app/frontend

RUN corepack enable

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend ./
RUN pnpm build

FROM ${FRONTEND_BASE_IMAGE}

COPY deploy/frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/frontend/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
