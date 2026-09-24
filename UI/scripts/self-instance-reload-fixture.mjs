// Production-built Self Instance behind the real Caddy config, with disposable API state.
import {build} from 'vite';
import {svelte} from '@sveltejs/vite-plugin-svelte';
import {createServer} from 'node:http';
import {mkdtemp, mkdir, readFile, writeFile, cp, rm, symlink, realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';

const root = fileURLToPath(new URL('../', import.meta.url));
const temp = await realpath(await mkdtemp(join(tmpdir(), 'assozeta-reload-')));
const container = `assozeta-reload-${process.pid}`;
const port = Number(process.env.ASSOZETA_RELOAD_TEST_PORT || 5194);
let version = 1;
let stage = null;
let unavailable = false;
let writes = 0;
let reads = 0;
let connections = 0;
const sockets = new Set();
let config;
let restartOperation;
let restartBehavior;
let restartTimer;
function reset() {
    clearTimeout(restartTimer);
    restartOperation = null; restartBehavior = 'success';
    stage = null; unavailable = false; writes = 0; reads = 0;
    config = {oem: {name: 'Associazione Aurora', abbreviation: 'Aurora', primaryColor: '#351dc2', supportEmail: 'support@example.test'}, features: {selfHosted: true, aiEnabled: false}};
}
reset();
const operation = () => ({id: 'fcc58c2a-436c-4c95-aa38-ea9b4ef6b135', actor_id: 'fixture-owner', source_version: 'v1.0.5', target_version: 'v1.0.6', stage, status: stage === 'backup' ? 'running' : stage});
const status = () => ({available: true, protocol: 1, is_owner: true, can_restart: stage !== 'recovery_required', can_update: stage !== 'recovery_required',
    active: restartOperation?.status === 'running' ? restartOperation : stage === 'backup' ? operation() : null,
    history: [...(restartOperation && restartOperation.status !== 'running' ? [restartOperation] : []), ...(stage && stage !== 'backup' ? [operation()] : [])]});
const html = '<html lang="it"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/bootstrap.min.css"><link rel="stylesheet" href="/static/css/app-bundle.css"><link rel="stylesheet" href="/global.css"><link rel="stylesheet" href="/dark-mode.css"><link rel="stylesheet" href="/brand.css"><style>body{background:#f3f6fa;padding:24px;font-family:Arial,sans-serif}main{max-width:1200px;margin:auto}@media(max-width:575px){body{padding:12px}.card-body{padding:16px!important}}</style></head><body><main><output aria-label="Versione frontend"></output><div id="app"></div></main><script type="module" src="/reload-fixture-entry.js"></script></body></html>';
await writeFile(join(temp, 'index.html'), html);
await cp(join(root, 'package.json'), join(temp, 'package.json'));
await symlink(join(root, 'node_modules'), join(temp, 'node_modules'), 'dir');
const apiModule = `export const originalFetch = window.fetch.bind(window);
export async function apiFetch(url, options = {}) {
    const response = await fetch(url, {...options, headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + JSON.parse(localStorage.getItem('sessionToken'))}});
    return {error: !response.ok, response: await response.json(), status: response.status};
}`;
try {
    for (const revision of [1, 2]) {
        await build({root: temp, configFile: false, logLevel: 'error', publicDir: false,
            define: {__bakney: JSON.stringify({OEM_CONFIG: {selfHosted: true}, env: {API_HOST: '/api'}})},
            resolve: {alias: {store: `${root}/src/store`, utils: `${root}/src/utils`, components: `${root}/src/components`}},
            plugins: [{name: 'reload-fixture', enforce: 'pre', resolveId(id) {
                if (id === 'utils/ApiMiddleware.js' || id.endsWith('/utils/ApiMiddleware.js')) return '\0fixture-api';
                if (id === '/reload-fixture-entry.js') return '\0fixture-entry';
            }, load(id) {
                if (id === '\0fixture-api') return apiModule;
                if (id === '\0fixture-entry') return `import SelfInstance from ${JSON.stringify(root + '/src/routes/profile/sections/SelfInstance.svelte')};
                    import {saveRuntimeConfig} from ${JSON.stringify(root + '/src/store/instanceStore.js')};
                    import {apiFetch} from 'utils/ApiMiddleware.js';
                    window.cachedConfigAtStartup = localStorage.getItem('assozeta_instance_config');
                    document.querySelector('output').textContent = 'Frontend ${revision}';
                    const socket = new WebSocket(location.origin.replace(/^http/, 'ws') + '/ws/fixture');
                    window.addEventListener('pagehide', () => socket.close());
                    apiFetch('/api/instance/admin').then(result => {
                        if (!result.error) saveRuntimeConfig(result.response.config);
                        new SelfInstance({target: document.getElementById('app')});
                    });`;
            }}, svelte({configFile: false})],
            build: {outDir: join(temp, String(revision)), emptyOutDir: true, rollupOptions: {input: join(temp, 'index.html'), output: {entryFileNames: 'build-assets/[name]-[hash].js', assetFileNames: 'build-assets/[name]-[hash][extname]'}}},
        });
    }
    await mkdir(join(temp, 'serve'));
    await cp(join(root, 'public'), join(temp, 'serve'), {recursive: true});
    async function deploy(revision) {
        version = revision;
        await cp(join(temp, String(revision)), join(temp, 'serve'), {recursive: true});
    }
    await deploy(1);
    const api = createServer(async (req, res) => {
        res.setHeader('Content-Type', 'application/json');
        res.setHeader('Cache-Control', 'no-store');
        const send = value => res.end(JSON.stringify(value));
        let raw = ''; for await (const chunk of req) raw += chunk;
        const body = raw ? JSON.parse(raw) : {};
        if (req.url === '/fixture/reset') {reset(); await deploy(1); return send({});}
        if (req.url === '/fixture/state') {
            if (req.method === 'POST') {
                if (body.version) await deploy(body.version);
                if ('stage' in body) stage = body.stage;
                if ('unavailable' in body) unavailable = body.unavailable;
                if ('restartBehavior' in body) restartBehavior = body.restartBehavior;
            }
            return send({version, stage, unavailable, writes, reads, connections, status: status()});
        }
        if (req.url === '/readyz' || req.url === '/api/readyz') {res.statusCode = unavailable ? 503 : 200; return send({ready: !unavailable});}
        if (req.headers.authorization !== 'Bearer fixture-session') {res.statusCode = 403; return send({error: 'Sessione non valida'});}
        if (req.url === '/instance-update-status') {
            if (unavailable && restartOperation) {res.statusCode = 503; return send({error: 'Riavvio'});}
            return send(status());
        }
        if (unavailable) {res.statusCode = 503; return send({error: 'Manutenzione'});}
        const path = req.url;
        if (req.method !== 'GET') writes++;
        else reads++;
        if (path === '/instance/admin') {
            if (req.method === 'PUT') {
                // Leave a genuine in-flight save long enough to assert its reload guard.
                await new Promise(resolve => setTimeout(resolve, 800));
                config.oem = {...config.oem, ...body.oem};
            }
            return send({config, mode: 'production', running_version: 'v1.0.5'});
        }
        if (path === '/instance/admin/updates') return send(status());
        if (path === '/instance/admin/restarts' && req.method === 'POST') {
            restartOperation = {id: 'fixture-restart', request_id: body.request_id, kind: 'restart', stage: 'restarting', status: 'running', target_version: 'v1.0.5'};
            unavailable = true;
            if (restartBehavior !== 'stall') restartTimer = setTimeout(() => {
                unavailable = false;
                restartOperation = {...restartOperation, stage: restartBehavior === 'failed' ? 'failed' : 'completed',
                    status: restartBehavior === 'failed' ? 'failed' : 'succeeded', verified_at: new Date().toISOString(),
                    ...(restartBehavior === 'failed' ? {error: 'Riavvio non riuscito.', recovery: 'Verifica i container e i log del server.'} : {})};
            }, 2500);
            if (restartBehavior === 'lost-response') return req.socket.destroy();
            res.statusCode = 202; return send({operation: restartOperation});
        }
        if (path.startsWith('/instance/admin/releases')) return send({relation: 'current', history: [], pending: [], latest: null});
        if (path === '/instance/admin/diagnostics') return send({overall: 'passed', checks: [], integrations: []});
        if (path === '/instance/admin/email') return send({host: 'smtp.example.test', port: 587, security: 'tls', source: 'environment', revision: 0});
        if (path.startsWith('/instance/admin/integrations/')) return send({enabled: false, model: 'fixture', api_key_configured: false, source: 'environment', revision: 0});
        res.statusCode = 404; send({error: `Unknown fixture endpoint: ${path}`});
    });
    // Minimal local WebSocket handshake: prove a document reload reconnects.
    api.on('upgrade', (req, socket) => {
        if (req.url !== '/ws/fixture' || !req.headers['sec-websocket-key']) return socket.destroy();
        const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
        socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
        connections++; sockets.add(socket);
        socket.on('error', () => socket.destroy());
        socket.on('close', () => sockets.delete(socket));
        socket.on('data', frame => {if ((frame[0] & 15) === 8) socket.end(Buffer.from([0x88, 0]));});
    });
    await new Promise(resolve => api.listen(0, '0.0.0.0', resolve));
    const upstream = `host.docker.internal:${api.address().port}`;
    const caddy = (await readFile(join(root, '../selfhost/caddy/Caddyfile'), 'utf8'))
        .replaceAll('api:8000', upstream).replace('unix//run/assozeta-update-status/status.sock', upstream);
    await writeFile(join(temp, 'Caddyfile'), caddy);
    execFileSync('docker', ['run', '--rm', '-d', '--name', container, '--add-host', 'host.docker.internal:host-gateway', '-p', `127.0.0.1:${port}:80`,
        '-e', 'SITE_ADDRESS=:80', '-v', `${temp}/serve:/srv:ro`, '-v', `${temp}/Caddyfile:/etc/caddy/Caddyfile:ro`, 'caddy:2-alpine'], {stdio: 'pipe'});
    console.log(`Reload fixture: http://127.0.0.1:${port}`);
    let closing = false;
    async function close() {
        if (closing) return; closing = true;
        try {execFileSync('docker', ['rm', '-f', container], {stdio: 'pipe'});} finally {
            for (const socket of sockets) socket.destroy();
            api.closeAllConnections(); api.close(); await rm(temp, {recursive: true, force: true});
        }
    }
    process.on('SIGTERM', () => close().then(() => process.exit()));
    process.on('SIGINT', () => close().then(() => process.exit()));
} catch (error) {
    try {execFileSync('docker', ['rm', '-f', container], {stdio: 'pipe'});} catch {}
    await rm(temp, {recursive: true, force: true});
    throw error;
}
