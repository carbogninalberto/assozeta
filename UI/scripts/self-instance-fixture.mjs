import {createServer} from 'vite';
import {svelte} from '@sveltejs/vite-plugin-svelte';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const defaults = {enabled: false, api_key_configured: false, model: 'deepseek-v4-flash', cheap_model: 'deepseek-v4-flash', base_url: 'https://api.deepseek.com', max_iterations: 15, max_results: 5000, query_timeout: 10, ws_rate_limit: 10, ws_timeout: 240, history_cap: 50, source: 'environment', revision: 0};
let ai = {...defaults};
let failSave = false;
let maintenance = false;
const config = () => ({oem: {name: 'Associazione Aurora', abbreviation: 'Aurora', primaryColor: '#351dc2', supportEmail: 'support@example.test'}, features: {selfHosted: true, aiEnabled: ai.enabled}});
const server = await createServer({
    root, configFile: false, logLevel: 'error',
    define: {__bakney: JSON.stringify({OEM_CONFIG: {selfHosted: true}, env: {API_HOST: '/api'}})},
    resolve: {alias: {store: `${root}/src/store`, utils: `${root}/src/utils`, components: `${root}/src/components`}},
    plugins: [{name: 'self-instance-fixture', enforce: 'pre',
        resolveId(id) {
            if (id === 'utils/ApiMiddleware.js' || id.endsWith('/utils/ApiMiddleware.js')) return '\0fixture-api';
            if (id === '/fixture-entry.js') return '\0fixture-entry';
        },
        load(id) {
            if (id === '\0fixture-api') return `export const originalFetch = window.fetch.bind(window); export async function apiFetch(url, options = {}) {const res = await fetch('/fixture/api' + url.slice(url.indexOf('/instance') + 9), {...options, headers: {'Content-Type': 'application/json'}}); return {error: !res.ok, response: await res.json()};}`;
            if (id === '\0fixture-entry') return `import {Toaster} from 'svelte-sonner'; new Toaster({target: document.body}); import SelfInstance from '/src/routes/profile/sections/SelfInstance.svelte'; import HeaderActions from '/src/components/HeaderActions.svelte'; import {saveRuntimeConfig} from '/src/store/instanceStore.js'; const data = await fetch('/fixture/api/admin').then(r => r.json()); saveRuntimeConfig(data.config); new HeaderActions({target: document.getElementById('navbar'), props: {quickAddItems: [{id: 'fixture', label: 'Nuovo elemento'}], showQuickAdd: true, showAI: true}}); new SelfInstance({target: document.getElementById('app')});`;
        },
        configureServer(server) {
            server.middlewares.use(async (req, res, next) => {
                if (req.url === '/') {
                    res.setHeader('Content-Type', 'text/html');
                    return res.end(await server.transformIndexHtml('/', `<html lang="it"><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/static/css/bootstrap.min.css"><link rel="stylesheet" href="/static/css/app-bundle.css"><style>body{background:#f3f6fa;padding:24px;font-family:Arial,sans-serif}main{max-width:1200px;margin:auto}#navbar{display:flex;justify-content:flex-end;min-height:36px;margin-bottom:16px}@media(max-width:575px){body{padding:12px}.card-body{padding:16px!important}}</style></head><body><main><nav id="navbar" aria-label="Navbar"></nav><div id="app"></div></main><script type="module" src="/fixture-entry.js"></script></body></html>`));
                }
                if (!req.url.startsWith('/fixture/')) return next();
                let raw = ''; for await (const chunk of req) raw += chunk;
                const body = raw ? JSON.parse(raw) : {};
                const path = req.url.replace('/fixture/api', '');
                res.setHeader('Content-Type', 'application/json');
                const send = value => res.end(JSON.stringify(value));
                if (req.url === '/fixture/reset') {ai = {...defaults}; failSave = false; maintenance = false; return send({});}
                if (req.url === '/fixture/failure') {failSave = body.enabled; return send({});}
                if (req.url === '/fixture/maintenance') {maintenance = body.enabled; return send({});}
                if (path === '/admin') return send({config: config(), mode: 'production', running_version: 'v1.2.3', configured_version: 'v1.2.3'});
                if (path === '/admin/updates') return send({available: true, can_update: true, active: maintenance ? {id: 'fixture', stage: 'backup', target_version: 'v1.2.4'} : null, history: []});
                if (path.startsWith('/admin/releases')) return send({relation: 'current', history: [], pending: [], latest: null});
                if (path === '/admin/diagnostics') return send({overall: 'passed', checked_at: new Date().toISOString(), checks: [], integrations: []});
                if (path === '/admin/email') return send({host: 'smtp.example.test', port: 587, security: 'tls', username: '', from_email: 'hello@example.test', sender_name: 'Aurora', source: 'environment', revision: 0});
                if (path === '/admin/integrations/ai/test') {
                    ai.last_test = {status: 'passed', level: 'connectivity', message: 'Elenco modelli accessibile. Nessuna generazione AI eseguita.', checked_at: new Date().toISOString(), revision: ai.revision};
                    return send(ai.last_test);
                }
                if (path === '/admin/integrations/ai') {
                    if (req.method === 'PUT') {
                        if (failSave) {res.statusCode = 409; return send({error: 'Integrazione modificata in un’altra sessione. Ricarica prima di salvare.'});}
                        const {api_key, clear_secrets, ...values} = body;
                        ai = {...ai, ...values, api_key_configured: clear_secrets?.includes('api_key') ? false : !!api_key || ai.api_key_configured, revision: ai.revision + 1, source: 'instance', last_test: null};
                    }
                    if (req.method === 'DELETE') ai = {...defaults, revision: ai.revision + 1};
                    return send(ai);
                }
                if (path.startsWith('/admin/integrations/')) return send({enabled: false, public_key: '', client_id: '', source: 'environment', revision: 0});
                res.statusCode = 404; send({error: 'Fixture endpoint missing: ' + path});
            });
        },
    }, svelte({configFile: false})],
    server: {host: '127.0.0.1', port: 5193, strictPort: true},
});
await server.listen();
console.log('Self Instance fixture ready on http://127.0.0.1:5193');
