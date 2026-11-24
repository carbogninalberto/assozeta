import {writable} from 'svelte/store';
import {v4 as uuidv4} from 'uuid';
import {readIndependentStatus} from '../routes/profile/sections/independentStatus.js';
import {WRONG_PASSWORD} from '../routes/profile/sections/backupPassword.js';

/**
 * The owner's view of the instance runtime switch (Django or Go).
 *
 * The server is the only authority: this store reads its status (P5-STATUS)
 * from the owner API and, while neither application API answers, from the
 * controller's status socket. It never reports a result the server did not
 * report, never retries a submission on its own, and follows whichever
 * operation the server says is in progress.
 */

export const RUNTIME_ENDPOINT = '/api/instance/admin/runtime';
export const RUNTIMES = ['django', 'go'];
const ACTIVE = ['queued', 'running'];

// Only the fields the status contract lists reach the page.
const pick = (source, keys) => source && typeof source === 'object'
    ? Object.fromEntries(keys.filter(key => key in source).map(key => [key, source[key]])) : null;

function operationView(op) {
    if (!op || typeof op !== 'object') return null;
    const view = pick(op, ['id', 'kind', 'status', 'stage', 'previous_runtime', 'requested_runtime', 'expected_generation',
        'committed_generation', 'source_generation', 'failed_stage', 'error_code', 'error', 'recovery', 'recovery_action',
        'adoption', 'created_at', 'updated_at']);
    view.progress = pick(op.progress, ['stage', 'index', 'total', 'maintenance_since', 'maintenance_deadline']);
    view.dispositions = pick(op.dispositions, ['staged', 'transferred', 'blocked', 'ambiguous']);
    const blockers = pick(op.blockers, ['count', 'oldest_age_seconds', 'action']);
    if (blockers) {
        blockers.by_reason = Object.fromEntries(Object.entries(op.blockers.by_reason || {})
            .filter(([reason, count]) => /^[a-z_]{1,64}$/.test(reason) && Number.isInteger(count)));
    }
    view.blockers = blockers;
    return view;
}

export function statusView(raw) {
    if (!raw || typeof raw !== 'object' || !RUNTIMES.includes(raw.effective_runtime)) return null;
    const view = pick(raw, ['effective_runtime', 'intended_runtime', 'requested_runtime', 'generation', 'maintenance',
        'switch_available', 'unavailable_reason']);
    view.operation = operationView(raw.operation);
    view.active = operationView(raw.active);
    return view;
}

/** The phase the page shows for a server status. */
export function phaseOf(status) {
    const op = status?.active || status?.operation;
    if (!op) return 'idle';
    if (op.status === 'succeeded') return 'completed';
    if (op.status === 'recovery_required') return 'recovery_required';
    if (op.blockers?.count > 0) return 'blocked';
    if (op.status === 'failed') return 'failed';
    return ACTIVE.includes(op.status) ? 'in_progress' : 'idle';
}

/** True while the server runs a switch: a new request would only conflict with it. */
export function switchRunning(status) {
    return ACTIVE.includes(status?.active?.status);
}

export function createRuntimeStore({fetcher, readToken, pollInterval = 4000, endpoint = RUNTIME_ENDPOINT}) {
    // The controller's status socket lives on the same origin as the API.
    const origin = /^https?:\/\//i.test(endpoint) ? new URL(endpoint).origin : '';
    let current = {phase: 'idle', status: null, error: '', reachable: true, denied: false};
    const state = writable(current);
    let submitting = false;
    let sequence = 0;
    let timer = null;
    let polling = false;

    function publish(changes) {
        current = {...current, ...changes};
        current.phase = submitting ? 'submitting' : phaseOf(current.status);
        state.set(current);
    }

    const headers = (extra = {}) => {
        const token = readToken();
        return {...(token ? {Authorization: `Bearer ${token}`} : {}), ...extra};
    };

    async function refresh() {
        const id = ++sequence;
        let response = null;
        try {
            response = await fetcher(endpoint, {method: 'GET', cache: 'no-store', headers: headers()});
        } catch { /* The API stops during the switch; ask the status socket below. */ }
        let result;
        if (response && [401, 403].includes(response.status)) result = {kind: 'denied'};
        else if (response?.ok) {
            const status = statusView(await response.json().catch(() => null));
            result = status ? {kind: 'owner', status, reachable: true} : {kind: 'unavailable'};
        } else {
            // Neither application API may be up: the controller still answers.
            const independent = await readIndependentStatus(readToken(), fetcher, origin);
            const status = independent.kind === 'owner' ? statusView(independent.status.runtime) : null;
            result = independent.kind === 'denied' ? independent
                : status ? {kind: 'owner', status, reachable: false} : {kind: 'unavailable'};
        }
        if (id !== sequence) return current;
        // Revoked access: do not keep showing the private operation.
        if (result.kind === 'denied') publish({status: null, reachable: true, denied: true});
        // Keep the last known progress through a complete outage.
        else if (result.kind === 'unavailable') publish({reachable: false});
        else publish({status: result.status, reachable: result.reachable, denied: false});
        schedule();
        return current;
    }

    /**
     * Requests the switch once, with the generation last read from the server
     * and a fresh idempotency key. Whatever the reply, the page then follows
     * the server's own operation. Nothing else is sent until that status read
     * settles, and never while the server holds an operation (running or
     * waiting for recovery).
     */
    async function submit(target, password) {
        if (submitting || !current.status?.switch_available || current.status.active || !RUNTIMES.includes(target)) {
            return {done: false, error: ''};
        }
        submitting = true;
        publish({error: ''});
        const body = {target, expected_generation: current.status.generation, idempotency_key: uuidv4(), password};
        let response = null;
        let payload = {};
        try {
            response = await fetcher(endpoint, {method: 'POST', cache: 'no-store', body: JSON.stringify(body),
                headers: headers({'Content-Type': 'application/json'})});
            payload = await response.json().catch(() => ({}));
        } catch { /* The reply can be lost while the API stops: the status shows whether it was accepted. */ }
        // A wrong password changes nothing on the server: ask for it again.
        if (response?.status === 400 && payload?.error === WRONG_PASSWORD) {
            submitting = false;
            publish({});
            return {done: false, error: payload.error};
        }
        const error = !response || response.ok ? '' : payload?.error || 'Cambio non avviato.';
        publish({error});
        await refresh();
        submitting = false;
        // Without a reply only the server's status tells whether it started.
        const op = current.status?.active || current.status?.operation;
        const ours = op?.requested_runtime === target && op.expected_generation === body.expected_generation;
        publish(response || ours ? {} : {error: 'Nessuna risposta dal server: controlla qui lo stato del cambio prima di riprovare.'});
        return {done: true, error: current.error};
    }

    // The next read follows the latest status: often while an operation is
    // open or the API is down, otherwise at the slower idle pace.
    function schedule() {
        clearTimeout(timer);
        if (!polling) return;
        const busy = !!current.status?.active || !current.reachable || current.status?.maintenance;
        timer = setTimeout(refresh, busy ? pollInterval : pollInterval * 4);
    }

    function start() {
        if (polling) return;
        polling = true;
        schedule();
    }

    function stop() {
        polling = false;
        clearTimeout(timer);
        timer = null;
        sequence++;
    }

    return {subscribe: state.subscribe, refresh, submit, start, stop};
}
