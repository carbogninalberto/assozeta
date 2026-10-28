import fs from 'node:fs';
import http from 'node:http';
import {
    Browser,
    computeExecutablePath,
    detectBrowserPlatform,
} from '@puppeteer/browsers';
import puppeteer from 'puppeteer-core';
import {BrowserPool, PoolUnavailableError} from './browser-pool.mjs';

const PINNED_BROWSER_BUILD = '156.0.8078.47';
const BROWSER_CACHE_DIR = process.env.PUPPETEER_CACHE_DIR || '/opt/puppeteer';
const platform = detectBrowserPlatform();
if (!platform) {
    throw new Error(`Unsupported host platform: ${process.platform}/${process.arch}`);
}
const executablePath = process.env.PUPPETEER_EXECUTABLE_PATH || computeExecutablePath({
    browser: Browser.CHROMEHEADLESSSHELL,
    buildId: PINNED_BROWSER_BUILD,
    cacheDir: BROWSER_CACHE_DIR,
    platform,
});
if (!fs.existsSync(executablePath)) {
    throw new Error(`Pinned Chromium executable is missing: ${executablePath}`);
}

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
const requestTimeoutMs = positiveInt(
    process.env.RENDER_REQUEST_TIMEOUT_MS,
    55000,
    'RENDER_REQUEST_TIMEOUT_MS',
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
const hostResolverRules = [
    'MAP * ~NOTFOUND',
    ...[...allowedHosts].map(host => `EXCLUDE ${host}`),
].join(', ');

const browserPool = new BrowserPool({
    launchBrowser: () => puppeteer.launch({
        executablePath,
        headless: true,
        timeout: 30000,
        protocolTimeout: requestTimeoutMs + 5000,
        args: [
            '--block-new-web-contents',
            '--disable-background-networking',
            '--disable-background-timer-throttling',
            '--disable-component-update',
            '--disable-default-apps',
            '--disable-dev-shm-usage',
            '--disable-extensions',
            '--disable-renderer-backgrounding',
            '--disable-sync',
            `--host-resolver-rules=${hostResolverRules}`,
            '--metrics-recording-only',
            '--mute-audio',
            '--no-default-browser-check',
            '--no-first-run',
            // Docker's default seccomp profile does not provide Chromium's
            // user-namespace sandbox. The service remains non-root with all
            // capabilities dropped, a read-only root, and no-new-privileges.
            '--no-sandbox',
            '--disable-features=AutofillServerCommunication,MediaRouter,OptimizationHints,OptimizationGuideModelDownloading,ServiceWorker,Translate',
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

class RenderAbortedError extends Error {
    constructor(message = 'Render request was aborted') {
        super(message);
        this.name = 'RenderAbortedError';
        this.statusCode = 499;
    }
}

class RenderTimeoutError extends Error {
    constructor(message = 'Render request exceeded its total timeout') {
        super(message);
        this.name = 'RenderTimeoutError';
        this.statusCode = 504;
    }
}

function abortable(promise, signal) {
    if (!signal) return promise;
    if (signal.aborted) {
        return Promise.reject(signal.reason || new RenderAbortedError());
    }
    return new Promise((resolve, reject) => {
        const onAbort = () => reject(signal.reason || new RenderAbortedError());
        signal.addEventListener('abort', onAbort, {once: true});
        Promise.resolve(promise).then(
            value => {
                signal.removeEventListener('abort', onAbort);
                resolve(value);
            },
            error => {
                signal.removeEventListener('abort', onAbort);
                reject(error);
            },
        );
    });
}

function abortableDelay(milliseconds, signal) {
    if (signal?.aborted) {
        return Promise.reject(signal.reason || new RenderAbortedError());
    }
    return new Promise((resolve, reject) => {
        let onAbort;
        const cleanup = () => {
            clearTimeout(timer);
            if (onAbort) signal.removeEventListener('abort', onAbort);
        };
        const timer = setTimeout(() => {
            cleanup();
            resolve();
        }, milliseconds);
        if (signal) {
            onAbort = () => {
                cleanup();
                reject(signal.reason || new RenderAbortedError());
            };
            signal.addEventListener('abort', onAbort, {once: true});
        }
    });
}

function withDeadline(promise, milliseconds, message) {
    let timer;
    const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new RenderTimeoutError(message)), milliseconds);
        timer.unref();
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
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

function strippedHeaders(headers, restrictedNames) {
    const result = {...headers};
    for (const name of Object.keys(result)) {
        if (restrictedNames.has(name.toLowerCase())) delete result[name];
    }
    return result;
}

async function renderPdf(payload, signal) {
    const target = validateTarget(payload.url);
    const width = clampPositiveInt(payload.device?.width, 1920, SAFE_MAX_DIMENSION, 'viewport width');
    const height = clampPositiveInt(payload.device?.height, 1080, SAFE_MAX_DIMENSION, 'viewport height');
    const deviceScaleFactor = clampPositiveFloat(payload.device?.scale, 1, SAFE_MAX_SCREEN_SCALE, 'device scale');
    const gotoTimeout = clampPositiveInt(payload.render?.timeout, 30000, 120000, 'navigation timeout');
    const waitTime = clampNonNegative(payload.render?.waitTime, 0, SAFE_MAX_WAIT_MS, 'waitTime');
    const pdfScale = clampPositiveFloat(payload.pdf?.scale, 1, SAFE_MAX_SCALE, 'PDF scale');
    const pdfTimeout = clampPositiveInt(payload.pdf?.timeout, gotoTimeout, 120000, 'PDF timeout');
    const extraHeaders = payload.headers && typeof payload.headers === 'object'
        ? Object.fromEntries(
            Object.entries(payload.headers)
                .filter(([, value]) => value != null)
                .map(([key, value]) => [key, String(value)]),
        )
        : {};
    const restrictedNames = new Set(Object.keys(extraHeaders).map(name => name.toLowerCase()));

    const acquisition = browserPool.acquire();
    let resource;
    try {
        resource = await abortable(acquisition, signal);
    } catch (error) {
        acquisition.then(
            lateResource => browserPool.release(lateResource, {countRender: false}),
            () => {},
        );
        throw error;
    }
    let browserContext;
    let contextCreated = false;
    let failed = false;
    try {
        if (!resource.browser.connected) {
            failed = true;
            throw new PoolUnavailableError('Renderer browser disconnected');
        }
        browserContext = await abortable(
            resource.browser.createBrowserContext(),
            signal,
        );
        contextCreated = true;
        const page = await abortable(browserContext.newPage(), signal);
        await page.setRequestInterception(true);
        await page.setBypassServiceWorker(true);
        await page.setJavaScriptEnabled(payload.device?.javascriptEnabled !== false);
        await page.evaluateOnNewDocument(() => {
            for (const name of ['WebSocket', 'Worker', 'SharedWorker']) {
                class Blocked {
                    constructor() {
                        throw new Error(`${name}s are disabled during PDF rendering`);
                    }
                }
                Object.defineProperty(globalThis, name, {
                    configurable: false,
                    writable: false,
                    value: Blocked,
                });
            }
        });
        await page.setViewport({width, height, deviceScaleFactor});
        await page.setDefaultTimeout(Math.max(gotoTimeout, pdfTimeout));
        await page.setDefaultNavigationTimeout(gotoTimeout);
        if (payload.device?.userAgent) await page.setUserAgent(payload.device.userAgent);
        if (payload.device?.timezone) await page.emulateTimezone(payload.device.timezone);

        const cdp = await page.createCDPSession();
        await cdp.send('Network.enable');
        await cdp.send('Network.setBlockedURLs', {urls: ['ws://*', 'wss://*']});
        browserContext.on('targetcreated', target => {
            void (async () => {
                if (target.type() !== 'page') return;
                const popup = await target.page();
                if (!popup || popup === page) return;
                await popup.setRequestInterception(true);
                popup.on('request', request => request.abort('blockedbyclient').catch(() => {}));
                await popup.close().catch(() => {});
            })().catch(error => console.error('[renderer] Popup cleanup failed:', error));
        });

        const authorizedInitialNavigations = new WeakSet();
        let initialNavigationStarted = false;
        let requestGuardError;

        page.on('request', request => {
            void (async () => {
                try {
                    let requestUrl;
                    try {
                        requestUrl = new URL(request.url());
                    } catch (_) {
                        await request.abort('blockedbyclient');
                        return;
                    }

                    if (requestUrl.protocol === 'ws:' || requestUrl.protocol === 'wss:') {
                        await request.abort('blockedbyclient');
                        return;
                    }
                    if (!['http:', 'https:'].includes(requestUrl.protocol)) {
                        if (/^(data|blob|about|javascript):/i.test(requestUrl.protocol)) {
                            await request.continue();
                            return;
                        }
                        await request.abort('blockedbyclient');
                        return;
                    }

                    const isMainNavigation = (
                        request.isNavigationRequest()
                        && request.frame() === page.mainFrame()
                    );
                    if (!allowedHosts.has(requestUrl.hostname.toLowerCase())) {
                        console.error(`[renderer] Blocked request to disallowed host: ${requestUrl.hostname}`);
                        if (isMainNavigation) {
                            requestGuardError ||= new RenderRequestError(
                                `Redirect host is not allowed: ${requestUrl.hostname}`,
                            );
                        }
                        await request.abort('blockedbyclient');
                        return;
                    }

                    const chain = request.redirectChain();
                    const isInitialNavigation = (
                        isMainNavigation
                        && !initialNavigationStarted
                        && chain.length === 0
                        && requestUrl.origin === target.origin
                    );
                    const isAuthorizedRedirect = (
                        isMainNavigation
                        && chain.length > 0
                        && authorizedInitialNavigations.has(chain[0])
                        && [...chain, request].every(item => (
                            new URL(item.url()).origin === target.origin
                        ))
                    );
                    const authorized = isInitialNavigation || isAuthorizedRedirect;
                    if (authorized) {
                        initialNavigationStarted = true;
                        authorizedInitialNavigations.add(request);
                    }

                    const headers = strippedHeaders(request.headers(), restrictedNames);
                    if (authorized && Object.keys(extraHeaders).length > 0) {
                        Object.assign(headers, extraHeaders);
                    }
                    await request.continue({headers});
                } catch (error) {
                    requestGuardError ||= error;
                    await request.abort('failed').catch(() => {});
                }
            })();
        });

        const requestedWait = payload.render?.waitUntil;
        const waitUntil = requestedWait?.startsWith('networkidle')
            ? ['load', 'networkidle2']
            : ['load'];
        let response;
        try {
            response = await abortable(
                page.goto(target.toString(), {waitUntil, timeout: gotoTimeout}),
                signal,
            );
        } catch (error) {
            throw requestGuardError || error;
        }
        if (requestGuardError) throw requestGuardError;
        if (!response) {
            throw new RenderRequestError('Navigation produced no response (possible network error)');
        }
        if (response.status() >= 400) {
            throw new RenderRequestError(
                `Navigation returned HTTP ${response.status()} ${response.statusText()}`,
            );
        }

        if (waitTime > 0) await abortableDelay(waitTime, signal);
        if (requestGuardError) throw requestGuardError;

        const options = payload.pdf || {};
        const format = options.format || 'A4';
        const size = options.width || options.height
            ? {width: options.width, height: options.height}
            : format.toLowerCase() === 'a4'
                ? {width: '595.92pt', height: '842.88pt'}
                : {format};
        const pdf = await abortable(
            withDeadline(page.pdf({
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
            }), pdfTimeout, 'PDF generation timed out'),
            signal,
        );
        return Buffer.from(pdf);
    } catch (error) {
        failed = (
            !(error instanceof RenderRequestError)
            && (contextCreated || !resource.connected)
        );
        if (error?.name === 'TimeoutError') error.statusCode = 504;
        throw error;
    } finally {
        if (browserContext) {
            await withDeadline(
                browserContext.close(),
                5000,
                'Browser context close timed out',
            ).catch(() => {});
        }
        browserPool.release(resource, {destroy: failed || !resource.connected});
    }
}

async function healthCheck() {
    await withDeadline(browserPool.probeBrowser(async browser => {
        const browserContext = await browser.createBrowserContext();
        try {
            const page = await browserContext.newPage();
            await page.close();
        } finally {
            await browserContext.close().catch(() => {});
        }
    }), 2500, 'Renderer health check timed out');
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

    const controller = new AbortController();
    const abortOnClose = () => {
        if (!response.writableEnded) controller.abort(new RenderAbortedError());
    };
    const timeout = setTimeout(() => {
        controller.abort(new RenderTimeoutError());
    }, requestTimeoutMs);
    timeout.unref();
    request.once('aborted', abortOnClose);
    response.once('close', abortOnClose);

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
        const pdf = await renderPdf(payload, controller.signal);
        if (response.destroyed) return;
        response.writeHead(200, {
            'content-type': 'application/pdf',
            'content-length': pdf.length,
            'cache-control': 'no-store',
        });
        response.end(pdf);
    } catch (error) {
        if (response.destroyed) return;
        console.error('[renderer]', error);
        const status = error.statusCode || 502;
        sendJson(
            response,
            status,
            {error: error.message || 'Rendering failed'},
            status === 503 ? {'retry-after': '5'} : {},
        );
    } finally {
        clearTimeout(timeout);
        request.off('aborted', abortOnClose);
        response.off('close', abortOnClose);
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
