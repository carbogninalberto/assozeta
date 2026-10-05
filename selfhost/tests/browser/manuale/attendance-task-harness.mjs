// Private fixture command invocation; production task results, never synthetic API replies.
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
export function attendanceHarness(action, input) {
    const run = process.env.ASSOZETA_MANUAL_RUN;
    if (!run) throw Error('Owned manual run required');
    const state = JSON.parse(fs.readFileSync(path.join(run, 'run.json')));
    const args = ['compose', '--env-file', state.env_file, '--project-name', state.project,
        '-f', path.join(state.application, 'selfhost/compose.dev.yml'), '-f', state.override,
        'run', '--rm', '--no-deps', '--user', `${process.getuid()}:${process.getgid()}`,
        'api', 'python', 'manage.py', 'run_manuale_attendance', '--action', action,
        '--reference-date', input.reference_date];
    const log = fs.openSync(path.join(run, 'execution.log'), 'a', 0o600);
    try {execFileSync('docker', args, {cwd: state.application, stdio: ['ignore', log, log], timeout: 120000});}
    finally {fs.closeSync(log);}
    if (action === 'cleanup') return null;
    return JSON.parse(fs.readFileSync(path.join(run, action === 'prepare' ? 'attendance-case.json' : 'attendance-result.json')));
}
export async function settleAttendanceCases(api, data, input, expect) {
    for (const id of data.settle_payment_ids) {
        const result = await api(`payment/${id}/approve`, {method: 'POST', data: {
            payment_date: input.reference_date, generate_invoice: false, send_receipt_email: false}});
        expect(result.status()).toBe(200);
        const payment = (await result.json()).data.payment;
        expect(payment.paid).toBe(true);
        expect(Number(payment.amount)).toBe(50);
    }
}
