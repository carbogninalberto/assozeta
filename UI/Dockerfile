# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS dependencies

WORKDIR /app

COPY package.json package-lock.json .npmrc ./
RUN --mount=type=cache,target=/root/.npm npm ci \
    && sha256sum package-lock.json | cut -d' ' -f1 > node_modules/.assozeta-package-lock.sha256

FROM dependencies AS development

COPY . .

EXPOSE 5001

CMD ["sh", "scripts/dev-container.sh"]

FROM dependencies AS build

ARG VERSION=unstable
ARG OEM_ENV=assozeta
ARG CLIENT_ID=""
ARG APPLE_CLIENT_ID=""

ENV VERSION=$VERSION \
    OEM_ENV=$OEM_ENV \
    CLIENT_ID=$CLIENT_ID \
    APPLE_CLIENT_ID=$APPLE_CLIENT_ID \
    NODE_OPTIONS=--max-old-space-size=4096

COPY . .
RUN npm run css:vendor-bootstrap && npm run build:vite:production

FROM caddy:2.11.7-alpine AS production

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist/public /srv

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
