# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev

FROM mcr.microsoft.com/playwright:v1.54.1-noble

# Playwright disables Chromium's sandbox by default in containers. Running the
# renderer as its built-in non-root user still limits the process on every host.
USER pwuser

WORKDIR /app
COPY --from=dependencies --chown=pwuser:pwuser /app/node_modules ./node_modules
COPY --chown=pwuser:pwuser server.mjs ./server.mjs

ENV PORT=3000 \
    RENDER_ALLOWED_HOSTS=api \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

CMD ["node", "server.mjs"]
