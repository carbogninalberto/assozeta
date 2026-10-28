# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PORT=3000 \
    RENDER_ALLOWED_HOSTS=api \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TMPDIR=/tmp

WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY package.json package-lock.json ./

# Keep the browser and its fonts pinned. The dependency installation includes
# the Playwright font set needed by Italian and international documents.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    node node_modules/playwright-core/cli.js install --with-deps --only-shell chromium \
    && rm -rf /ms-playwright/ffmpeg-* /root/.npm \
    && chmod -R a+rX /ms-playwright

COPY fontconfig.conf /etc/fonts/conf.d/60-assozeta-fonts.conf
RUN fc-cache -f \
    && rm -rf /usr/local/lib/node_modules/npm \
        /usr/local/lib/node_modules/corepack \
        /opt/yarn-* \
        /usr/local/bin/npm \
        /usr/local/bin/npx \
        /usr/local/bin/corepack \
        /usr/local/bin/yarn \
        /usr/local/bin/yarnpkg

COPY --chown=node:node browser-pool.mjs server.mjs ./

# The headless browser is the largest memory consumer. The pool reads cgroup
# usage (not only the Node.js heap) and recycles browsers by age and request.
USER node

EXPOSE 3000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

CMD ["node", "server.mjs"]
