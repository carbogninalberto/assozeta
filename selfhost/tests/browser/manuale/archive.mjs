import {scenario, expect} from './scenario.mjs';
import {focusRegion, tableFocus} from './focus.mjs';
import {legacyWorkflows} from '../../../../docs/manuale/legacy-workflows.mjs';
const spec = legacyWorkflows['members-archive-restore'];

await scenario({id: 'members-archive-restore', actions: async ({page, api, open, actor, input, capture, report}) => {
    const subscriptionId = input.subscription_ids[2];
    const unpaidId = input.payment_ids[2];
    const payments = async archived => (await (await api(`payment/list?mode=compressed&query[archived]=${archived ? 'True' : 'False'}`)).json()).data;
    expect((await payments(false)).find(payment => payment.payment_id === unpaidId).paid).toBe(false);
    await open('Organizzazione', '/#/members/list');
    await expect(page.locator('[data-row]')).toHaveCount(3);
    const row = page.locator('[data-row]').filter({hasText: /Sara Conti/});
    await row.locator('label.checkbox').click();
    await expect(page.locator('#bkn_datatable_archive_selected')).toBeEnabled();
    await capture(page, 'select-members-to-archive', focusRegion(
        page.locator('#bkn_datatable_archive_selected'),
        row.locator('[data-field="subscription_id"]'),
        row.locator('[data-field="status_flag"]')));
    await page.locator('#bkn_datatable_archive_selected').click();
    await expect(page.getByText('Vuoi archiviare 1 soci?', {exact: true})).toBeVisible();
    const confirmation = page.locator('.swal2-popup');
    const settleConfirmation = () => confirmation.evaluate(async element => {
        const animations = element.getAnimations({subtree: true}).filter(animation =>
            Number.isFinite(animation.effect.getComputedTiming().endTime));
        await Promise.all(animations.map(animation => animation.finished.catch(() => {})));
    });
    await settleConfirmation();
    await capture(page, 'archive-confirmation', confirmation);
    const archived = page.waitForResponse(response => response.url().endsWith(`/subscription/${subscriptionId}/archive`));
    await page.getByRole('button', {name: 'Archivia selezionati', exact: true}).click();
    expect((await archived).status()).toBe(200);
    await expect(page.locator('[data-row]')).toHaveCount(2);
    expect((await payments(false)).some(payment => payment.payment_id === unpaidId)).toBe(false);
    expect((await payments(true)).some(payment => payment.payment_id === unpaidId)).toBe(true);
    // A paid member's payment is left in the active list by this operation.
    expect((await payments(false)).some(payment => payment.payment_id === input.payment_ids[0])).toBe(true);
    await page.getByRole('button', {name: 'Archivio', exact: true}).click();
    await expect(page.locator('[data-row]')).toHaveCount(1);
    const archivedRow = page.locator('[data-row]').filter({hasText: 'Sara Conti'});
    await expect(archivedRow).toBeVisible();
    await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
    await capture(page, 'archived-member-and-restore-action', focusRegion(
        archivedRow.getByRole('button', {name: "Rimuovi dall'archivio"}),
        page.locator('.datatable-head [data-field="user"]'),
        archivedRow.locator('[data-field="user"]')));
    await archivedRow.getByRole('button', {name: "Rimuovi dall'archivio"}).click();
    await expect(page.getByText("Vuoi spostare l'atleta nel libro soci?", {exact: true})).toBeVisible();
    await settleConfirmation();
    await capture(page, 'restore-confirmation', confirmation);
    const restored = page.waitForResponse(response => response.url().endsWith(`/subscription/${subscriptionId}/archive`));
    await page.getByRole('button', {name: 'Sposta nel libro soci', exact: true}).click();
    expect((await restored).status()).toBe(200);
    await expect(page.locator('[data-row]')).toHaveCount(0);
    await page.getByRole('button', {name: 'Tesserati', exact: true}).click();
    await page.reload();
    await expect(page.locator('[data-row]')).toHaveCount(3);
    await expect(page.locator('[data-row]').filter({hasText: 'Sara Conti'})).toBeVisible();
    await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
    await capture(page, 'restored-member-persists-after-reload', tableFocus(page, ['associate', 'status_flag'],
        page.locator('[data-row]').filter({hasText: 'Sara Conti'})));
    // The restore branch toggles only the subscription; unpaid payments remain archived.
    expect((await payments(true)).some(payment => payment.payment_id === unpaidId)).toBe(true);
    const reader = await actor('reader');
    await reader.open('Organizzazione', '/#/members/list');
    await expect(reader.page.locator('[data-row]')).toHaveCount(3);
    await reader.page.locator('[data-row]').filter({hasText: 'Sara Conti'}).locator('label.checkbox').click();
    await expect(reader.page.locator('#bkn_datatable_archive_selected')).toBeDisabled();
    expect((await reader.api(`subscription/${subscriptionId}/archive`, {method: 'POST'})).status()).toBe(403);
    const active = await api('subscription/list');
    expect(active.ok()).toBeTruthy();
    expect(Object.values((await active.json()).data).some(entry => entry.subscription_id === subscriptionId)).toBe(true);
    report.archive_workflow = {
        archived_member_removed: true, unpaid_payment_archived: true, paid_payment_preserved: true,
        restored_member_persists: true, restoring_does_not_restore_payment: true, reader_archive_status: 403,
    };
    report.checks = ['real UI selection and archive confirmation', 'archived member moves out of active list',
        'unpaid linked payment is archived; another paid payment remains active',
        'restore action and persisted active member after reload', 'restoring a member does not restore its unpaid payment',
        'restricted collaborator sees disabled archive action; backend denies archive without changing state'];
}});
