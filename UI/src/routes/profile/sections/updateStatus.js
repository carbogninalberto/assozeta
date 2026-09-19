// An unavailable controller cannot authoritatively clear a durable operation.
export function mergeRunnerStatus(previous, incoming) {
    if (incoming.available) return incoming;
    return {...incoming, active: previous.active, history: previous.history};
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
