# Assozeta PDF renderer

This service is a PDF-only adaptation of the browser-pool approach used by
`carbogninalberto/fast-html2pdfapi`. It deliberately keeps only the HTTP
contract, Playwright Chromium, and request isolation required by Assozeta.
Screenshots, images, video/FFmpeg, HTML capture, API documentation, and their
dependencies are not included.

## Runtime design

- `POST /render` accepts Assozeta's existing JSON payload and returns PDF bytes.
- `GET /healthz` opens and closes a page, matching Compose and diagnostics.
- Browser processes are bounded by `RENDER_POOL_SIZE` and requests wait for a
  bounded acquisition timeout. Every render receives a fresh browser context;
  cookies, storage, caches that matter to a context, and service workers do not
  cross requests.
- Browsers are replaced after `RENDER_BROWSER_MAX_AGE_MS`,
  `RENDER_BROWSER_MAX_REQUESTS`, disconnection, or a failed render.
- Host filtering is applied to navigation, redirects, and every browser
  subrequest. Authentication headers are added only to the initial navigation
  and same-origin redirect chain.
- The image runs as the non-root `node` user. The production Compose definition
  makes its filesystem read-only apart from `/tmp`, drops all capabilities, and
  sets a process limit.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `RENDER_ALLOWED_HOSTS` | `api` | Comma-separated exact hostname allowlist |
| `RENDER_POOL_SIZE` | `2` | Maximum concurrent browser processes |
| `RENDER_POOL_ACQUIRE_TIMEOUT_MS` | `30000` | Maximum wait for a browser slot |
| `RENDER_BROWSER_MAX_AGE_MS` | `600000` | Browser lifetime before recycling |
| `RENDER_BROWSER_MAX_REQUESTS` | `100` | Renders before browser recycling |
| `RENDER_MEMORY_LIMIT_MB` | container cgroup limit | Optional memory-pressure override |
| `PORT` | `3000` | HTTP listen port |

`RENDER_BROWSER_MAX_MEMORY_MB`-style checks based on
`process.memoryUsage().heapUsed` are intentionally absent: that value excludes
Chromium processes. This renderer reads `memory.current`/`memory.max` (or their
cgroup v1 equivalents), which covers the service container and its browsers.
Recycling remains based on age and request count because a shared cgroup value
cannot safely identify one browser's contribution.

## Verification

Run the pool tests and Docker contract test:

```sh
npm --prefix selfhost/renderer ci --omit=dev --ignore-scripts
npm --prefix selfhost/renderer test
make selfhost-renderer-test
DOCKER_CONTEXT=desktop-linux \
  node selfhost/tests/renderer-benchmark.mjs BASE_IMAGE OPTIMIZED_IMAGE
```

The contract test uses synthetic invoice, receipt, subscription, and multipage
documents with images, a signature, multiple fonts, host restrictions,
redirects, external assets, state isolation, service-worker blocking,
concurrency, exhaustion, timeouts, HTTP failures, and browser-crash recovery.
The benchmark uses the same authenticated Assozeta-style request, reports
startup, sequential percentiles, concurrent throughput, container cgroup
memory, and image size, and can save generated PDFs when
`RENDER_BENCHMARK_OUTPUT_DIR` is set.
