#!/usr/bin/env node

import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import path from 'node:path';
import {deflateSync} from 'node:zlib';

const [baselineImage, optimizedImage] = process.argv.slice(2);
if (!baselineImage || !optimizedImage) {
    console.error('Usage: node selfhost/tests/renderer-benchmark.mjs BASE_IMAGE OPTIMIZED_IMAGE');
    process.exit(2);
}

const dockerContextArgs = process.env.DOCKER_CONTEXT
    ? ['--context', process.env.DOCKER_CONTEXT]
    : [];
const containers = [];

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

function documentBody(origin, title = 'Ricevuta e riepilogo pluriennale', rowCount = 48) {
    const rows = Array.from(
        {length: rowCount},
        (_, index) => `<tr><td>Quota o corso dimostrativo ${index + 1}</td><td>€ ${index + 12},00</td></tr>`,
    ).join('');
    return `<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<style>
@page { size: A4; margin: 12mm; }
body { font-family: "Liberation Sans", sans-serif; color: #20242a; }
header { display: flex; justify-content: space-between; border-bottom: 2px solid #245b91; }
table { width: 100%; border-collapse: collapse; margin-top: 10mm; }
th, td { border-bottom: 1px solid #bbb; padding: 2.5mm; text-align: left; }
img { width: 90px; height: 55px; }
</style></head><body>
<header><div><strong>ASSOZETA BENCHMARK</strong><br>Documento sintetico</div>
<img src="${origin}/logo.svg"></header>
<h1>${title}</h1>
<p>Accenti àèìòù, simboli € e contenuto internazionale Русский 日本語 العربية.</p>
<img src="${origin}/pixel.png" width="20" height="20" alt="Pixel dimostrativo">
<table><thead><tr><th>Descrizione</th><th>Importo</th></tr></thead><tbody>${rows}</tbody></table>
<img src="data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80"><path d="M10 50 Q70 5 130 48 T230 35" fill="none" stroke="#245b91" stroke-width="4"/></svg>')}">
</body></html>`;
}

async function startFixture() {
    const port = await freePort();
    const origin = `http://api.renderer.test:${port}`;
    let active = 0;
    let maxActive = 0;
    const server = http.createServer(async (request, response) => {
        const url = new URL(request.url, origin);
        if (url.pathname.startsWith('/document') && request.method === 'GET') {
            active++;
            maxActive = Math.max(maxActive, active);
            await new Promise(resolve => setTimeout(resolve, 50));
            active--;
            response.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
            const content = {
                '/document': documentBody(origin),
                '/document/invoice': documentBody(origin, 'Fattura dimostrativa'),
                '/document/receipt': documentBody(origin, 'Ricevuta dimostrativa', 8),
                '/document/subscription': documentBody(origin, 'Tesseramento dimostrativo', 12),
                '/document/multi': documentBody(origin, 'Elenco multipagina', 100),
            };
            response.end(content[url.pathname] || documentBody(origin));
            return;
        }
        if (url.pathname === '/logo.svg') {
            response.writeHead(200, {'content-type': 'image/svg+xml'});
            response.end('<svg xmlns="http://www.w3.org/2000/svg" width="180" height="110"><rect width="180" height="110" fill="#245b91"/><circle cx="90" cy="55" r="32" fill="white"/></svg>');
            return;
        }
        if (url.pathname === '/pixel.png') {
            response.writeHead(200, {'content-type': 'image/png'});
            response.end(fixturePng());
            return;
        }
        if (url.pathname.startsWith('/document')) {
            response.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
            response.end(documentBody(origin));
            return;
        }
        response.writeHead(404);
        response.end();
    });
    await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(port, '0.0.0.0', resolve);
    });
    return {
        server,
        origin,
        port,
        maxActive: () => maxActive,
        resetMaxActive: () => {
            maxActive = 0;
        },
    };
}

async function waitForHealth(origin, timeoutMs = 120000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        try {
            const response = await fetch(`${origin}/healthz`, {signal: AbortSignal.timeout(5000)});
            if (response.ok) return;
        } catch (_) {
            // The image itself may still be starting its first browser.
        }
        await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error('Renderer did not become healthy');
}

async function render(origin, target) {
    const response = await fetch(`${origin}/render`, {
        method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({
            type: 'pdf',
            url: target,
            headers: {'x-render-auth': 'synthetic-benchmark'},
            device: {userAgent: 'Assozeta benchmark', scale: 1, width: 1920, height: 1080},
            render: {waitTime: 0, timeout: 30000, waitUntil: 'networkidle2'},
            pdf: {
                scale: 1, displayHeaderFooter: false, printBackground: true, landscape: false,
                pageRanges: '', format: 'A4',
                margin: {top: '1cm', right: '0px', bottom: '1cm', left: '0px'},
                preferCSSPageSize: false, omitBackground: false, timeout: 30000,
            },
        }),
        signal: AbortSignal.timeout(60000),
    });
    const body = Buffer.from(await response.arrayBuffer());
    if (!response.ok || body.subarray(0, 5).toString() !== '%PDF-') {
        throw new Error(`render failed: HTTP ${response.status}: ${body.toString().slice(0, 200)}`);
    }
    return body;
}

function percentile(values, fraction) {
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))];
}

async function measure(image, fixture) {
    fixture.resetMaxActive();
    const port = await freePort();
    const origin = `http://127.0.0.1:${port}`;
    const name = `assozeta-renderer-benchmark-${process.pid}-${Date.now()}`;
    const target = `${fixture.origin}/document`;
    containers.push(name);

    const started = performance.now();
    docker(
        'run', '--detach', '--rm', '--name', name,
        '--publish', `127.0.0.1:${port}:3000`,
        '--add-host', 'api.renderer.test:host-gateway',
        '--shm-size', '1g',
        '--env', 'RENDER_ALLOWED_HOSTS=api.renderer.test',
        '--env', 'RENDER_POOL_SIZE=2',
        image,
    );
    await waitForHealth(origin);
    const startupMs = performance.now() - started;

    for (let index = 0; index < 3; index++) await render(origin, target);
    const sequential = [];
    for (let index = 0; index < 15; index++) {
        const before = performance.now();
        await render(origin, target);
        sequential.push(performance.now() - before);
    }

    const concurrentStarted = performance.now();
    await Promise.all(Array.from(
        {length: 16},
        () => render(origin, target),
    ));
    const concurrentMs = performance.now() - concurrentStarted;
    const memory = docker('stats', '--no-stream', '--format', '{{.MemUsage}}', name);
    const imageInfo = JSON.parse(docker(
        'image', 'inspect', image,
        '--format', '{{json .}}',
    ));
    const containerInfo = JSON.parse(docker(
        'inspect', name,
        '--format', '{{json .}}',
    ));

    const pdfs = {};
    if (process.env.RENDER_BENCHMARK_OUTPUT_DIR) {
        const output = path.join(
            process.env.RENDER_BENCHMARK_OUTPUT_DIR,
            image.replace(/[^a-zA-Z0-9_.-]+/g, '_'),
        );
        await fs.mkdir(output, {recursive: true});
        for (const kind of ('invoice receipt subscription multi'.split(' '))) {
            const pdf = await render(origin, `${fixture.origin}/document/${kind}`);
            await fs.writeFile(path.join(output, `${kind}.pdf`), pdf);
            pdfs[kind] = createHash('sha256').update(pdf).digest('hex');
        }
    }
    docker('stop', '--timeout', '15', name);

    return {
        image,
        architecture: imageInfo.Architecture,
        uncompressedBytes: imageInfo.Size,
        startupMs: Math.round(startupMs),
        sequentialRequests: sequential.length,
        sequentialMeanMs: Math.round(sequential.reduce((sum, value) => sum + value, 0) / sequential.length),
        sequentialP50Ms: Math.round(percentile(sequential, 0.5)),
        sequentialP95Ms: Math.round(percentile(sequential, 0.95)),
        concurrentRequests: 16,
        concurrentWallMs: Math.round(concurrentMs),
        requestsPerSecond: Number((16 / (concurrentMs / 1000)).toFixed(2)),
        fixtureMaxConcurrent: fixture.maxActive(),
        memory: memory.split('/')[0].trim(),
        memoryBytes: containerInfo.HostConfig.Memory || null,
        pdfs,
    };
}

const fixture = await startFixture();
try {
    const baseline = await measure(baselineImage, fixture);
    const optimized = await measure(optimizedImage, fixture);
    console.log(JSON.stringify({baseline, optimized}, null, 2));
} finally {
    await new Promise(resolve => fixture.server.close(resolve));
    for (const container of containers) {
        try {
            docker('rm', '--force', container);
        } catch (_) {
            // --rm removes the container after the normal stop.
        }
    }
}
