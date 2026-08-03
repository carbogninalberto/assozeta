# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS dependencies

WORKDIR /app

COPY package.json package-lock.json .npmrc ./
RUN --mount=type=cache,target=/root/.npm npm ci

FROM dependencies AS development

COPY . .

EXPOSE 5001

CMD ["npm", "run", "dev:selfhosted", "--", "--host", "0.0.0.0"]

FROM dependencies AS build

ARG OEM_ENV=assozeta
ARG CLIENT_ID=""
ARG APPLE_CLIENT_ID=""

ENV OEM_ENV=$OEM_ENV \
    CLIENT_ID=$CLIENT_ID \
    APPLE_CLIENT_ID=$APPLE_CLIENT_ID \
    NODE_OPTIONS=--max-old-space-size=4096

COPY . .
RUN npm run css:vendor-bootstrap && npm run build:vite:production

FROM caddy:2.9.1-alpine AS production

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist/public /srv

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
