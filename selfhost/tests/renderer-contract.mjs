#!/usr/bin/env node

import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import http from 'node:http';
import net from 'node:net';
import {deflateSync} from 'node:zlib';

const image = process.argv[2] || 'assozeta-renderer:test';
const dockerContextArgs = process.env.DOCKER_CONTEXT
    ? ['--context', process.env.DOCKER_CONTEXT]
    : [];
const containers = [];
let fixture;

function docker(...args) {
    const result = spawnSync('docker', [...dockerContextArgs, ...args], {
        encoding: 'utf8',
        maxBuffer: 10 * 1024 * 1024,
    });
    if (result.status !== 0) {
        throw new Error(`docker ${args.join(' ')} failed:\n${result.stderr || result.stdout}`);
    }
    return result.stdout.trim();
}

function dockerCombinedOutput(...args) {
    const result = spawnSync('docker', [...dockerContextArgs, ...args], {
        encoding: 'utf8',
        maxBuffer: 10 * 1024 * 1024,
    });
    return `${result.stdout || ''}${result.stderr || ''}`.trim();
}

async function freePort() {
    return new Promise((resolve, reject) => {
        const server = net.createServer();
        server.once('error', reject);
        server.listen(0, '127.0.0.1', () => {
            const {port} = server.address();
            server.close(error => error ? reject(error) : resolve(port));
        });
    });
}

function resetHeaders() {
    fixture.state.headers.length = 0;
    fixture.state.maxConcurrent = 0;
    fixture.state.activeConcurrent = 0;
    fixture.state.serviceWorkerRequests = 0;
    fixture.state.websocketAttempts = 0;
    fixture.state.popupTargets = 0;
    fixture.state.stateReports.length = 0;
}

function fixturePng() {
    const width = 16;
    const height = 16;
    const rows = [];
    for (let y = 0; y < height; y++) {
        const row = Buffer.alloc(1 + width * 4);
        for (let x = 0; x < width; x++) {
            const offset = 1 + x * 4;
            row[offset] = x * 12;
            row[offset + 1] = y * 12;
            row[offset + 2] = 180;
            row[offset + 3] = 255;
        }
        rows.push(row);
    }
    const table = Array.from({length: 256}, (_, index) => {
        let value = index;
        for (let bit = 0; bit < 8; bit++) {
            value = (value & 1) ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
        }
        return value >>> 0;
    });
    const crc32 = buffer => {
        let value = 0xffffffff;
        for (const byte of buffer) value = table[(value ^ byte) & 0xff] ^ (value >>> 8);
        return (value ^ 0xffffffff) >>> 0;
    };
    const chunk = (type, data) => {
        const name = Buffer.from(type);
        const length = Buffer.alloc(4);
        length.writeUInt32BE(data.length);
        const checksum = Buffer.alloc(4);
        checksum.writeUInt32BE(crc32(Buffer.concat([name, data])));
        return Buffer.concat([length, name, data, checksum]);
    };
    const header = Buffer.alloc(13);
    header.writeUInt32BE(width, 0);
    header.writeUInt32BE(height, 4);
    header[8] = 8;
    header[9] = 6;
    return Buffer.concat([
        Buffer.from('89504e470d0a1a0a', 'hex'),
        chunk('IHDR', header),
        chunk('IDAT', deflateSync(Buffer.concat(rows))),
        chunk('IEND', Buffer.alloc(0)),
    ]);
}

function headerAt(fixtureHost, pathname) {
    return fixture.state.headers.find(entry => (
        entry.host === fixtureHost && entry.pathname === pathname
    ));
}

function invoiceBody(assetOrigin, title = 'Ricevuta dimostrativa', blockedOrigin) {
    return `<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <style>
    @page { size: A4; margin: 12mm; }
    body { font-family: "Liberation Sans", sans-serif; color: #20242a; }
    header { display: flex; justify-content: space-between; border-bottom: 2px solid #245b91; }
    table { width: 100%; border-collapse: collapse; margin-top: 12mm; }
    th, td { border-bottom: 1px solid #bbb; padding: 3mm; text-align: left; }
    .signature { width: 52mm; height: 18mm; }
    .page-break { break-before: page; }
  </style>
  <link rel="stylesheet" href="${assetOrigin}/asset.css">
</head>
<body>
  <header>
    <div><strong>ASSOZETA DEMO</strong><br>Via Esempio 1 · Roma</div>
    <img src="${assetOrigin}/asset.svg" alt="Logo dimostrativo" width="80" height="50">
  </header>
  <h1>${title}</h1>
  <p>Documento sintetico per la verifica del renderer, senza dati reali.</p>
  <table>
    <thead><tr><th>Descrizione</th><th>Importo</th></tr></thead>
    <tbody>
      <tr><td>Quota associativa dimostrativa</td><td>€ 30,00</td></tr>
      <tr><td>Corso dimostrativo — accenti àèìòù</td><td>€ 120,00</td></tr>
    </tbody>
  </table>
  <p class="page-break">Contenuto multilingua: Ελληνικά · Русский · 日本語 · العربية.</p>
  <img src="${assetOrigin}/pixel.png" width="20" height="20" alt="Pixel dimostrativo">
  <img src="${blockedOrigin}/blocked-asset.svg" width="1" height="1" alt="">
  <img class="signature" src="data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="260" height="90"><path d="M10 60 Q60 5 100 55 T200 40" fill="none" stroke="#14558b" stroke-width="4"/></svg>')}">
  <iframe src="/fixture/frame-same-origin" title="Iframe interno"></iframe>
</body>
</html>`;
}

async function startFixture() {
    const port = await freePort();
    const state = {
        port,
        headers: [],
        serviceWorkerRequests: 0,
        stateReports: [],
        activeConcurrent: 0,
        maxConcurrent: 0,
        websocketAttempts: 0,
        popupTargets: 0,
    };

    const server = http.createServer(async (request, response) => {
        const url = new URL(request.url, `http://${request.headers.host}`);
        const host = (request.headers.host || '').split(':')[0];
        state.headers.push({
            host,
            pathname: url.pathname,
            auth: request.headers['x-render-auth'] || '',
            method: request.method,
        });

        const apiOrigin = `http://api.renderer.test:${port}`;
        const assetOrigin = `http://assets.renderer.test:${port}`;
        const blockedOrigin = `http://blocked.renderer.test:${port}`;
        const html = body => {
            response.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
            response.end(body);
        };

        if (url.pathname === '/fixture/invoice') return html(invoiceBody(assetOrigin, 'Fattura dimostrativa', blockedOrigin));
        if (url.pathname === '/fixture/receipt') return html(invoiceBody(assetOrigin, undefined, blockedOrigin));
        if (url.pathname === '/fixture/subscription') {
            return html(invoiceBody(assetOrigin, 'Tesseramento dimostrativo', blockedOrigin));
        }
        if (url.pathname === '/fixture/multi') {
            const rows = Array.from(
                {length: 80},
                (_, index) => `<tr><td>Riga dimostrativa ${index + 1}</td><td>€ ${index + 1},00</td></tr>`,
            ).join('');
            return html(invoiceBody(assetOrigin, 'Elenco multipagina', blockedOrigin).replace(
                '<tr><td>Corso dimostrativo — accenti àèìòù</td><td>€ 120,00</td></tr>',
                rows,
            ));
        }
        if (url.pathname === '/fixture/external-assets') {
            return html(`<!doctype html><html><head>
                <link rel="stylesheet" href="${assetOrigin}/asset.css">
                <script src="${assetOrigin}/asset.js"></script>
                </head><body>
                <h1>Contenuto con asset esterni consentiti</h1>
                <img src="${assetOrigin}/asset.svg" width="120" height="80">
                <iframe src="${assetOrigin}/frame"></iframe>
                </body></html>`);
        }
        if (url.pathname === '/fixture/redirect-same') {
            response.writeHead(302, {location: `${apiOrigin}/fixture/invoice`});
            return response.end();
        }
        if (url.pathname === '/fixture/redirect-chain') {
            response.writeHead(302, {location: `${apiOrigin}/fixture/redirect-same`});
            return response.end();
        }
        if (url.pathname === '/fixture/redirect-cross') {
            response.writeHead(302, {location: `${assetOrigin}/fixture/invoice`});
            return response.end();
        }
        if (url.pathname === '/fixture/redirect-blocked') {
            response.writeHead(302, {location: `http://blocked.renderer.test:${port}/fixture/invoice`});
            return response.end();
        }
        if (url.pathname === '/fixture/redirect-get-only') {
            if (request.method === 'HEAD') return html(invoiceBody(assetOrigin, undefined, blockedOrigin));
            response.writeHead(302, {location: `http://blocked.renderer.test:${port}/fixture/popup-target`});
            return response.end();
        }
        if (url.pathname === '/fixture/slow') {
            const delay = Math.min(Number(url.searchParams.get('delay') || 1000), 30000);
            if (request.method === 'GET' && url.searchParams.has('concurrent')) {
                state.activeConcurrent++;
                state.maxConcurrent = Math.max(state.maxConcurrent, state.activeConcurrent);
            }
            await new Promise(resolve => setTimeout(resolve, delay));
            if (request.method === 'GET' && url.searchParams.has('concurrent')) state.activeConcurrent--;
            return html(invoiceBody(assetOrigin, 'Documento con attesa', blockedOrigin));
        }
        if (url.pathname === '/fixture/state-set') {
            return html(`<!doctype html><html><body><script>
                localStorage.setItem('renderer-state', 'leaked-local-storage');
                sessionStorage.setItem('renderer-state', 'leaked-session-storage');
                document.cookie = 'renderer_state=leaked-cookie; path=/';
                document.body.textContent = 'state stored';
                </script></body></html>`);
        }
        if (url.pathname === '/fixture/frame-same-origin') {
            return html('<p>Iframe same-origin dimostrativo</p>');
        }
        if (url.pathname === '/fixture/state-check') {
            return html(`<!doctype html><html><body><script>
                const params = new URLSearchParams({
                    local: localStorage.getItem('renderer-state') || '',
                    session: sessionStorage.getItem('renderer-state') || '',
                    cookie: document.cookie,
                });
                const report = new Image();
                report.src = '/fixture/state-report?' + params.toString();
                document.body.textContent = 'state checked';
                </script></body></html>`);
        }
        if (url.pathname === '/fixture/state-report') {
            state.stateReports.push(Object.fromEntries(url.searchParams));
            return html('reported');
        }
        if (url.pathname === '/fixture/register-sw') {
            return html(`<!doctype html><html><body><script>
                navigator.serviceWorker.register('/service-worker.js').catch(() => {});
                document.body.textContent = 'service worker requested';
                </script></body></html>`);
        }
        if (url.pathname === '/fixture/popup') {
            return html(`<!doctype html><html><body><script>
                window.open('http://blocked.renderer.test:${port}/fixture/popup-target', '_blank');
                document.body.textContent = 'popup requested';
                </script></body></html>`);
        }
        if (url.pathname === '/fixture/popup-target') {
            state.popupTargets++;
            return html('popup target');
        }
        if (url.pathname === '/fixture/websocket') {
            return html(`<!doctype html><html><body><script>
                new WebSocket('ws://api.renderer.test:${port}/socket');
                document.body.textContent = 'websocket requested';
                </script></body></html>`);
        }
        if (url.pathname === '/fixture/worker-websocket') {
            return html(`<!doctype html><html><body><script>
                const source = URL.createObjectURL(new Blob([
                    "new WebSocket('ws://blocked.renderer.test/socket')"
                ], {type: 'text/javascript'}));
                new Worker(source);
                document.body.textContent = 'worker requested';
                </script></body></html>`);
        }
        if (url.pathname === '/fixture/iframe-websocket') {
            return html('<!doctype html><html><body><iframe src="/fixture/websocket"></iframe></body></html>');
        }
        if (url.pathname === '/service-worker.js') {
            state.serviceWorkerRequests++;
            response.writeHead(200, {'content-type': 'text/javascript'});
            return response.end('self.addEventListener("fetch", () => {});');
        }
        if (url.pathname === '/asset.css') {
            response.writeHead(200, {'content-type': 'text/css'});
            return response.end('body { border-top: 3px solid #245b91; }');
        }
        if (url.pathname === '/asset.js') {
            response.writeHead(200, {'content-type': 'text/javascript'});
            return response.end('document.documentElement.dataset.externalScript = "loaded";');
        }
        if (url.pathname === '/asset.svg') {
            response.writeHead(200, {'content-type': 'image/svg+xml'});
            return response.end('<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100"><rect width="160" height="100" fill="#245b91"/><circle cx="80" cy="50" r="30" fill="white"/></svg>');
        }
        if (url.pathname === '/pixel.png') {
            response.writeHead(200, {'content-type': 'image/png'});
            return response.end(fixturePng());
        }
        if (url.pathname === '/frame') return html('<p>Iframe dimostrativo</p>');
        if (url.pathname === '/fixture/server-error') {
            response.writeHead(500, {'content-type': 'text/html'});
            return response.end('<h1>Errore sintetico</h1>');
        }
        response.writeHead(404, {'content-type': 'text/html'});
        response.end('<h1>Pagina non trovata</h1>');
    });
    server.on('upgrade', (request, socket) => {
        state.websocketAttempts++;
        socket.destroy();
    });

    await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(port, '0.0.0.0', resolve);
    });
    return {server, state};
}

async function waitForHealth(origin, timeoutMs = 90000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        try {
            const response = await fetch(`${origin}/healthz`, {signal: AbortSignal.timeout(5000)});
            if (response.ok) return response.json();
        } catch (_) {
            // Browser launch can briefly outlive the container port.
        }
        await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error('Renderer health check did not become ready');
}

async function render(origin, payload) {
    return fetch(`${origin}/render`, {
        method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(45000),
    });
}

async function expectPdf(origin, pathname, fixturePort, options = {}) {
    const response = await render(origin, {
        type: 'pdf',
        url: `http://api.renderer.test:${fixturePort}${pathname}`,
        headers: options.headers || {'x-render-auth': 'fixture-secret-marker'},
        device: {width: 1920, height: 1080, scale: 1},
        render: {
            waitTime: 200,
            timeout: 30000,
            waitUntil: 'networkidle2',
            ...options.render,
        },
        pdf: {
            scale: 1,
            displayHeaderFooter: false,
            printBackground: true,
            landscape: false,
            pageRanges: '',
            format: 'A4',
            margin: {top: '1cm', right: '0px', bottom: '1cm', left: '0px'},
            preferCSSPageSize: false,
            omitBackground: false,
            timeout: 30000,
            ...options.pdf,
        },
    });
    assert.equal(response.status, 200, await response.clone().text());
    assert.match(response.headers.get('content-type') || '', /^application\/pdf/);
    const pdf = Buffer.from(await response.arrayBuffer());
    assert.equal(pdf.subarray(0, 5).toString(), '%PDF-');
    return pdf;
}

async function main() {
    fixture = await startFixture();
    const rendererPort = await freePort();
    const rendererOrigin = `http://127.0.0.1:${rendererPort}`;
    const name = `assozeta-renderer-contract-${process.pid}-${Date.now()}`;
    let passed = false;
    const dockerRun = [
        'run', '--detach', '--name', name,
        '--publish', `127.0.0.1:${rendererPort}:3000`,
        '--add-host', 'api.renderer.test:host-gateway',
        '--add-host', 'assets.renderer.test:host-gateway',
        '--add-host', 'blocked.renderer.test:host-gateway',
        '--read-only',
        '--tmpfs', '/tmp:rw,size=512m,mode=1777',
        '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true',
        '--pids-limit', '512',
        '--shm-size', '1g',
        '--env', 'RENDER_ALLOWED_HOSTS=api.renderer.test,assets.renderer.test',
        '--env', 'RENDER_POOL_SIZE=2',
        '--env', 'RENDER_POOL_ACQUIRE_TIMEOUT_MS=10000',
        image,
    ];
    containers.push(name);
    docker(...dockerRun);

    try {
        const health = await waitForHealth(rendererOrigin);
        assert.equal(health.status, 'ok');
        assert.equal(health.pool.max, 2);
        assert.equal(docker('exec', name, 'id', '-u'), '1000');

        for (const [pathname, title] of [
            ['/fixture/invoice', 'Fattura'],
            ['/fixture/receipt', 'Ricevuta'],
            ['/fixture/subscription', 'Tesseramento'],
            ['/fixture/multi', 'multipagina'],
        ]) {
            const pdf = await expectPdf(rendererOrigin, pathname, fixture.state.port);
            assert.ok(pdf.length > 1000, `${title} PDF is unexpectedly small`);
        }
        for (const pathname of ('/asset.css /asset.svg /pixel.png'.split(' '))) {
            assert.ok(
                fixture.state.headers.filter(entry => entry.pathname === pathname).length >= 4,
                `${pathname} was not requested by every representative document`,
            );
            assert.equal(headerAt('assets.renderer.test', pathname)?.auth || '', '');
        }
        assert.ok(headerAt('api.renderer.test', '/fixture/frame-same-origin'));
        assert.equal(headerAt('api.renderer.test', '/fixture/frame-same-origin')?.auth || '', '');
        assert.equal(headerAt('blocked.renderer.test', '/blocked-asset.svg'), undefined);
        assert.equal(fixture.state.headers.filter(entry => entry.method === 'HEAD').length, 0);
        assert.equal(
            fixture.state.headers.filter(entry => entry.method === 'GET' && entry.pathname === '/fixture/invoice').length,
            1,
        );

        resetHeaders();
        await expectPdf(rendererOrigin, '/fixture/redirect-chain', fixture.state.port);
        assert.equal(headerAt('api.renderer.test', '/fixture/redirect-chain')?.auth, 'fixture-secret-marker');
        assert.equal(headerAt('api.renderer.test', '/fixture/redirect-same')?.auth, 'fixture-secret-marker');
        assert.equal(headerAt('api.renderer.test', '/fixture/invoice')?.auth, 'fixture-secret-marker');

        resetHeaders();
        await expectPdf(rendererOrigin, '/fixture/redirect-cross', fixture.state.port);
        assert.equal(headerAt('api.renderer.test', '/fixture/redirect-cross')?.auth, 'fixture-secret-marker');
        assert.equal(headerAt('assets.renderer.test', '/fixture/invoice')?.auth || '', '');

        resetHeaders();
        await expectPdf(rendererOrigin, '/fixture/external-assets', fixture.state.port);
        assert.equal(headerAt('assets.renderer.test', '/asset.css')?.auth || '', '');
        assert.equal(headerAt('assets.renderer.test', '/asset.js')?.auth || '', '');
        assert.equal(headerAt('assets.renderer.test', '/asset.svg')?.auth || '', '');
        assert.ok(headerAt('assets.renderer.test', '/frame'));
        assert.equal(headerAt('assets.renderer.test', '/frame')?.auth || '', '');

        resetHeaders();
        let response = await render(rendererOrigin, {
            type: 'pdf',
            url: `http://api.renderer.test:${fixture.state.port}/fixture/redirect-blocked`,
            headers: {'x-render-auth': 'fixture-secret-marker'},
        });
        assert.notEqual(response.status, 200);
        assert.equal(headerAt('blocked.renderer.test', '/fixture/invoice'), undefined);

        resetHeaders();
        response = await render(rendererOrigin, {
            type: 'pdf',
            url: `http://api.renderer.test:${fixture.state.port}/fixture/redirect-get-only`,
            headers: {'x-render-auth': 'fixture-secret-marker'},
        });
        assert.notEqual(response.status, 200);
        assert.equal(headerAt('blocked.renderer.test', '/fixture/popup-target'), undefined);

        response = await render(rendererOrigin, {
            type: 'pdf',
            url: `http://blocked.renderer.test:${fixture.state.port}/fixture/invoice`,
        });
        assert.equal(response.status, 400);

        resetHeaders();
        await expectPdf(rendererOrigin, '/fixture/state-set', fixture.state.port);
        await expectPdf(rendererOrigin, '/fixture/state-check', fixture.state.port, {
            render: {waitTime: 1000},
        });
        assert.deepEqual(fixture.state.stateReports, [{local: '', session: '', cookie: ''}]);

        resetHeaders();
        await expectPdf(rendererOrigin, '/fixture/register-sw', fixture.state.port, {
            pdf: {format: 'A4'},
        });
        assert.equal(fixture.state.serviceWorkerRequests, 0);
        await expectPdf(rendererOrigin, '/fixture/popup', fixture.state.port);
        assert.equal(fixture.state.popupTargets, 0);
        await expectPdf(rendererOrigin, '/fixture/websocket', fixture.state.port);
        assert.equal(fixture.state.websocketAttempts, 0);
        await expectPdf(rendererOrigin, '/fixture/worker-websocket', fixture.state.port);
        assert.equal(fixture.state.websocketAttempts, 0);
        await expectPdf(rendererOrigin, '/fixture/iframe-websocket', fixture.state.port);
        assert.equal(fixture.state.websocketAttempts, 0);

        resetHeaders();
        const concurrent = await Promise.all(Array.from({length: 4}, () => expectPdf(
            rendererOrigin,
            '/fixture/slow?delay=700&concurrent=1',
            fixture.state.port,
        )));
        assert.equal(concurrent.length, 4);
        assert.ok(fixture.state.maxConcurrent >= 1);
        assert.ok(fixture.state.maxConcurrent <= 2);

        const exhaustionPromises = Array.from({length: 3}, () => render(rendererOrigin, {
            type: 'pdf',
            url: `http://api.renderer.test:${fixture.state.port}/fixture/slow?delay=15000`,
        }));
        await new Promise(resolve => setTimeout(resolve, 200));
        response = await fetch(`${rendererOrigin}/healthz`, {signal: AbortSignal.timeout(3000)});
        assert.equal(response.status, 200);
        const exhaustion = await Promise.all(exhaustionPromises);
        assert.deepEqual(exhaustion.map(item => item.status).sort(), [200, 200, 503]);

        response = await render(rendererOrigin, {
            type: 'pdf',
            url: `http://api.renderer.test:${fixture.state.port}/fixture/slow?delay=1000`,
            render: {timeout: 200},
        });
        assert.equal(response.status, 504);
        await waitForHealth(rendererOrigin);

        await assert.rejects(fetch(`${rendererOrigin}/render`, {
            method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify({
                type: 'pdf',
                url: `http://api.renderer.test:${fixture.state.port}/fixture/slow?delay=5000`,
            }),
            signal: AbortSignal.timeout(200),
        }));
        await waitForHealth(rendererOrigin);
        await expectPdf(rendererOrigin, '/fixture/invoice', fixture.state.port);

        response = await render(rendererOrigin, {
            type: 'pdf',
            url: `http://api.renderer.test:${fixture.state.port}/fixture/server-error`,
        });
        assert.equal(response.status, 400);
        response = await render(rendererOrigin, {
            type: 'pdf',
            url: `http://api.renderer.test:${fixture.state.port}/fixture/missing`,
        });
        assert.equal(response.status, 400);
        response = await render(rendererOrigin, {
            type: 'image',
            url: `http://api.renderer.test:${fixture.state.port}/fixture/invoice`,
        });
        assert.equal(response.status, 400);
        response = await render(rendererOrigin, {
            type: 'pdf',
            url: 'ftp://api.renderer.test/fixture/invoice',
        });
        assert.equal(response.status, 400);
        response = await render(rendererOrigin, {
            type: 'pdf',
            url: `http://api.renderer.test:${fixture.state.port}/fixture/invoice`,
            device: {width: 0},
        });
        assert.equal(response.status, 400);
        response = await fetch(`${rendererOrigin}/render`, {
            method: 'POST',
            headers: {'content-type': 'application/json'},
            body: '{invalid',
        });
        assert.equal(response.status, 400);
        response = await fetch(`${rendererOrigin}/render`, {
            method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify({
                type: 'pdf',
                url: `http://api.renderer.test:${fixture.state.port}/fixture/invoice`,
                padding: 'x'.repeat(1024 * 1024),
            }),
        });
        assert.equal(response.status, 413);
        response = await fetch(`${rendererOrigin}/render`);
        assert.equal(response.status, 404);

        await expectPdf(rendererOrigin, '/fixture/multi', fixture.state.port, {
            pdf: {
                landscape: true,
                displayHeaderFooter: true,
                headerTemplate: '<span class="title"></span>',
                footerTemplate: '<span class="pageNumber"></span>',
                pageRanges: '1-2',
                margin: {top: '2cm', right: '1cm', bottom: '2cm', left: '1cm'},
            },
        });

        const browserKiller = `
            const fs = require('fs');
            for (const pid of fs.readdirSync('/proc').filter(name => /^\\d+$/.test(name))) {
                if (Number(pid) === process.pid) continue;
                try {
                    if (fs.readFileSync('/proc/' + pid + '/cmdline').toString().includes('headless_shell')) {
                        process.kill(Number(pid), 'SIGKILL');
                    }
                } catch (_) {}
            }
        `;
        docker('exec', name, 'node', '-e', browserKiller);
        await waitForHealth(rendererOrigin);
        await expectPdf(rendererOrigin, '/fixture/invoice', fixture.state.port);

        const timeoutPort = await freePort();
        const timeoutOrigin = `http://127.0.0.1:${timeoutPort}`;
        const timeoutName = `assozeta-renderer-deadline-${process.pid}-${Date.now()}`;
        containers.push(timeoutName);
        docker(
            'run', '--detach', '--name', timeoutName,
            '--publish', `127.0.0.1:${timeoutPort}:3000`,
            '--add-host', 'api.renderer.test:host-gateway',
            '--read-only',
            '--tmpfs', '/tmp:rw,size=512m,mode=1777',
            '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true',
            '--env', 'RENDER_ALLOWED_HOSTS=api.renderer.test',
            '--env', 'RENDER_POOL_SIZE=1',
            '--env', 'RENDER_REQUEST_TIMEOUT_MS=1500',
            image,
        );
        await waitForHealth(timeoutOrigin);
        const deadlineStarted = Date.now();
        response = await render(timeoutOrigin, {
            type: 'pdf',
            url: `http://api.renderer.test:${fixture.state.port}/fixture/slow?delay=5000`,
            render: {timeout: 120000},
        });
        const deadlineElapsed = Date.now() - deadlineStarted;
        assert.equal(response.status, 504);
        assert.ok(deadlineElapsed < 60000, `total deadline took ${deadlineElapsed} ms`);
        docker('stop', '--time', '10', timeoutName);

        console.log('Renderer API, security, isolation, pool and recovery contracts passed.');
        passed = true;
    } finally {
        if (!passed) {
            try {
                console.error(`--- fixture requests: ${JSON.stringify(fixture.state.headers)}`);
                console.error(
                    `--- renderer state: ${docker('inspect', name, '--format', '{{json .State}}')}`,
                );
                console.error(`--- renderer logs: ${name} ---\n${dockerCombinedOutput('logs', name)}`);
            } catch (_) {
                // Keep the original failure if logs are unavailable.
            }
        }
        docker('stop', '--time', '10', name);
    }
}

try {
    await main();
} finally {
    if (fixture?.server) await new Promise(resolve => fixture.server.close(resolve));
    for (const container of containers) {
        try {
            docker('rm', '--force', container);
        } catch (_) {
            // --rm normally removes the container after the explicit stop.
        }
    }
}
