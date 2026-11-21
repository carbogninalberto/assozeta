import {readFile} from 'node:fs/promises';
import {test as base, expect} from '@playwright/test';
import {controller, snapshot} from '../../../UI/scripts/p5-runtime-fixtures.mjs';

export const settings = '/#/profile?page=self-instance&tab=api';
const oems = JSON.parse(await readFile(new URL('../../../UI/oems.json', import.meta.url)));
const maintenancePage = await readFile(new URL('../../../UI/public/maintenance.html', import.meta.url), 'utf8');
const retired = {assozeta_api_override: 'v2', assozeta_api_v2_probe: 'ok', assozeta_ws_v2_fallback: '["/ws/health/"]'};

export const test = base.extend({
    stub: async ({browser, baseURL}, use) => {
        const contexts = [], errors = [], openSockets = new Set();
        const state = {runtime: snapshot(), offline: false, calls: [], onSubmit: null};
        state.disconnect = async (path = null) => {
            const selected = [...openSockets].filter(ws => !path || new URL(ws.url()).pathname === path);
            await Promise.all(selected.map(ws => ws.close({code: 1001, reason: 'Manutenzione'})));
            selected.forEach(ws => openSockets.delete(ws));
        };
        state.open = async ({owner = true, stale = false, path = settings} = {}) => {
            const role = owner ? 'administrator' : 'association';
            const user = {user_id: 'fixture-user', username: 'fixture', first_name: 'Fixture', role, is_superuser: owner};
            const session = {sessionToken: 'fixture', refreshToken: 'renew', expires: Date.now() + 3600000,
                role, userData: user, subPage: 'self-instance', permissions: [], snoozeTestimonial: true};
            const items = Object.entries(session).map(([name, value]) => ({name, value: JSON.stringify(value)}));
            if (stale) for (const [name, value] of Object.entries(retired)) items.push({name, value});
            const context = await browser.newContext({baseURL, serviceWorkers: 'block',
                storageState: {cookies: [], origins: [{origin: baseURL, localStorage: items}]}});
            contexts.push(context);
            if (stale) await context.addInitScript(values => {
                for (const [key, value] of Object.entries(values)) sessionStorage.setItem(key, value);
            }, retired);
            const page = await context.newPage();
            page.on('pageerror', e => errors.push(e.message));
            const sockets = [];
            await context.routeWebSocket(/\/ws\//, ws => {
                sockets.push({path: new URL(ws.url()).pathname, generation: state.runtime.generation});
                if (state.offline) return ws.close({code: 1013, reason: 'Manutenzione'});
                openSockets.add(ws);
                ws.onClose(() => openSockets.delete(ws));
                ws.onMessage(raw => {
                    const msg = JSON.parse(raw);
                    if (msg.type === 'ping') ws.send(JSON.stringify({type: 'pong', timestamp: msg.timestamp}));
                });
            });
            await context.route('**/*', async route => {
                const request = route.request(), url = new URL(request.url());
                if (url.origin !== baseURL) return route.abort();
                const path = url.pathname;
                const json = (value, status = 200) => route.fulfill({status, json: value});
                if (!path.startsWith('/api/') && path !== '/instance-update-status') {
                    if (state.offline && request.isNavigationRequest())
                        return route.fulfill({status: 503, contentType: 'text/html', body: maintenancePage});
                    if (state.offline && !['/healthz', '/maintenance.html', '/offline.html', '/sw.js'].includes(path))
                        return route.fulfill({status: 503, body: ''});
                    if (path === '/healthz') return json({});
                    return route.continue();
                }
                const method = request.method();
                const {authorization} = request.headers();
                const call = {path, method, authorization, page, generation: state.runtime.generation};
                state.calls.push(call);
                const allowed = owner && authorization === 'Bearer fixture';
                if (path === '/instance-update-status') {
                    if (method !== 'POST') return json({}, 405);
                    return allowed ? json(controller(state.runtime)) : json({}, 403);
                }
                if (state.offline) return json({error: 'Manutenzione.'}, 503);
                const endpoint = path.replace(/^\/api(?:\/v[12])?\//, '/');
                if (endpoint === '/instance/admin/runtime') {
                    if (!allowed) return json({error: 'Accesso negato.'}, 403);
                    if (method === 'GET') return json(state.runtime);
                    if (method === 'POST' && state.onSubmit) {
                        call.body = request.postDataJSON();
                        return state.onSubmit(route, call.body);
                    }
                    return json({error: 'Invio imprevisto.'}, 400);
                }
                if (method !== 'GET') return json({error: 'Operazione imprevista.'}, 405);
                const config = {oem: {...oems.assozeta, name: 'Fixture ' + state.runtime.generation},
                    features: {selfHosted: true, aiEnabled: false, uiApiVersion: stale ? 'v2' : 'v1'}};
                switch (endpoint) {
                    case '/instance/status': return json({configured: true});
                    case '/instance/config': return json(config);
                    case '/instance/access': return json({is_owner: owner, is_administrator: owner});
                    case '/instance/admin': return allowed ? json({config, mode: 'production',
                        configured_version: 'v1.0.0', running_version: 'v1.0.0', go_tasks_enabled: stale}) : json({}, 403);
                    case '/instance/admin/updates': {
                        if (!allowed) return json({}, 403);
                        const {runtime, ...updates} = controller(state.runtime);
                        return json(updates);
                    }
                    case '/instance/admin/releases': return json({relation: 'current', history: [], pending: []});
                    case '/instance/admin/diagnostics': return json({overall: 'passed', integrations: [], checks: []});
                    case '/profile/info': return json({info: {role}, user_data: user});
                    case '/administration/impersonation/users': return json({users: [], meta: {total: 0, pages: 1}});
                    case '/readyz': return json({ready: true});
                    default: return json({});
                }
            });
            const response = await page.goto(new URL(path, baseURL).href);
            return {page, context, response, sockets};
        };
        try { await use(state); }
        finally {
            await Promise.all(contexts.map(context => context.close()));
            expect(errors, 'Application errors while exercising the fixture').toEqual([]);
        }
    },
});
export {expect, retired};
