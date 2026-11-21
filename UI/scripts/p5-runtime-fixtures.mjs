// P5-STATUS v1, shared by the Node and browser contracts.
export const stages = ['queued', 'preflight', 'close_admission', 'settle', 'stage_backlog',
    'stop_source', 'target_preflight', 'commit', 'activate', 'verify', 'completed'];
export const operationId = '00000000-0000-4000-8000-000000000501';

export function snapshot(stage = null, {source = 'django', generation = 7, failed = false, blocked = false} = {}) {
    const index = stage ? stages.indexOf(stage) : 0;
    if (index < 0) throw new Error('Unknown fixture stage ' + stage);
    const target = source === 'django' ? 'go' : 'django';
    const committed = index > stages.indexOf('commit');
    const effective = committed ? target : source;
    const completed = stage === 'completed';
    const status = failed ? (committed ? 'recovery_required' : 'failed')
        : completed ? 'succeeded' : stage === 'queued' ? 'queued' : 'running';
    const active = !!stage && ['queued', 'running', 'recovery_required'].includes(status);
    const maintenance = index >= 2 && !completed && (!failed || committed);
    const dispositions = blocked ? {staged: 17, transferred: 13, blocked: 5, ambiguous: 2}
        : {staged: 0, transferred: 0, blocked: 0, ambiguous: 0};
    const at = '2026-01-01T12:00:00Z';
    const op = stage && {
        id: operationId, kind: 'runtime_switch', actor_id: '00000000-0000-4000-8000-000000000502',
        previous_runtime: source, requested_runtime: target, expected_generation: generation,
        committed_generation: committed ? generation + 1 : null, source_generation: generation,
        status, stage: failed ? status : stage, outcome: failed ? 'failed' : completed ? 'committed' : null,
        created_at: at, updated_at: at, adoption: false, failed_stage: failed ? stage : null,
        error_code: failed ? (committed ? 'post_commit_failed' : 'transfer_blocked') : null,
        error: failed ? (committed ? 'Il nuovo runtime richiede una verifica.' : 'Trasferimento bloccato.') : null,
        recovery: failed && committed ? './bin/assozeta runtime-recover' : null,
        recovery_action: blocked ? 'reconcile' : failed ? (committed ? 'recover' : 'retry') : null,
        counts: {pending: dispositions.staged, transferred: dispositions.transferred, blocked: dispositions.blocked},
        dispositions, progress: {stage, index, total: 9, maintenance_since: maintenance ? at : null,
            maintenance_deadline: maintenance ? '2026-01-01T12:15:00Z' : null},
        blockers: blocked ? {count: 5, by_reason: {ambiguous_effect: 2, unsupported: 3}, oldest_age_seconds: 73,
            items: [{ref: 'fixture-item', reason: 'ambiguous_effect', age_seconds: 73}],
            action: 'Verifica le consegne ambigue prima di riprovare.'} : null,
    };
    return {protocol: 1, effective_runtime: effective, generation: generation + Number(committed),
        last_operation_id: committed ? operationId : null, requested_runtime: active ? target : null,
        operation: op, active: active ? op : null, intended_runtime: active ? target : effective,
        authority_error: null, recovery_command: './bin/assozeta runtime-recover',
        maintenance, switch_available: true, unavailable_reason: null};
}

export function controller(runtime) {
    return {protocol: 1, is_owner: true, available: true, active: runtime.active,
        history: runtime.operation ? [runtime.operation] : [], runtime};
}
