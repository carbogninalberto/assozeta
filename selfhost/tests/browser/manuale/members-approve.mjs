// Prepare a signed request through the real wizard, then exercise approval.
import {scenario, expect} from './scenario.mjs';
import {memberApprovalSources} from './member-sources.mjs';
import {createMember} from './member-create-flow.mjs';

await scenario({id: 'members-approve', prefix: 'images/libro-soci/approvazione', sources: memberApprovalSources,
    actions: async context => {
        const {page, api, open, actor, capture, report} = context;
        // No copied/fabricated screenshots from preparation. This scenario's
        // four named frames cover only the distinct approval operation.
        await createMember({...context, capture: async () => {}});
        const before = Object.values((await (await api('subscription/list?query[generalSearch]=Marta')).json()).data)[0];
        expect(before.status_flag).toBe(2);
        const row = page.locator('[data-row]').filter({hasText: 'Marta Neri'});
        await expect(row).toContainText('in attesa');
        await capture(page, 1, 'signed-pending-request-before-approval');
        await row.locator('button:is([title="Approva Iscrizione"],[data-original-title="Approva Iscrizione"])').click();
        const popup = page.locator('.swal2-popup');
        await expect(popup).toContainText("Vuoi accettare l'iscrizione?");
        await capture(page, 2, 'confirm-single-registration-approval', popup);
        const response = page.waitForResponse(response =>
            new URL(response.url()).pathname === `/api/subscription/${before.subscription_id}/approve`
            && response.request().method() === 'POST');
        await popup.getByRole('button', {name: 'Approva', exact: true}).click();
        expect((await response).status()).toBe(200);
        await expect(row).toContainText('accettata');
        await page.reload();
        await expect(row).toContainText('accettata');
        await expect(row.locator('button:is([title="Approva Iscrizione"],[data-original-title="Approva Iscrizione"])')).toBeDisabled();
        const after = Object.values((await (await api('subscription/list?query[generalSearch]=Marta')).json()).data)[0];
        expect(after.subscription_id).toBe(before.subscription_id);
        expect(after.status_flag).toBe(4);
        expect(after.acceptance_date).toBeTruthy();
        expect(after.payment.payment_id).toBe(before.payment.payment_id);
        expect(after.payment.amount).toBe(25);
        expect(after.payment.paid).toBe(false);
        await capture(page, 3, 'accepted-registration-persists-after-reload');
        const reader = await actor('reader');
        await reader.open('Organizzazione', '/#/members/list');
        const readerRow = reader.page.locator('[data-row]').filter({hasText: 'Marta Neri'});
        await expect(readerRow).toBeVisible();
        await expect(readerRow.locator('button:is([title="Approva Iscrizione"],[data-original-title="Approva Iscrizione"])')).toBeDisabled();
        const forbidden = await reader.api(`subscription/${after.subscription_id}/approve`, {method: 'POST'});
        expect(forbidden.status()).toBe(403);
        const repeated = await api(`subscription/${after.subscription_id}/approve`, {method: 'POST'});
        expect(repeated.status()).toBe(403);
        await open('Pagamenti', '/#/payment/list');
        const paymentRow = page.locator('[data-row]').filter({hasText: 'Marta Neri'});
        await expect(paymentRow).toBeVisible();
        await expect(paymentRow).toContainText('In attesa');
        await capture(page, 4, 'registration-fee-remains-unpaid-after-approval');
        const unchanged = Object.values((await (await api('subscription/list?query[generalSearch]=Marta')).json()).data)[0];
        expect(unchanged.status_flag).toBe(4);
        expect(unchanged.acceptance_date).toBe(after.acceptance_date);
        report.member_approval = {initial_status: before.status_flag, final_status: after.status_flag,
            acceptance_date_saved: Boolean(after.acceptance_date), persisted_after_reload: true,
            payment_id_preserved: after.payment.payment_id === before.payment.payment_id,
            payment_amount: after.payment.amount, payment_paid: after.payment.paid,
            reader_approve_status: forbidden.status(), repeat_approve_status: repeated.status(),
            denial_left_state_unchanged: unchanged.acceptance_date === after.acceptance_date};
        report.checks.push('actual approval confirmation and POST', 'accepted state and date persisted after reload',
            'approval does not collect the example unpaid fee', 'read-only and repeat approval rejected without changing saved state');
    }});
