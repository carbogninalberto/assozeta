import http from 'node:http';
import {chromium} from 'playwright-core';
import {BrowserPool, PoolUnavailableError} from './browser-pool.mjs';

function positiveInt(value, fallback, label) {
    if (value == null || value === '') return fallback;
    const parsed = Number(value);
    if (!Number.isInteger(parsed) || parsed <= 0) {
        throw new Error(`${label} must be a positive integer`);
    }
    return parsed;
}

const port = positiveInt(process.env.PORT, 3000, 'PORT');
const poolSize = positiveInt(process.env.RENDER_POOL_SIZE, 2, 'RENDER_POOL_SIZE');
const acquireTimeoutMs = positiveInt(
    process.env.RENDER_POOL_ACQUIRE_TIMEOUT_MS,
    30000,
    'RENDER_POOL_ACQUIRE_TIMEOUT_MS',
);
const browserMaxAgeMs = positiveInt(
    process.env.RENDER_BROWSER_MAX_AGE_MS,
    10 * 60 * 1000,
    'RENDER_BROWSER_MAX_AGE_MS',
);
const browserMaxRequests = positiveInt(
    process.env.RENDER_BROWSER_MAX_REQUESTS,
    100,
    'RENDER_BROWSER_MAX_REQUESTS',
);
const memoryLimitBytes = process.env.RENDER_MEMORY_LIMIT_MB
    ? positiveInt(process.env.RENDER_MEMORY_LIMIT_MB, 0, 'RENDER_MEMORY_LIMIT_MB') * 1024 * 1024
    : null;
const allowedHosts = new Set(
    (process.env.RENDER_ALLOWED_HOSTS || 'api')
        .split(',')
        .map(host => host.trim().toLowerCase())
        .filter(Boolean),
);

const browserPool = new BrowserPool({
    launchBrowser: () => chromium.launch({
        headless: true,
        // Playwright disables the Chromium sandbox in containers. The service
        // still runs as node with no-new-privileges and all capabilities dropped.
        chromiumSandbox: false,
        timeout: 30000,
        args: [
            '--disable-background-networking',
            '--disable-background-timer-throttling',
            '--disable-component-update',
            '--disable-default-apps',
            '--disable-dev-shm-usage',
            '--disable-extensions',
            '--disable-renderer-backgrounding',
            '--disable-sync',
            '--metrics-recording-only',
            '--mute-audio',
            '--no-default-browser-check',
            '--no-first-run',
            '--disable-features=AutofillServerCommunication,MediaRouter,OptimizationHints,OptimizationGuideModelDownloading,Translate',
        ],
    }),
    size: poolSize,
    warmSize: 1,
    acquireTimeoutMs,
    browserMaxAgeMs,
    browserMaxRequests,
    memoryLimitBytes,
});

class RenderRequestError extends Error {
    constructor(message, statusCode = 400) {
        super(message);
        this.statusCode = statusCode;
    }
}

function sendJson(response, status, payload, headers = {}) {
    const body = JSON.stringify(payload);
    response.writeHead(status, {
        'content-type': 'application/json',
        'content-length': Buffer.byteLength(body),
        'cache-control': 'no-store',
        ...headers,
    });
    response.end(body);
}

async function readJson(request) {
    const chunks = [];
    let size = 0;

    for await (const chunk of request) {
        size += chunk.length;
        if (size > 1024 * 1024) {
            throw new RenderRequestError('Request body exceeds 1 MiB', 413);
        }
        chunks.push(chunk);
    }

    try {
        return JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch (_) {
        throw new RenderRequestError('Request body must be valid JSON');
    }
}

function validateTarget(rawUrl) {
    let target;
    try {
        target = new URL(rawUrl);
    } catch (_) {
        throw new RenderRequestError('Target URL is invalid');
    }
    if (!['http:', 'https:'].includes(target.protocol)) {
        throw new RenderRequestError('Only HTTP and HTTPS targets are supported');
    }
    if (!allowedHosts.has(target.hostname.toLowerCase())) {
        throw new RenderRequestError(`Target host is not allowed: ${target.hostname}`);
    }
    return target;
}

const SAFE_MAX_DIMENSION = 7680;
const SAFE_MAX_SCALE = 5;
const SAFE_MAX_SCREEN_SCALE = 3;
const SAFE_MAX_WAIT_MS = 60000;

function clampPositiveInt(value, fallback, max = SAFE_MAX_DIMENSION, label = 'value') {
    if (value == null) return fallback;
    const number = Number(value);
    if (!Number.isFinite(number) || number < 1 || number > max) {
        throw new RenderRequestError(`${label} must be between 1 and ${max}, got ${value}`);
    }
    return Math.round(number);
}

function clampPositiveFloat(value, fallback, max, label = 'value') {
    if (value == null) return fallback;
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0 || number > max) {
        throw new RenderRequestError(`${label} must be positive and <= ${max}, got ${value}`);
    }
    return number;
}

function clampNonNegative(value, fallback, max = SAFE_MAX_WAIT_MS, label = 'value') {
    if (value == null) return fallback;
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0 || number > max) {
        throw new RenderRequestError(`${label} must be between 0 and ${max}, got ${value}`);
    }
    return number;
}

function requestIsHealthy(browser) {
    return browser.isConnected();
}

async function resolveNavigation(context, target, extraHeaders, timeout) {
    if (Object.keys(extraHeaders).length === 0) {
        return {url: target, authAllowed: true};
    }

    let current = target;
    let authAllowed = true;
    const visited = new Set();
    for (let redirects = 0; redirects <= 10; redirects++) {
        if (visited.has(current.href)) {
            throw new RenderRequestError('Target redirect loop detected');
        }
        visited.add(current.href);

        const response = await context.request.fetch(current.href, {
            method: 'HEAD',
            headers: authAllowed ? extraHeaders : {},
            maxRedirects: 0,
            failOnStatusCode: false,
            timeout,
        });
        const status = response.status();
        const location = response.headers().location;
        await response.dispose();

        if (status < 300 || status >= 400 || !location) {
            return {url: current, authAllowed};
        }
        let next;
        try {
            next = new URL(location, current);
        } catch (_) {
            throw new RenderRequestError('Target returned an invalid redirect location');
        }
        if (!['http:', 'https:'].includes(next.protocol)) {
            throw new RenderRequestError('Redirect uses an unsupported protocol');
        }
        if (!allowedHosts.has(next.hostname.toLowerCase())) {
            throw new RenderRequestError(`Redirect host is not allowed: ${next.hostname}`);
        }
        authAllowed = authAllowed && next.origin === target.origin;
        current = next;
    }
    throw new RenderRequestError('Target exceeded the redirect limit');
}

async function renderPdf(payload) {
    const target = validateTarget(payload.url);

    const width = clampPositiveInt(payload.device?.width, 1920, SAFE_MAX_DIMENSION, 'viewport width');
    const height = clampPositiveInt(payload.device?.height, 1080, SAFE_MAX_DIMENSION, 'viewport height');
    const deviceScaleFactor = clampPositiveFloat(payload.device?.scale, 1, SAFE_MAX_SCREEN_SCALE, 'device scale');

    const gotoTimeout = clampPositiveInt(payload.render?.timeout, 30000, 120000, 'navigation timeout');
    const waitTime = clampNonNegative(payload.render?.waitTime, 0, SAFE_MAX_WAIT_MS, 'waitTime');

    const pdfScale = clampPositiveFloat(payload.pdf?.scale, 1, SAFE_MAX_SCALE, 'PDF scale');
    const pdfTimeout = clampPositiveInt(payload.pdf?.timeout, gotoTimeout, 120000, 'PDF timeout');

    const targetOrigin = target.origin;
    const extraHeaders = payload.headers && typeof payload.headers === 'object'
        ? Object.fromEntries(Object.entries(payload.headers).map(([key, value]) => [key, String(value)]))
        : {};

    const resource = await browserPool.acquire();
    let context;
    let failed = false;
    try {
        if (!requestIsHealthy(resource.browser)) {
            failed = true;
            throw new PoolUnavailableError('Renderer browser disconnected');
        }

        context = await resource.browser.newContext({
            viewport: {width, height},
            deviceScaleFactor,
            userAgent: payload.device?.userAgent || undefined,
            locale: payload.device?.locale || undefined,
            timezoneId: payload.device?.timezone || undefined,
            javaScriptEnabled: payload.device?.javascriptEnabled !== false,
            serviceWorkers: 'block',
        });
        const page = await context.newPage();
        const navigation = await resolveNavigation(
            context,
            target,
            extraHeaders,
            gotoTimeout,
        );
        const restrictedNames = new Set(
            Object.keys(extraHeaders).map(name => name.toLowerCase()),
        );
        const authorizedNavigations = new WeakSet();
        let initialNavigationStarted = false;

        await page.route('**/*', async route => {
            const request = route.request();
            let requestUrl;
            try {
                requestUrl = new URL(request.url());
            } catch (_) {
                await route.abort('blockedbyclient').catch(() => {});
                return;
            }

            if (!['http:', 'https:'].includes(requestUrl.protocol)) {
                if (/^(data|blob|about|javascript):/i.test(requestUrl.protocol)) {
                    await route.continue().catch(() => {});
                    return;
                }
                console.error(`[renderer] Blocked non-HTTP network request: ${requestUrl.href}`);
                await route.abort('blockedbyclient').catch(() => {});
                return;
            }

            if (!allowedHosts.has(requestUrl.hostname.toLowerCase())) {
                console.error(`[renderer] Blocked request to disallowed host: ${requestUrl.hostname}`);
                await route.abort('blockedbyclient').catch(() => {});
                return;
            }

            const headers = {...request.headers()};
            const isTopLevelNavigation = (
                request.isNavigationRequest()
                && request.frame() === page.mainFrame()
            );
            let authorized = false;
            if (isTopLevelNavigation) {
                const redirectedFrom = request.redirectedFrom();
                authorized = (
                    navigation.authAllowed
                    && (
                        (
                            !initialNavigationStarted
                            && requestUrl.origin === target.origin
                        )
                        || (
                            redirectedFrom
                            && authorizedNavigations.has(redirectedFrom)
                            && requestUrl.origin === target.origin
                        )
                    )
                );
                if (authorized) {
                    initialNavigationStarted = true;
                    authorizedNavigations.add(request);
                }
            }

            if (authorized) {
                Object.assign(headers, extraHeaders);
            } else {
                for (const name of Object.keys(headers)) {
                    if (restrictedNames.has(name.toLowerCase())) delete headers[name];
                }
            }
            await route.continue({headers}).catch(() => {});
        });

        page.setDefaultTimeout(gotoTimeout);
        const waitUntil = payload.render?.waitUntil?.startsWith('networkidle') ? 'networkidle' : 'load';
        const response = await page.goto(navigation.url.toString(), {waitUntil, timeout: gotoTimeout});

        if (!response) {
            throw new RenderRequestError('Navigation produced no response (possible network error)');
        }
        if (response.status() >= 400) {
            throw new RenderRequestError(
                `Navigation returned HTTP ${response.status()} ${response.statusText()}`,
            );
        }

        if (waitTime > 0) {
            await new Promise(resolve => setTimeout(resolve, waitTime));
        }

        const options = payload.pdf || {};
        const size = options.width || options.height
            ? {width: options.width, height: options.height}
            : {format: options.format || 'A4'};
        const pdf = await page.pdf({
            ...size,
            landscape: Boolean(options.landscape),
            printBackground: options.printBackground !== false,
            displayHeaderFooter: Boolean(options.displayHeaderFooter),
            headerTemplate: options.headerTemplate || '',
            footerTemplate: options.footerTemplate || '',
            margin: options.margin || {top: '1cm', right: '0', bottom: '1cm', left: '0'},
            preferCSSPageSize: Boolean(options.preferCSSPageSize),
            omitBackground: Boolean(options.omitBackground),
            pageRanges: options.pageRanges || undefined,
            scale: pdfScale,
            timeout: pdfTimeout,
        });
        return pdf;
    } catch (error) {
        failed = true;
        if (error?.name === 'TimeoutError') {
            error.statusCode = 504;
        }
        throw error;
    } finally {
        if (context) {
            await context.close().catch(() => {});
        }
        browserPool.release(resource, {destroy: failed || !resource.connected});
    }
}

async function healthCheck() {
    const resource = await browserPool.acquire({timeoutMs: 10000});
    let context;
    let failed = false;
    try {
        context = await resource.browser.newContext({serviceWorkers: 'block'});
        const page = await context.newPage();
        await page.close();
    } catch (error) {
        failed = true;
        throw error;
    } finally {
        if (context) {
            await context.close().catch(() => {});
        }
        browserPool.release(resource, {countRender: false, destroy: failed || !resource.connected});
    }
}

const server = http.createServer(async (request, response) => {
    if (request.method === 'GET' && request.url === '/healthz') {
        try {
            await healthCheck();
            await browserPool.refreshMemory();
            sendJson(response, 200, {status: 'ok', pool: browserPool.getStats()});
        } catch (error) {
            console.error('[renderer] healthz failure:', error);
            sendJson(response, 503, {
                status: 'unhealthy',
                error: error.message || 'Renderer health check failed',
            });
        }
        return;
    }

    if (request.method !== 'POST' || request.url !== '/render') {
        sendJson(response, 404, {error: 'Not found'});
        return;
    }

    try {
        const payload = await readJson(request);
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
            throw new RenderRequestError('Request body must be a JSON object');
        }
        if (payload.type && payload.type !== 'pdf') {
            throw new RenderRequestError('Only PDF rendering is supported');
        }
        if (!payload.url || typeof payload.url !== 'string') {
            throw new RenderRequestError('A target URL is required');
        }

        const pdf = await renderPdf(payload);
        response.writeHead(200, {
            'content-type': 'application/pdf',
            'content-length': pdf.length,
            'cache-control': 'no-store',
        });
        response.end(Buffer.from(pdf));
    } catch (error) {
        console.error('[renderer]', error);
        const status = error.statusCode || 400;
        sendJson(
            response,
            status,
            {error: error.message || 'Rendering failed'},
            status === 503 ? {'retry-after': '5'} : {},
        );
    }
});

server.requestTimeout = 180000;
server.headersTimeout = 65000;

let shuttingDown = false;
async function shutdown(signal) {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`[renderer] received ${signal}, shutting down`);
    server.close();
    server.closeIdleConnections?.();
    try {
        await Promise.race([
            new Promise(resolve => server.once('close', resolve)),
            new Promise(resolve => setTimeout(resolve, 15000)),
        ]);
        await browserPool.drain({timeoutMs: 15000});
    } finally {
        process.exit(0);
    }
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));

try {
    await browserPool.warmUp();
    server.listen(port, '0.0.0.0', () => {
        console.log(`[renderer] listening on port ${port} with ${poolSize} browser slots`);
    });
} catch (error) {
    console.error('[renderer] startup failed:', error);
    process.exit(1);
}
