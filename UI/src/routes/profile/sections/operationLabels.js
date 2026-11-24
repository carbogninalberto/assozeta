// Plain Italian labels for runner operations (updates, restarts, backups, restores, runtime switches).
const STEPS = {
    update: ['checking', 'backup', 'downloading', 'migrating', 'restarting', 'health_check'],
    restart: ['checking', 'restarting', 'health_check'],
    backup: ['checking', 'backup', 'restarting'],
    restore: ['checking', 'backup', 'restoring', 'restarting', 'health_check'],
    go_tasks: ['checking', 'restarting'],
    fetch: ['checking', 'downloading'],
};

const STAGES = {
    queued: 'In coda', checking: 'Controlli iniziali', backup: 'Backup dei dati', downloading: 'Download della versione',
    migrating: 'Aggiornamento del database', restoring: 'Ripristino dei dati', restarting: 'Riavvio dei servizi',
    health_check: 'Verifica finale', completed: 'Completato', failed: 'Non riuscito',
    recovery_required: 'Ripristino necessario', recovered: 'Ripristinato',
};

// The instance runtime switch, in the controller's order (P5-STATUS).
export const RUNTIME_STAGES = {
    queued: 'In coda', preflight: 'Controlli iniziali', close_admission: 'Sospensione delle nuove attività',
    settle: 'Completamento delle attività in corso', stage_backlog: 'Trasferimento delle attività in attesa',
    stop_source: 'Arresto del runtime di origine', target_preflight: 'Verifica del runtime di destinazione',
    commit: 'Registrazione del cambio', activate: 'Attivazione', verify: 'Verifica finale', completed: 'Completato',
};
const RUNTIME_STEPS = Object.keys(RUNTIME_STAGES).slice(1, -1);

export const runtimeName = runtime => ({django: 'Django', go: 'Go'})[runtime] || '—';

// The status has no "last completed" step: before a failure point, the
// controller confirmed the step that precedes it.
export function lastCompletedStage(operation) {
    const index = RUNTIME_STEPS.indexOf(operation?.failed_stage);
    return index > 0 ? RUNTIME_STEPS[index - 1] : null;
}

export const kindOf = operation => operation?.kind || 'update';

export function operationTitle(operation) {
    const kind = kindOf(operation);
    if (kind === 'restart') return 'Riavvio dell’applicazione';
    if (kind === 'backup') return 'Backup in corso';
    if (kind === 'restore') return 'Ripristino in corso';
    if (kind === 'fetch') return 'Recupero dalla copia remota';
    if (kind === 'go_tasks') return 'Attività in background su Go';
    if (kind === 'runtime_switch') return `Cambio runtime · ${runtimeName(operation.previous_runtime)} → ${runtimeName(operation.requested_runtime)}`;
    return `Aggiornamento a ${operation.target_version}`;
}

export function stageLabel(operation) {
    if (kindOf(operation) === 'fetch' && operation.stage === 'downloading') return 'Download e decifratura';
    if (kindOf(operation) === 'runtime_switch' && RUNTIME_STAGES[operation.stage]) return RUNTIME_STAGES[operation.stage];
    return STAGES[operation?.stage] || operation?.stage || '';
}

export function operationProgress(operation) {
    if (kindOf(operation) === 'runtime_switch') {
        const total = operation.progress?.total || RUNTIME_STEPS.length;
        return {label: stageLabel(operation), step: Math.min(Math.max(operation.progress?.index || 0, 0), total), total};
    }
    const steps = STEPS[kindOf(operation)] || STEPS.update;
    const index = steps.indexOf(operation.stage);
    return {label: stageLabel(operation), step: index < 0 ? 0 : index + 1, total: index < 0 && operation.stage !== 'queued' ? 0 : steps.length};
}

// One readable line for the history list.
export function historyTitle(operation) {
    const kind = kindOf(operation);
    if (kind === 'restart') return 'Riavvio';
    if (kind === 'backup') return operation.origin === 'scheduled' ? 'Backup automatico' : 'Backup manuale';
    if (kind === 'restore') return 'Ripristino';
    if (kind === 'fetch') return 'Recupero da remoto';
    if (kind === 'go_tasks') return 'Attività su Go';
    if (kind === 'runtime_switch') return `Runtime ${runtimeName(operation.previous_runtime)} → ${runtimeName(operation.requested_runtime)}`;
    return operation.source_version ? `${operation.source_version} → ${operation.target_version}` : `Aggiornamento a ${operation.target_version}`;
}

export function historyTone(operation) {
    if (operation.stage === 'completed' || operation.stage === 'recovered') return 'ok';
    if (operation.stage === 'recovery_required') return 'warn';
    if (operation.stage === 'failed') return 'bad';
    return 'neutral';
}

// The latest operation, if it finished in the last 15 minutes: shown once as a banner.
export function recentOutcome(history = [], now = Date.now()) {
    const latest = history?.[0];
    if (!latest || !['completed', 'failed'].includes(latest.stage)) return null;
    const finished = Date.parse(latest.verified_at || latest.updated_at || latest.created_at || '');
    if (!finished || now - finished > 15 * 60000) return null;
    const ok = latest.stage === 'completed';
    const kind = kindOf(latest);
    const titles = {
        update: ok ? `Aggiornamento completato · ${latest.target_version}` : 'Aggiornamento non riuscito',
        restart: ok ? 'Riavvio completato' : 'Riavvio non riuscito',
        backup: ok ? 'Backup completato' : 'Backup non riuscito',
        restore: ok ? 'Ripristino completato' : 'Ripristino non riuscito',
        fetch: ok ? 'Backup recuperato sul server' : 'Recupero non riuscito',
        go_tasks: ok ? 'Attività su Go aggiornate' : 'Attività su Go non riuscite',
        runtime_switch: ok ? `Runtime cambiato · ${runtimeName(latest.requested_runtime)}` : 'Cambio runtime non riuscito',
    };
    return {id: latest.id, ok, title: titles[kind] || titles.update, detail: ok ? '' : latest.error || ''};
}
