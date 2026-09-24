// WebSocket events and cached HTTP snapshots may arrive out of order.
export function mergeRestoreProgress(current, incoming, operation) {
    if (!incoming || !operation || incoming.operation_id !== operation.id || !['queued', 'running'].includes(operation.state)) return current;
    if (incoming.attempt < (operation.attempts || 0)) return current;
    if (current?.operation_id === incoming.operation_id) {
        if (incoming.attempt < current.attempt) return current;
        if (incoming.attempt === current.attempt && incoming.sequence <= current.sequence) return current;
    }
    return incoming;
}
