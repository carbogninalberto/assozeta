import http from 'node:http';
import {chromium} from 'playwright-core';

const port = Number.parseInt(process.env.PORT || '3000', 10);
const allowedHosts = new Set(
    (process.env.RENDER_ALLOWED_HOSTS || 'api')
        .split(',')
        .map(host => host.trim().toLowerCase())
        .filter(Boolean),
);

let browserPromise;

function getBrowser() {
    if (!browserPromise) {
        const launch = chromium.launch({
            headless: true,
            // Playwright disables Chromium's sandbox by default. The process
            // still runs as a non-root user in a no-new-privileges container.
            chromiumSandbox: false,
        });
        browserPromise = launch;

        launch.then(browser => {
            browser.on('disconnected', () => {
                if (browserPromise === launch) {
                    browserPromise = undefined;
                }
            });
        }, () => {
            if (browserPromise === launch) {
                browserPromise = undefined;
            }
        });
    }

    return browserPromise;
}

function sendJson(response, status, payload) {
    response.writeHead(status, {'content-type': 'application/json'});
    response.end(JSON.stringify(payload));
}

async function readJson(request) {
    const chunks = [];
    let size = 0;

    for await (const chunk of request) {
        size += chunk.length;
        if (size > 1024 * 1024) {
            throw new Error('Request body exceeds 1 MiB');
        }
        chunks.push(chunk);
    }

    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function validateTarget(rawUrl) {
    const target = new URL(rawUrl);
    if (!['http:', 'https:'].includes(target.protocol)) {
        throw new Error('Only HTTP and HTTPS targets are supported');
    }
    if (!allowedHosts.has(target.hostname.toLowerCase())) {
        throw new Error(`Target host is not allowed: ${target.hostname}`);
    }
    return target;
}

const SAFE_MAX_DIMENSION = 7680;
const SAFE_MAX_SCALE = 5;
const SAFE_MAX_SCREEN_SCALE = 3;
const SAFE_MAX_WAIT_MS = 60000;

function clampPositiveInt(value, fallback, max = SAFE_MAX_DIMENSION, label = 'value') {
    if (value == null) return fallback;
    const n = Number(value);
    if (!Number.isFinite(n) || n < 1 || n > max) {
        throw new Error(`${label} must be between 1 and ${max}, got ${value}`);
    }
    return Math.round(n);
}

function clampPositiveFloat(value, fallback, max, label = 'value') {
    if (value == null) return fallback;
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0 || n > max) {
        throw new Error(`${label} must be positive and <= ${max}, got ${value}`);
    }
    return n;
}

function clampNonNegative(value, fallback, max = SAFE_MAX_WAIT_MS, label = 'value') {
    if (value == null) return fallback;
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0 || n > max) {
        throw new Error(`${label} must be between 0 and ${max}, got ${value}`);
    }
    return n;
}

async function renderPdf(payload) {
    const target = validateTarget(payload.url);
    const browser = await getBrowser();

    // Validate numeric viewport / PDF inputs ----------------------------------
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

    const context = await browser.newContext({
        viewport: {width, height},
        deviceScaleFactor,
        userAgent: payload.device?.userAgent || undefined,
        serviceWorkers: 'block',
    });
    const page = await context.newPage();
    const authorizedNavigations = new WeakSet();
    let initialNavigationStarted = false;

    try {
        await page.route('**/*', (route) => {
            const req = route.request();
            const reqUrl = req.url();
            const protocol = reqUrl.startsWith('http:') ? 'http:'
                : reqUrl.startsWith('https:') ? 'https:'
                : 'other';

            if (protocol === 'other') {
                if (/^(data|blob|about|javascript):/i.test(reqUrl)) {
                    route.continue();
                    return;
                }
                console.error(`[renderer] Blocked non-HTTP network request: ${reqUrl}`);
                route.abort('blockedbyclient');
                return;
            }

            const hostname = new URL(reqUrl).hostname.toLowerCase();
            if (!allowedHosts.has(hostname)) {
                console.error(`[renderer] Blocked request to disallowed host: ${hostname}`);
                route.abort('blockedbyclient');
                return;
            }

            const headers = {...req.headers()};
            const isTopLevelNavigation = req.isNavigationRequest() && req.frame() === page.mainFrame();
            if (isTopLevelNavigation) {
                const reqOrigin = new URL(reqUrl).origin;
                const redirectedFrom = req.redirectedFrom();
                const isInitialRequest = !initialNavigationStarted && reqUrl === target.toString();
                const isSameOriginRedirect = redirectedFrom
                    && authorizedNavigations.has(redirectedFrom)
                    && reqOrigin === targetOrigin;

                if (isInitialRequest || isSameOriginRedirect) {
                    initialNavigationStarted = true;
                    authorizedNavigations.add(req);
                }
                if ((isInitialRequest || isSameOriginRedirect) && Object.keys(extraHeaders).length > 0) {
                    Object.assign(headers, extraHeaders);
                }
            }

            route.continue({headers});
        });

        const waitUntil = payload.render?.waitUntil?.startsWith('networkidle') ? 'networkidle' : 'load';
        const response = await page.goto(target.toString(), {waitUntil, timeout: gotoTimeout});

        if (!response) {
            throw new Error('Navigation produced no response (possible network error)');
        }
        if (response.status() >= 400) {
            throw new Error(`Navigation returned HTTP ${response.status()} ${response.statusText()}`);
        }

        if (waitTime > 0) {
            await new Promise(resolve => setTimeout(resolve, waitTime));
        }

        const options = payload.pdf || {};
        const pdf = await page.pdf({
            format: options.format || 'A4',
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
    } finally {
        await context.close();
    }
}

const server = http.createServer(async (request, response) => {
    if (request.method === 'GET' && request.url === '/healthz') {
        try {
            const browser = await getBrowser();
            const page = await browser.newPage();
            await page.close();
            sendJson(response, 200, {status: 'ok'});
        } catch (err) {
            console.error('[renderer] healthz failure:', err);
            sendJson(response, 503, {status: 'unhealthy', error: err.message});
        }
        return;
    }

    if (request.method !== 'POST' || request.url !== '/render') {
        sendJson(response, 404, {error: 'Not found'});
        return;
    }

    try {
        const payload = await readJson(request);
        if (payload.type && payload.type !== 'pdf') {
            throw new Error('Only PDF rendering is supported');
        }
        if (!payload.url) {
            throw new Error('A target URL is required');
        }

        const pdf = await renderPdf(payload);
        response.writeHead(200, {
            'content-type': 'application/pdf',
            'content-length': pdf.length,
        });
        response.end(Buffer.from(pdf));
    } catch (error) {
        console.error('[renderer]', error);
        sendJson(response, 400, {error: error.message || 'Rendering failed'});
    }
});

server.listen(port, '0.0.0.0', () => {
    console.log(`[renderer] listening on port ${port}`);
});

async function shutdown() {
    server.close();
    try {
        if (browserPromise) {
            const browser = await browserPromise;
            if (browser.isConnected()) {
                await browser.close();
            }
        }
    } catch (_) {
        // Browser launch, disconnection, or shutdown may already be in progress.
    }
    process.exit(0);
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
