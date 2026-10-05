// Real seed8 baseline: archive/consult/restore Sara and persist only the automatic preference.
// Scheduler execution, invoice deletion and course-related cascades remain separate gaps.
import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {organizationMaintenanceAuthoredWorkflows} from '../../../../docs/manuale/organization-maintenance-authored-workflows.mjs';
const spec = organizationMaintenanceAuthoredWorkflows['archive-maintenance'];
await scenario({id: 'archive-maintenance', prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const subscriptionId = input.subscription_ids[2], paymentId = input.payment_ids[2];
        const json = async (route, client = api) => {
            const response = await client(route); expect(response.status()).toBe(200); return response.json();
        };
        const activeMembers = async () => Object.values((await json('subscription/list?pagination[perpage]=100')).data).sort((a, b) => a.subscription_id.localeCompare(b.subscription_id));
        const archivedMembers = async () => Object.values((await json('subscription/list/archived?pagination[perpage]=100')).data);
        const payments = async archived => (await json(`payment/list?mode=compressed&query[archived]=${archived ? 'True' : 'False'}`)).data.sort((a, b) => a.payment_id.localeCompare(b.payment_id));
        const originalMembers = await activeMembers(), originalPayments = await payments(false);
        const originalSettings = (await json('profile/settings')).settings;
        const receipts = async () => Object.values((await json('invoice/list?pagination[perpage]=100')).data);
        const enrollments = async () => Object.values((await json(`course-subscriptions/list?course_id=${input.course_id}`)).data);
        const originalReceipts = await receipts(), originalEnrollments = await enrollments();
        expect(originalReceipts).toHaveLength(0); expect(originalEnrollments).toHaveLength(0);
        expect(originalSettings.auto_archive).toBe(false);
        expect(originalMembers).toHaveLength(3);
        expect(await archivedMembers()).toHaveLength(0);
        expect(await payments(true)).toHaveLength(0);
        expect(originalPayments.find(row => row.payment_id === paymentId).paid).toBe(false);
        const take = async (number, checkpoint, target = page, locator) => {
            await expect(target.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            await capture(target, number, checkpoint, locator);
        };
        const requestAction = async (route, button, method = 'POST') => {
            const received = page.waitForResponse(response => new URL(response.url()).pathname === '/api/' + route
                && response.request().method() === method);
            await button.click(); expect((await received).status()).toBe(200);
        };
        const subscriptionRow = target => target.locator('[data-row]').filter({hasText: /Sara\s+Conti/i});
        const autoArchiveField = target => target.locator('.form-group').filter({has: target.locator('label').filter({hasText: /^Archivia Iscrizioni Automaticamente$/})}).locator('input[type="checkbox"]');
        const saveSettings = () => requestAction('profile/settings', page.locator('#bkn_form_password_update_submit'));
        try {
            await open('Organizzazione', '/#/members/list');
            await expect(subscriptionRow(page)).toBeVisible();
            await subscriptionRow(page).locator('label.checkbox').click();
            await expect(page.locator('#bkn_datatable_archive_selected')).toBeEnabled();
            await take(1, 'selected-unpaid-member-before-archiving');
            await page.locator('#bkn_datatable_archive_selected').click();
            await expect(page.getByText('Vuoi archiviare 1 soci?', {exact: true})).toBeVisible();
            await take(2, 'member-archive-confirmation-and-cancel', page, page.locator('.swal2-popup'));
            await page.getByRole('button', {name: 'Annulla', exact: true}).click();
            expect(await activeMembers()).toEqual(originalMembers);
            expect(await payments(false)).toEqual(originalPayments);
            await page.locator('#bkn_datatable_archive_selected').click();
            await requestAction(`subscription/${subscriptionId}/archive`, page.getByRole('button', {name: 'Archivia selezionati', exact: true}));
            await expect(subscriptionRow(page)).toHaveCount(0);
            await page.reload();
            await expect(page.locator('[data-row]')).toHaveCount(2);
            expect((await activeMembers()).some(row => row.subscription_id === subscriptionId)).toBe(false);
            expect((await payments(true)).map(row => row.payment_id)).toEqual([paymentId]);
            expect((await payments(false)).map(row => row.payment_id).sort()).toEqual(input.payment_ids.slice(0, 2).sort());
            await take(3, 'active-members-after-archive-and-reload');
            await page.getByRole('button', {name: 'Archivio', exact: true}).click();
            await page.reload();
            await expect(subscriptionRow(page)).toBeVisible();
            expect((await archivedMembers()).map(row => row.subscription_id)).toEqual([subscriptionId]);
            await take(4, 'archived-member-and-restore-action-after-reload');
            await page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}).fill('Sara');
            await expect(page.locator('[data-row]')).toHaveCount(1);
            await take(5, 'archive-search-finds-selected-registration');
            await page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}).fill('NessunRisultatoManuale');
            await expect(page.locator('[data-row]')).toHaveCount(0);
            await page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}).fill('');
            await expect(subscriptionRow(page)).toBeVisible();
            await open('Contabilità', '/#/payment/list');
            await page.getByRole('button', {name: 'Archivio', exact: true}).click();
            await expect(page.locator('[data-row]').filter({hasText: /Sara\s+Conti/i})).toBeVisible();
            await take(6, 'unpaid-member-payment-in-separate-payment-archive');
            const reader = await actor('reader');
            await reader.open('Organizzazione', '/#/members/list');
            await reader.page.getByRole('button', {name: 'Archivio', exact: true}).click();
            await expect(subscriptionRow(reader.page)).toBeVisible();
            await expect(subscriptionRow(reader.page).getByRole('button', {name: "Rimuovi dall'archivio", exact: true})).toBeDisabled();
            const deniedRestore = await reader.api(`subscription/${subscriptionId}/archive`, {method: 'POST'});
            expect(deniedRestore.status()).toBe(403);
            expect((await archivedMembers()).map(row => row.subscription_id)).toEqual([subscriptionId]);
            await take(7, 'reader-consults-archive-with-disabled-restore', reader.page);
            await open('Organizzazione', '/#/members/list');
            await page.getByRole('button', {name: 'Archivio', exact: true}).click();
            await subscriptionRow(page).getByRole('button', {name: "Rimuovi dall'archivio", exact: true}).click();
            await expect(page.getByText("Vuoi spostare l'atleta nel libro soci?", {exact: true})).toBeVisible();
            await take(8, 'member-restore-confirmation', page, page.locator('.swal2-popup'));
            await requestAction(`subscription/${subscriptionId}/archive`, page.getByRole('button', {name: 'Sposta nel libro soci', exact: true}));
            await page.getByRole('button', {name: 'Tesserati', exact: true}).click();
            await page.reload(); await expect(subscriptionRow(page)).toBeVisible();
            // Restoring the member leaves its unpaid payment archived. Only
            // the serializer's active-payment summary changes; assert every
            // other field and every unrelated member exactly as before.
            expect(await activeMembers()).toEqual(originalMembers.map(member => member.subscription_id === subscriptionId
                ? {...member, all_payments_paid: true, payments_info: {total: 0, to_be_paid: 0}} : member));
            expect((await payments(true)).map(row => row.payment_id)).toEqual([paymentId]);
            await take(9, 'restored-member-and-payment-still-archived');
            // Restore the affected payment separately through its actual visible control.
            await open('Contabilità', '/#/payment/list');
            await page.getByRole('button', {name: 'Archivio', exact: true}).click();
            const paymentRow = page.locator('[data-row]').filter({hasText: /Sara\s+Conti/i});
            await paymentRow.getByRole('button', {name: "Rimuovi dall'archivio", exact: true}).click();
            await expect(page.getByText('Vuoi spostare il pagamento nella sezione principale?', {exact: true})).toBeVisible();
            await requestAction(`payment/${paymentId}/archive`, page.getByRole('button', {name: 'Sposta', exact: true}));
            expect(await payments(false)).toEqual(originalPayments);
            expect(await payments(true)).toHaveLength(0);
            expect(await activeMembers()).toEqual(originalMembers);
            // Save preference only; this does not invoke Celery or fake its result.
            await open('Impostazioni', '/#/profile'); await page.getByText('Generali', {exact: true}).click();
            await expect(page.locator('#balance-sheet')).toHaveValue(originalSettings.balance_sheet_year);
            await take(10, 'fiscal-start-before-automatic-archive-preference');
            await expect(autoArchiveField(page)).not.toBeChecked();
            await setCheckbox(autoArchiveField(page), true);
            await autoArchiveField(page).locator('xpath=ancestor::label[1]').scrollIntoViewIfNeeded();
            await take(11, 'automatic-archive-enabled-before-save');
            await saveSettings(); await page.reload();
            await expect(autoArchiveField(page)).toBeChecked();
            await autoArchiveField(page).locator('xpath=ancestor::label[1]').scrollIntoViewIfNeeded();
            expect((await json('profile/settings')).settings).toEqual({...originalSettings, auto_archive: true});
            expect(await activeMembers()).toEqual(originalMembers);
            expect(await payments(false)).toEqual(originalPayments);
            await take(12, 'automatic-archive-preference-persists-after-reload');
            await reader.open('Impostazioni', '/#/profile'); await reader.page.getByText('Generali', {exact: true}).click();
            await expect(autoArchiveField(reader.page)).toBeChecked();
            await expect(autoArchiveField(reader.page)).toBeDisabled();
            const deniedSetting = await reader.api('profile/settings', {method: 'POST', data: {...originalSettings, auto_archive: false}});
            expect(deniedSetting.status()).toBe(403);
            expect((await json('profile/settings')).settings.auto_archive).toBe(true);
            await autoArchiveField(reader.page).locator('xpath=ancestor::label[1]').scrollIntoViewIfNeeded();
            await take(13, 'reader-can-consult-automatic-preference-without-changing-it', reader.page);
            await setCheckbox(autoArchiveField(page), false); await saveSettings(); await page.reload();
            await expect(autoArchiveField(page)).not.toBeChecked();
            expect((await json('profile/settings')).settings).toEqual(originalSettings);
            await autoArchiveField(page).locator('xpath=ancestor::label[1]').scrollIntoViewIfNeeded();
            await take(14, 'automatic-archive-preference-restored-and-baseline-preserved');
            report.archive_maintenance_authored_workflow = {
                archive_cancel_preserved_state: true, member_archived_after_reload: true,
                archive_search_and_clear_worked: true, separate_unpaid_payment_archived: true,
                other_paid_payments_preserved: true, reader_restore_status: deniedRestore.status(),
                member_restored_after_reload: true, restore_does_not_restore_payment: true,
                payment_restored_separately: true, automatic_preference_saved_after_reload: true,
                fiscal_settings_preserved: true, saving_preference_does_not_move_members: true,
                reader_automatic_setting_status: deniedSetting.status(), baseline_records_and_settings_restored: true,
                baseline_receipts_and_course_enrollments_preserved: true,
            };
            report.external_gaps = [{operation: 'automatic-archive-scheduler-execution', status: 'needs_external_verification',
                reason: 'Only saving and reopening the preference was exercised; scheduled fiscal-year processing was not executed.'},
                {operation: 'archive-course-enrollment-and-invoice-cascades', status: 'needs_external_verification',
                    reason: 'Sara has no course enrollments and no issued invoice in this example; their archival/deletion effects are not proved.'}];
            report.checks = ['actual UI confirmation cancellation leaves data unchanged', 'archive and consultation persist after reload',
                'search and separate payment archive show the affected unpaid record', 'reader restore and settings writes denied',
                'restoring member leaves payment archived; payment restored separately', 'automatic option saves independently of actual scheduler',
                'baseline member/payment/settings values restored'];
        } finally {
            // Cleanup checks real current state before toggling; never blindly invert it.
            if ((await archivedMembers()).some(row => row.subscription_id === subscriptionId)) {
                expect((await api(`subscription/${subscriptionId}/archive`, {method: 'POST'})).status()).toBe(200);
            }
            if ((await payments(true)).some(row => row.payment_id === paymentId)) {
                expect((await api(`payment/${paymentId}/archive`, {method: 'POST'})).status()).toBe(200);
            }
            if (JSON.stringify((await json('profile/settings')).settings) !== JSON.stringify(originalSettings)) {
                expect((await api('profile/settings', {method: 'POST', data: originalSettings})).status()).toBe(200);
            }
            expect(await activeMembers()).toEqual(originalMembers);
            expect(await payments(false)).toEqual(originalPayments);
            expect((await json('profile/settings')).settings).toEqual(originalSettings);
            expect(await receipts()).toEqual(originalReceipts);
            expect(await enrollments()).toEqual(originalEnrollments);
        }
    }});
