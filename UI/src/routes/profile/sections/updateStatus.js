// An unavailable controller cannot authoritatively clear a durable operation.
export function mergeRunnerStatus(previous, incoming) {
    if (incoming.available) return incoming;
    return {...incoming, active: previous.active, history: previous.history};
}

export function restartResult(status, requestId, startedAt, now = Date.now()) {
    const records = [status?.active, ...(status?.history || [])].filter(Boolean);
    const operation = status?.available && records.find(item => item.kind === 'restart' && item.request_id === requestId);
    if (operation?.status === 'succeeded' && operation.stage === 'completed' && operation.verified_at) return {phase: 'ready', operation};
    if (operation && ['failed', 'recovery_required'].includes(operation.status)) return {phase: 'failed', operation};
    if (now - startedAt >= 10 * 60 * 1000) return {phase: 'timeout', operation};
    return {phase: 'waiting', operation};
}

// A completed operation still needs its new version/catalog loaded. Keep that
// work pending if another lifecycle operation temporarily takes the API down.
export function createCompletionRefresh() {
    let pending = false;
    return {
        observe(previous, incoming) {
            if (incoming.available && previous.active && !incoming.active) pending = true;
        },
        async run(loadInfo, loadCatalog) {
            if (!pending) return;
            await loadInfo();
            pending = await loadCatalog() === false;
        },
    };
}
