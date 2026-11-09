// Plain Italian labels for runner operations (updates, restarts, backups, restores).
const STEPS = {
    update: ['checking', 'backup', 'downloading', 'migrating', 'restarting', 'health_check'],
    restart: ['checking', 'restarting', 'health_check'],
    backup: ['checking', 'backup', 'restarting'],
    restore: ['checking', 'backup', 'restoring', 'restarting', 'health_check'],
    fetch: ['checking', 'downloading'],
};

const STAGES = {
    queued: 'In coda', checking: 'Controlli iniziali', backup: 'Backup dei dati', downloading: 'Download della versione',
    migrating: 'Aggiornamento del database', restoring: 'Ripristino dei dati', restarting: 'Riavvio dei servizi',
    health_check: 'Verifica finale', completed: 'Completato', failed: 'Non riuscito',
    recovery_required: 'Ripristino necessario', recovered: 'Ripristinato',
};

export const kindOf = operation => operation?.kind || 'update';

export function operationTitle(operation) {
    const kind = kindOf(operation);
    if (kind === 'restart') return 'Riavvio dell’applicazione';
    if (kind === 'backup') return 'Backup in corso';
    if (kind === 'restore') return 'Ripristino in corso';
    if (kind === 'fetch') return 'Recupero dalla copia remota';
    return `Aggiornamento a ${operation.target_version}`;
}

export function stageLabel(operation) {
    if (kindOf(operation) === 'fetch' && operation.stage === 'downloading') return 'Download e decifratura';
    return STAGES[operation?.stage] || operation?.stage || '';
}

export function operationProgress(operation) {
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
    };
    return {id: latest.id, ok, title: titles[kind] || titles.update, detail: ok ? '' : latest.error || ''};
}
