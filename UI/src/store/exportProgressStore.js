import {writable} from 'svelte/store';

export const initialExportProgressState = {
    active: false,
    taskId: null,
    status: null,
    progress: null,
    updatedAt: null,
    error: null,
    document: null,
    announceTerminal: false,
};

function createExportProgressStore() {
    const {subscribe, set, update} = writable({...initialExportProgressState});

    function apply(message, active = true, announceTerminal = false) {
        if (!message?.task_id) return;
        update(current => {
            const incomingTime = Date.parse(message.updated_at || '') || 0;
            const currentTime = Date.parse(current.updatedAt || '') || 0;
            const sameTask = current.taskId === message.task_id;
            const currentTerminal = ['SUCCESS', 'FAILURE'].includes(current.status);
            const incomingTerminal = ['SUCCESS', 'FAILURE'].includes(message.status);

            // All backend snapshots carry an authoritative timestamp. Reject a
            // delayed event from an older task as well as stale same-task data.
            if (current.taskId && !sameTask && incomingTime && incomingTime < currentTime) {
                return current;
            }
            if (sameTask && incomingTime < currentTime) return current;
            if (sameTask && currentTerminal && !incomingTerminal) return current;
            if (sameTask && !incomingTerminal && (message.progress?.percent ?? 0) < (current.progress?.percent ?? 0))
                return current;

            return {
                active,
                taskId: message.task_id,
                status: message.status || (active ? 'PROGRESS' : current.status),
                progress: message.progress || current.progress,
                updatedAt: message.updated_at || current.updatedAt,
                error: message.error || null,
                document: message.document || null,
                announceTerminal: incomingTerminal && announceTerminal,
            };
        });
    }

    return {
        subscribe,
        applyProgress: message => apply(message, true),
        applyCompleted: (message, announceTerminal = true) =>
            apply({...message, status: 'SUCCESS'}, false, announceTerminal),
        applyFailed: (message, announceTerminal = true) =>
            apply({...message, status: 'FAILURE'}, false, announceTerminal),
        applySnapshot: message => apply(message, true),
        reset: () => set({...initialExportProgressState}),
    };
}

export const exportProgress = createExportProgressStore();
