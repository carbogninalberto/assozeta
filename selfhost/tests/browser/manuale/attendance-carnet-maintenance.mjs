// Reuse real enrollment/carnet preparation; assert each additional saved action.
import {scenario, expect} from './scenario.mjs';
import {runAttendanceCarnet} from './attendance-carnet.mjs';
import {openGiulia} from './member-profile-sources.mjs';
import {attendanceCarnetAuthoredWorkflows} from '../../../../docs/manuale/attendance-carnet-authored-workflows.mjs';
// Seeded and frozen-clock rows share timestamps (or the list is unordered), so compare
// record sets: identical rows and contents, independent of physical row order.
const records = rows => (Array.isArray(rows) ? rows.map(row => JSON.stringify(row))
    : Object.entries(rows).map(entry => JSON.stringify(entry))).sort();
const id = 'attendance-carnet-maintenance', spec = attendanceCarnetAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async scenarioContext => {
        const {page, api, open, actor, input, capture, report} = scenarioContext;
        const prepared = await runAttendanceCarnet({...scenarioContext, capture: async () => {}});
        const {carnet, registration, event, courseUrl, usageHref, carnetInfo, get, listPayments, openAttendance} = prepared;
        report.fixture_preparation = {workflow: 'attendance-carnet-manual', captures_reused: false,
            course: input.course_id, member: input.subscription_ids[0], lessons_restored_before_maintenance: 5};
        const original = (await carnetInfo()).subscriptions[0], uid = original.carnet_subscription_id;
        const initialPayments = await listPayments();
        const take = async (checkpoint, browserPage = page, locator) => {
            await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            const n = spec.checkpoints.findIndex(item => item.id === checkpoint) + 1; expect(n).toBeGreaterThan(0);
            await capture(browserPage, n, checkpoint, locator);
        };
        const facts = {}, proof = (key, value) => {expect(value, key).toEqual(spec.outcome.expected[key]); facts[key] = value;};
        const mark = async present => {
            await page.goto(input.origin + courseUrl + '/attendance');
            const dialog = await openAttendance();
            const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/course/${input.course_id}/attendees/${event.attendance_day_id}/update`
                && r.request().method() === 'POST');
            await expect(dialog.locator('input[type="checkbox"]')).toBeChecked({checked: !present});
            await dialog.locator('input[type="checkbox"]').locator('..').click();
            expect((await response).status()).toBe(200);
            await dialog.getByText('Chiudi', {exact: true}).click();
        };
        const history = suffix => get(`subscription/${input.subscription_ids[0]}/attendance?course=${suffix || 'all'}`);
        await mark(true);
        let drawer = await openGiulia({page, open, expect});
        await drawer.locator('.nav-link').filter({hasText: /^\s*Registro Presenze\s*$/}).click();
        await expect(drawer.locator('#bkn_datatable_attendance [data-row]')).toHaveCount(1);
        await expect(drawer.locator('#bkn_datatable_attendance')).toContainText(event.title);
        const allHistory = await history();
        proof('personal_history_saved_presences', allHistory.attendance_days.length);
        proof('personal_history_last_30_days', allHistory.stats.total_attendance_last_30_days);
        expect(allHistory.stats.total_attendance).toBe(1);
        expect(allHistory.attendance_days[0].attendance_day_id).toBe(event.attendance_day_id);
        await take('personal-attendance-history-all', page, drawer);
        await drawer.getByRole('textbox', {name: 'Corso', exact: true}).click();
        await page.locator('.list-item:visible').filter({hasText: /^\s*Ginnastica per tutti\s*$/i}).click();
        const filtered = await history(input.course_id); expect(filtered.attendance_days).toEqual(allHistory.attendance_days);
        proof('personal_history_course_filter_matches', true);
        await take('personal-attendance-history-course-filter', page, drawer);
        const search = drawer.locator('#bkn_datatable_attendance_search_query');
        await search.fill('nessuna corrispondenza'); await search.press('Enter');
        await expect(drawer.locator('#bkn_datatable_attendance [data-row]')).toHaveCount(0);
        proof('personal_history_search_no_match_rows', 0);
        await take('personal-attendance-history-search', page, drawer);
        await search.fill(''); await search.press('Enter');
        await expect(drawer.locator('#bkn_datatable_attendance [data-row]')).toHaveCount(1);
        await page.keyboard.press('Escape'); await expect(drawer).toHaveCount(0);
        await mark(false);
        proof('personal_history_correction_removes_presence', (await history()).attendance_days.length === 0);
        expect((await carnetInfo()).subscriptions[0].meta.lessons_left).toBe(5);

        const openBalance = async () => {
            drawer = await openGiulia({page, open, expect});
            await drawer.locator('.nav-link').filter({hasText: /^\s*Carnet\s*$/}).click();
            await expect(drawer.locator('#bkn_datatable_carnet [data-row]')).toHaveCount(1);
            await drawer.locator(`#action-col-${uid} button:is([title="Modifica"],[data-original-title="Modifica"])`).click();
            const modal = page.locator(`#editModal-${uid}`); await expect(modal).toBeVisible(); return modal;
        };
        const edit = async (value, captureBefore) => {
            const modal = await openBalance();
            await modal.locator('[name="lessons_left"]').fill(String(value));
            if (captureBefore) await take(captureBefore, page, modal.locator('.modal-content'));
            const updating = page.waitForResponse(r => new URL(r.url()).pathname === `/api/carnet-subscription/${uid}/update`
                && r.request().method() === 'PATCH');
            await modal.getByRole('button', {name: 'Salva', exact: true}).click();
            const response = await updating; expect(response.status()).toBe(200);
            expect(response.request().postDataJSON().lessons_left).toBe(value);
            await expect(modal).not.toBeVisible();
            await page.keyboard.press('Escape'); await expect(drawer).toHaveCount(0);
            await page.reload(); drawer = await openGiulia({page, open, expect});
            await drawer.locator('.nav-link').filter({hasText: /^\s*Carnet\s*$/}).click();
            await expect(drawer.locator('#bkn_datatable_carnet [data-row]')).toContainText(`${value}/5`);
        };
        await edit(3, 'carnet-balance-edit-three');
        const edited = (await carnetInfo()).subscriptions[0];
        proof('manual_balance_persisted', edited.meta.lessons_left);
        proof('manual_balance_total_preserved', edited.meta.lessons_counter);
        proof('manual_balance_usage_preserved', edited.meta.lessons_registry.length);
        await take('carnet-balance-three-persists', page, drawer);
        for (const invalid of [-1, 6]) {
            const response = await api(`carnet-subscription/${uid}/update`, {method: 'PATCH', data: {lessons_left: invalid}});
            expect(response.status()).toBe(400);
            expect((await carnetInfo()).subscriptions[0].meta).toEqual(edited.meta);
        }
        proof('invalid_balances_preserve_state', true);
        await page.keyboard.press('Escape'); await expect(drawer).toHaveCount(0);
        await edit(5);
        proof('manual_balance_restored', (await carnetInfo()).subscriptions[0].meta.lessons_left);
        await take('carnet-balance-five-restored', page, drawer);
        await page.keyboard.press('Escape'); await expect(drawer).toHaveCount(0);

        await page.goto(input.origin + usageHref);
        const recharge = page.locator(`#action-col-${uid} button:is([title="Ricarica carnet"],[data-original-title="Ricarica carnet"])`);
        await recharge.click();
        let popup = page.locator('.swal2-popup'); await expect(popup).toContainText('Giulia Bianchi');
        await take('carnet-topup-confirm-cancel', page, popup);
        await popup.getByRole('button', {name: 'Annulla', exact: true}).click();
        expect((await carnetInfo()).subscriptions).toHaveLength(1); expect(records(await listPayments())).toEqual(records(initialPayments));
        proof('cancel_topup_preserves_records', true); await take('carnet-topup-cancelled');
        await recharge.click(); popup = page.locator('.swal2-popup');
        await take('carnet-topup-confirm-new-assignment', page, popup);
        const recharging = page.waitForResponse(r => new URL(r.url()).pathname === `/api/carnet-subscription/${uid}/topup`
            && r.request().method() === 'POST');
        await popup.getByRole('button', {name: 'Ricarica', exact: true}).click();
        expect((await recharging).status()).toBe(200);
        await expect(page.getByText('Utilizzo del Carnet', {exact: true})).toBeVisible();
        const after = (await carnetInfo()).subscriptions;
        proof('topup_assignments', after.length);
        const newAssignment = after.find(row => row.carnet_subscription_id !== uid);
        expect(newAssignment.subscription.subscription_id).toBe(input.subscription_ids[0]);
        proof('topup_new_balance', newAssignment.meta.lessons_left);
        expect(newAssignment.meta.lessons_counter).toBe(5); expect(newAssignment.meta.lessons_registry).toEqual([]);
        proof('topup_courses_copied', newAssignment.course.some(row => row.course_subscription_id === registration.course_subscription_id));
        expect(after.find(row => row.carnet_subscription_id === uid)).toEqual(original);
        proof('topup_original_assignment_preserved', true);
        const payments = await listPayments(), newPayment = payments.find(row => row.payment_id === newAssignment.payment);
        proof('topup_new_payment_amount', Number(newPayment.amount)); proof('topup_new_payment_unpaid', newPayment.paid === false);
        expect(records(payments.filter(row => row.payment_id !== newAssignment.payment))).toEqual(records(initialPayments));
        proof('topup_payment_count', payments.length);
        await expect(page.locator('[data-row]').filter({hasText: 'Giulia Bianchi'})).toHaveCount(2);
        await take('carnet-topup-new-row-and-payment');

        const reader = await actor('reader');
        const denials = [
            [`carnet-subscription/${uid}/update`, 'PATCH', {lessons_left: 1}],
            [`carnet-subscription/${uid}/topup`, 'POST', {}],
            [`attendance-day/${event.attendance_day_id}/delete`, 'DELETE', undefined],
        ];
        for (const [endpoint, method, data] of denials) expect((await reader.api(endpoint, {method, data})).status()).toBe(403);
        proof('reader_write_denials', denials.length);
        expect((await carnetInfo()).subscriptions).toEqual(after); expect(records(await listPayments())).toEqual(records(payments));
        expect((await get(`course/${input.course_id}/attendees`)).events).toHaveLength(1);
        proof('denials_preserve_state', true);
        await page.goto(input.origin + courseUrl + '/attendance');
        expect((await get(`course/${input.course_id}/attendees`)).events[0].attendees).toEqual([]);
        const deletion = page.locator('.timeline-item').filter({hasText: event.title}).locator('a.btn-light-danger');
        await deletion.click(); popup = page.locator('.swal2-popup');
        await expect(popup).toContainText('Desideri eliminare la data dal registro presenze?');
        await take('attendance-empty-date-delete-cancel', page, popup);
        await popup.getByRole('button', {name: 'Annulla', exact: true}).click();
        proof('cancel_date_delete_preserves_row', (await get(`course/${input.course_id}/attendees`)).events.length === 1);
        await deletion.click(); popup = page.locator('.swal2-popup');
        const deleting = page.waitForResponse(r => new URL(r.url()).pathname === `/api/attendance-day/${event.attendance_day_id}/delete`
            && r.request().method() === 'DELETE');
        await popup.getByRole('button', {name: 'Elimina', exact: true}).click(); expect((await deleting).status()).toBe(200);
        await page.reload();
        proof('empty_date_deleted', (await get(`course/${input.course_id}/attendees`)).events.length === 0);
        await expect(page.locator('.timeline-item')).toHaveCount(0);
        proof('date_delete_calendar_event_preserved', (await get(`course/${input.course_id}/calendar`)).events.length === 1);
        expect((await carnetInfo()).subscriptions).toEqual(after);
        proof('empty_date_delete_balances_preserved', true);
        await take('attendance-empty-date-deleted-after-reload');
        await reader.page.goto(input.origin + usageHref);
        await expect(reader.page.getByRole('button', {name: 'Assegna carnet', exact: true})).toBeDisabled();
        await expect(reader.page.locator('button:is([title="Ricarica carnet"],[data-original-title="Ricarica carnet"])')).toHaveCount(2);
        for (const button of await reader.page.locator('button:is([title="Ricarica carnet"],[data-original-title="Ricarica carnet"])').all()) await expect(button).toBeDisabled();
        await take('carnet-maintenance-reader-controls', reader.page);
        report.cleanup_policy = {status: 'pending-runner-reset', operation: 'attendance-carnet-owned-fixture-reset',
            carnet: carnet.carnet_id, subscription: input.subscription_ids[0], course: input.course_id,
            assignments: after.map(row => row.carnet_subscription_id), payments: [original.payment, newAssignment.payment]};
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.external_gaps = [
            {operation: 'delete-attended-date-and-return-carnet-usage', status: 'pending',
                reason: 'Only an empty date is deleted. Deletion is not the manual absence correction path.'},
            {operation: 'automatic-attendance-and-athlete-absence-reports', status: 'pending',
                reason: 'No scheduled worker or athlete reporting device was exercised.'},
            {operation: 'multiple-carnet-consumption-priority', status: 'pending',
                reason: 'Top-up creates two assignments but no attendance is recorded while both exist.'},
        ];
        report.checks = ['personal history reflects actual check-in and removal', 'real filter/search actions',
            'UI manual balance change persists; invalid limits denied without mutation',
            'UI recharge cancellation and real POST preserve old assignment and create independent unpaid payment',
            'reader writes denied', 'empty-date deletion survives reload and leaves calendar event unchanged'];
    }});
