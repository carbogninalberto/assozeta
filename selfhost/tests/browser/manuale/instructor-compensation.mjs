// Compensation, PDF and lesson report use the real UI, API and rendering worker.
import {scenario, expect} from './scenario.mjs';
import {organizationAuthoredWorkflows} from '../../../../docs/manuale/organization-authored-workflows.mjs';
import {xlsxRows} from './organization-exports.mjs';
import crypto from 'node:crypto';
import fs from 'node:fs';

const id = 'instructor-compensation';
const spec = organizationAuthoredWorkflows[id];
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
// List endpoints order by fixture timestamps that share one frozen value (and the
// instructor list is unordered), so compare record sets, not physical row order.
// Some list endpoints return keyed objects; their keys are the record identities.
const recordSet = rows => JSON.stringify((Array.isArray(rows) ? rows.map(row => JSON.stringify(row))
    : Object.entries(rows).map(entry => JSON.stringify(entry))).sort());
const sameRecords = (left, right) => recordSet(left) === recordSet(right);
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async (route, requestApi = api) => {
            const res = await requestApi(route); expect(res.status()).toBe(200); return res.json();
        };
        const write = async (route, method, data, status = 200) => {
            const res = await api(route, {method, data}); expect(res.status(), route).toBe(status); return res.json();
        };
        const take = async (checkpoint, focus, activePage = page) => {
            await expect(activePage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await capture(activePage, spec.checkpoints.findIndex(item => item.id === checkpoint) + 1, checkpoint, focus);
        };
        const italian = value => value.split('-').reverse().join('/');
        const facts = {};
        const proof = (field, value) => {expect(value, field).toEqual(spec.outcome.expected[field]); facts[field] = value;};
        const allPayments = async () => Object.values((await read('payment/list?pagination[perpage]=100')).data);
        const originalPayments = await allPayments();
        const originalMembers = (await read('subscription/list?pagination[perpage]=100')).data;
        const originalCourses = (await read('course/list?all=1')).data;
        const originalInstructors = (await read('instructor/list')).data;
        const owned = {instructor: null, course: null, category: null, payments: new Set(), hours: [], memberships: [], calendar: false};
        let cleanupError;
        let primaryError;
        try {
            // Explicit fixture preparation through real authorized APIs. These are not
            // published as instructor/course creation or payment-approval procedures.
            await write('instructor/add', 'POST', {first_name: 'Elena', last_name: 'Moretti',
                email: 'elena@example.test', default_hourly_billing: '15.00', default_percentage_billing: '20.00'});
            const instructor = (await read('instructor/list')).data.find(item => !originalInstructors.some(old => old.instructor_id === item.instructor_id));
            expect(instructor).toBeTruthy(); owned.instructor = instructor.instructor_id;
            const uid = owned.instructor;
            const categories = (await read('payment/category/list')).data;
            let category = categories.find(item => item.name === 'Compensi e Rimborsi Spese');
            if (!category) {
                category = (await write('payment/category/add', 'POST', {name: 'Compensi e Rimborsi Spese', expense: true})).data;
                expect(category.payment_category_id).toBeTruthy(); owned.category = category.payment_category_id;
            }
            await write('course/add', 'POST', {new_course: {title: 'Ginnastica compensi e report',
                description: 'Lezione dimostrativa e incassi per il calcolo.', fee: '120.00', course_type: 1}, subscriptions: []});
            const course = (await read('course/list?all=1')).data.find(item => !originalCourses.some(old => old.course_id === item.course_id));
            expect(course).toBeTruthy(); owned.course = course.course_id;
            const assigned = await write('course-subscriptions/add', 'POST', input.subscription_ids.slice(0, 2)
                .map(subscription_id => ({subscription_id, course: owned.course})), 201);
            expect(assigned).toHaveLength(2);
            owned.memberships = assigned.map(item => item.course_subscription_id);
            const sourcePayments = (await allPayments()).filter(item => item.course?.course_id === owned.course);
            expect(sourcePayments).toHaveLength(2);
            for (const entry of sourcePayments) {
                owned.payments.add(entry.payment_id);
                // Fixture settlement via a declared PATCH avoids receipts/emails and does
                // not purport to demonstrate the incasso UI or an external transaction.
                await write(`payment/${entry.payment_id}/update`, 'PATCH', {paid: true,
                    payment_date: input.reference_date.slice(0, 8) + '01T10:00:00+02:00'});
            }
            const sourceSnapshot = (await allPayments()).filter(item => owned.payments.has(item.payment_id));
            expect(sourceSnapshot.every(item => item.paid && Number(item.amount) === 120 && item.invoice === null)).toBe(true);
            for (const [hours, paid, notes] of [[2, false, 'Ore dimostrative ammissibili'], [1, true, 'Ore già pagate escluse']]) {
                await write(`instructor/${uid}/hours/add`, 'POST', {date: italian(input.reference_date),
                    compensation_type: 'hourly', hours, hourly_billing: '15.00', amount: String(hours * 15), paid, notes});
            }
            const hours = async () => (await read(`instructor/${uid}/hours/list?pagination[perpage]=100`)).data;
            const initialHours = await hours(); expect(initialHours).toHaveLength(2);
            owned.hours = initialHours.map(item => item.instructor_hours_id);
            report.fixture_preparation = {backend: 'real', instructor_count: 1, course_count: 1,
                course_subscriptions: 2, course_payments_settled_via_patch: 2, hourly_rows: 2,
                created_compensation_category: Boolean(owned.category), email_requested: false,
                purpose: 'Owned local preparation; no creation/approval guides promoted'};

            await open('Attività', '/#/course/instructor/list/');
            await page.locator('[data-row]').filter({hasText: 'ELENA MORETTI'})
                .getByRole('link', {name: 'ELENA MORETTI', exact: true}).click();
            await page.getByRole('button', {name: 'Aggiungi', exact: true}).click();
            const modal = page.locator(`#modal-${uid}`);
            await expect(modal).toBeVisible();
            await modal.getByRole('button', {name: 'Percentuale', exact: true}).click();
            const courses = modal.locator('label[for="courses"]').locator('..');
            await courses.locator('input:not([type="hidden"])').fill(course.title);
            await page.locator('.list-item').filter({hasText: course.title}).click();
            await modal.locator('[name="percentage_billing"]').fill('20');
            await modal.locator('#compensation-range').click();
            const range = page.locator('.drp-panel');
            await range.getByRole('button', {name: 'Questo mese', exact: true}).click();
            await modal.locator('[name="date"]').fill(input.reference_date);
            await modal.locator('[name="paid"]').selectOption('false');
            await take('percentage-course-period-selection', modal.locator('.modal-content'));
            const calculated = page.waitForResponse(res => new URL(res.url()).pathname === `/api/instructor/${uid}/hours/calculate`
                && res.request().method() === 'POST');
            await modal.getByRole('button', {name: 'Calcola', exact: true}).click();
            const calculation = await calculated; expect(calculation.status()).toBe(200);
            const calculatedData = (await calculation.json()).data;
            proof('percentage_source_payments', calculatedData.calculation_data.length);
            proof('percentage_calculated_amount', Number(calculatedData.amount));
            expect(calculatedData.calculation_data.every(item => item.course === course.title && Number(item.amount) === 120)).toBe(true);
            await expect(modal.locator('input[name="amount"]')).toHaveValue('48');
            await take('percentage-paid-course-calculation', modal.locator('.modal-content'));
            const detail = modal.locator('.d-flex.align-items-center.justify-content-between').filter({hasText: 'Giulia Bianchi'});
            await detail.getByRole('button').click();
            await expect(modal.locator('input[name="amount"]')).toHaveValue('24');
            proof('percentage_excluded_amount', Number(calculatedData.amount) - Number(await modal.locator('input[name="amount"]').inputValue()));
            await modal.locator('[name="notes"]').fill('Percentuale dimostrativa senza il pagamento di Giulia');
            await take('percentage-exclusion-and-total', modal.locator('.modal-content'));
            const saved = page.waitForResponse(res => new URL(res.url()).pathname === `/api/instructor/${uid}/hours/add`
                && res.request().method() === 'POST');
            await modal.getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await saved).status()).toBe(200);
            await expect(modal).not.toBeVisible(); await page.reload();
            const recorded = await hours(); const percentage = recorded.find(item => item.compensation_type === 'percentage');
            expect(percentage).toBeTruthy(); owned.hours.push(percentage.instructor_hours_id);
            proof('percentage_saved_amount', Number(percentage.amount));
            expect(percentage.paid).toBe(false); expect(percentage.payment).toBeNull();
            expect(percentage.calculation_data).toHaveLength(1);
            expect(percentage.calculation_data[0].full_athlete_name).toBe('Luca Verdi');
            proof('source_payments_preserved', sameRecords((await allPayments()).filter(item => sourcePayments.some(p => p.payment_id === item.payment_id)), sourceSnapshot));
            const percentageRow = page.locator('[data-row]').filter({hasText: percentage.notes});
            await expect(percentageRow).toContainText('24,00');
            await take('percentage-persists-after-reload');
            const hourly = recorded.find(item => item.notes === 'Ore dimostrative ammissibili');
            const ineligible = recorded.find(item => item.notes === 'Ore già pagate escluse');
            expect(hourly).toBeTruthy(); expect(ineligible).toBeTruthy();
            const ineligibleBefore = JSON.stringify(ineligible);
            const rowFor = row => page.locator('[data-row]').filter({hasText: row.notes});
            const select = async row => {const checkbox = rowFor(row).locator('input[type="checkbox"]');
                if (!await checkbox.isChecked()) await checkbox.locator('..').click(); await expect(checkbox).toBeChecked();};
            await select(hourly); await select(percentage); await select(ineligible);
            await take('eligible-hours-selected', page.locator('.datatable-table').first());
            await page.locator('#bkn_datatable_approve_selected').click();
            const confirmation = page.locator('.swal2-popup');
            await expect(confirmation).toContainText('non sono ancora state pagate');
            await expect(confirmation).toContainText('non hanno un pagamento associato');
            await take('compensation-confirmation', confirmation);
            const beforeCancel = await allPayments();
            await confirmation.getByRole('button', {name: 'Annulla', exact: true}).click();
            proof('cancellation_did_not_create_payment', sameRecords(await allPayments(), beforeCancel));
            await page.locator('#bkn_datatable_approve_selected').click();
            const compensation = page.waitForResponse(res => new URL(res.url()).pathname === `/api/instructor/${uid}/hours/add/compensation`
                && res.request().method() === 'POST');
            await confirmation.getByRole('button', {name: 'Crea compenso', exact: true}).click();
            expect((await compensation).status()).toBe(200);
            const createdPayments = (await allPayments()).filter(item => !beforeCancel.some(old => old.payment_id === item.payment_id));
            expect(createdPayments).toHaveLength(1); const payment = createdPayments[0]; owned.payments.add(payment.payment_id);
            proof('compensation_amount', Number(payment.amount));
            proof('compensation_is_unpaid_expense', payment.expense === true && payment.paid === false);
            expect(payment.payment_category).toBe(category.payment_category_id);
            expect(payment.description).toContain('Elena Moretti');
            await page.reload();
            // The real hours-list fallback generates a missing compensation PDF. Require
            // a successfully stored file instead of treating POST success as document proof.
            await expect.poll(async () => (await hours()).filter(item => item.payment === payment.payment_id)
                .every(item => Boolean(item.document)), {timeout: 90000}).toBe(true);
            const linked = await hours();
            proof('linked_hours', linked.filter(item => item.payment === payment.payment_id).length);
            proof('ineligible_hours_unchanged', JSON.stringify(linked.find(item => item.instructor_hours_id === ineligible.instructor_hours_id)) === ineligibleBefore);
            proof('payment_links_persist_after_reload', [hourly, percentage].every(item =>
                linked.find(row => row.instructor_hours_id === item.instructor_hours_id)?.payment === payment.payment_id));
            for (const entry of [hourly, percentage]) {
                await expect(rowFor(entry).locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled();
                await expect(rowFor(entry).locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
            }
            await take('compensation-payment-links-persist');
            await page.goto(input.origin + '/#/payment/list/' + payment.payment_id);
            await expect(page.locator('[data-row]').filter({hasText: 'Elena Moretti'})).toBeVisible();
            await expect(page.locator('[data-row]').filter({hasText: 'Elena Moretti'})).toContainText('54,00');
            await take('compensation-payment-in-accounting', page.locator('.datatable-table').first());
            // Use the actual route produced by the list link when reopening the card.
            await open('Attività', '/#/course/instructor/list/');
            await page.locator('[data-row]').filter({hasText: 'ELENA MORETTI'}).getByRole('link', {name: 'ELENA MORETTI', exact: true}).click();
            const documentRow = (await hours()).find(item => item.instructor_hours_id === hourly.instructor_hours_id);
            expect(documentRow.document).toBeTruthy(); expect(documentRow.document_token).toBeTruthy();
            const pdf = await api(`document/retrieve/${documentRow.document}?download=true&token=${documentRow.document_token}`);
            expect(pdf.status()).toBe(200); expect(pdf.headers()['content-type']).toContain('application/pdf');
            const pdfBytes = await pdf.body(); proof('actual_pdf_validated', pdfBytes.subarray(0, 5).toString() === '%PDF-');
            const printView = await api(`document/compensation/${payment.payment_id}/view/`);
            expect(printView.status()).toBe(200);
            expect(printView.headers()['content-type']).toContain('text/html');
            const printText = await page.evaluate(html => new DOMParser().parseFromString(html, 'text/html').body.textContent,
                await printView.text());
            proof('pdf_source_person_and_amount_match', /ELENA\s+MORETTI/.test(printText) && /54[,.]00\s*€/.test(printText));
            report.pdf_documents = [{bytes: pdfBytes.length, sha256: digest(pdfBytes)}];
            const fetched = page.waitForResponse(res => new URL(res.url()).pathname === '/api/document/retrieve/' + documentRow.document
                && new URL(res.url()).searchParams.get('download') === 'false');
            await rowFor(hourly).locator('button[title^="Scarica il compenso"]').click();
            const preview = page.getByRole('dialog', {name: /^Compenso istruttore/});
            const frame = preview.locator('iframe'); await expect(frame).toBeVisible();
            const previewResponse = await fetched; expect(previewResponse.status()).toBe(200);
            expect(previewResponse.headers()['content-type']).toContain('application/pdf');
            const displayedPdf = await page.request.get(new URL(await frame.getAttribute('src'), input.origin).href);
            expect(displayedPdf.status()).toBe(200);
            proof('actual_pdf_previewed', digest(await displayedPdf.body()) === digest(pdfBytes));
            await expect(preview.getByRole('button', {name: /Firma/})).toBeVisible();
            await take('actual-compensation-document', frame);
            proof('signature_not_executed', true);
            await preview.getByRole('button', {name: 'Chiudi', exact: true}).first().click();

            const eventId = crypto.randomUUID();
            await write(`course/${owned.course}/calendar/update`, 'POST', {status: 2, events: [{event_id: eventId,
                title: 'Lezione report compensi', start: new Date(input.reference_date + 'T08:00:00+02:00').toISOString(),
                end: new Date(input.reference_date + 'T09:00:00+02:00').toISOString(), allDay: false,
                extendedProps: {timeContract: 'utc-v1', description: 'Lezione nota per confrontare il report', instructor: {
                    instructor_id: uid, first_name: 'Elena', last_name: 'Moretti'}, reminder_enabled: false}}]});
            owned.calendar = true;
            const lesson = (await read(`course/${owned.course}/attendees`)).data.events.find(item => item.title === 'Lezione report compensi');
            expect(lesson).toBeTruthy(); expect(lesson.attendance_day_id).toBeTruthy();
            await page.goto(input.origin + `/#/course/overview/${owned.course}/attendance`);
            await page.locator(`a[data-target="#attendance-day-${lesson.attendance_day_id}"]`).click();
            const attendance = page.locator(`#attendance-day-${lesson.attendance_day_id}`);
            const giulia = attendance.locator('.list-item-attendees').filter({hasText: 'Bianchi Giulia'});
            const checkin = page.waitForResponse(res => new URL(res.url()).pathname === `/api/course/${owned.course}/attendees/${lesson.attendance_day_id}/update`
                && res.request().method() === 'POST');
            await giulia.locator('input[type="checkbox"]').locator('..').click();
            expect((await checkin).status()).toBe(200);
            const known = (await read(`course/${owned.course}/attendees`)).data.events.find(item => item.attendance_day_id === lesson.attendance_day_id);
            expect(known.attendees).toEqual([{course_subscription_id: assigned[0].course_subscription_id}]);
            await take('report-known-attendance-day', attendance.locator('.modal-content'));
            await attendance.getByText('Chiudi', {exact: true}).click();
            await open('Attività', '/#/course/instructor/list/');
            await page.getByRole('button', {name: 'Stampa Report', exact: true}).click();
            const reportModal = page.locator('#report-instructors-modal');
            await reportModal.locator('#instructor_report_range').click();
            await page.locator('.drp-panel').getByRole('button', {name: 'Questo mese', exact: true}).click();
            await take('report-period-and-generate', reportModal.locator('.modal-content'));
            const responseEvent = page.waitForResponse(res => new URL(res.url()).pathname === '/api/instructor/report');
            const downloadEvent = page.waitForEvent('download');
            await reportModal.getByRole('button', {name: 'Genera Report', exact: true}).click();
            const generated = await responseEvent; expect(generated.status()).toBe(200);
            const requestRange = new URL(generated.url()).searchParams;
            expect(requestRange.get('start_date')).toBe(italian(input.reference_date.slice(0, 8) + '01'));
            const reportData = (await generated.json()).data;
            proof('report_known_lesson_rows', reportData.results.length);
            expect(reportData.results[0]).toMatchObject({first_name: 'Elena', last_name: 'Moretti', course: course.title,
                title: 'Lezione report compensi', description: 'Lezione nota per confrontare il report', attendees_count: 1,
                attendees: 'Giulia Bianchi', expected_absences: ''});
            proof('report_attendees_match', reportData.results[0].attendees_count === known.attendees.length);
            const download = await downloadEvent; expect(await download.failure()).toBeNull();
            expect(download.suggestedFilename()).toMatch(/\.xlsx$/);
            const xlsx = fs.readFileSync(await download.path()); await download.delete();
            const rows = await xlsxRows(page, xlsx);
            const column = name => {const index = rows[0].indexOf(name); expect(index).toBeGreaterThanOrEqual(0); return index;};
            const result = rows.slice(1).filter(row => row[column('Titolo')] === 'Lezione report compensi');
            expect(result).toHaveLength(1);
            proof('report_xlsx_matches', result[0][column('Nome')] === 'Elena' && result[0][column('Cognome')] === 'Moretti'
                && result[0][column('Corso')] === course.title && result[0][column('Partecipanti')] === 'Giulia Bianchi'
                && Number(result[0][column('Numero di partecipanti')]) === 1);
            report.downloads = [{format: 'xlsx', bytes: xlsx.length, sha256: digest(xlsx), known_lesson_rows: result.length}];

            const reader = await actor('reader');
            await reader.open('Attività', '/#/course/instructor/list/');
            await reader.page.locator('[data-row]').filter({hasText: 'ELENA MORETTI'}).getByRole('link', {name: 'ELENA MORETTI', exact: true}).click();
            await expect(reader.page.getByRole('button', {name: 'Aggiungi', exact: true})).toBeDisabled();
            const readerRow = reader.page.locator('[data-row]').filter({hasText: hourly.notes});
            await readerRow.locator('input[type="checkbox"]').locator('..').click();
            await expect(reader.page.locator('#bkn_datatable_approve_selected')).toBeDisabled();
            await take('reader-compensation-controls', reader.page.locator('.datatable-table').first(), reader.page);
            const beforeDenials = await hours();
            const denied = [];
            for (const [route, method, data] of [
                [`instructor/${uid}/hours/add`, 'POST', {date: italian(input.reference_date), hours: 1, amount: 99}],
                [`instructor/${uid}/hours/add/compensation`, 'POST', {hours: [ineligible.instructor_hours_id]}],
                [`instructor/${uid}/hours/${percentage.instructor_hours_id}/update`, 'PATCH', {amount: 99}],
            ]) {
                const res = await reader.api(route, {method, data}); expect(res.status()).toBe(403); denied.push(res.status());
                report.expected_denials.push({identity: 'reader', path: '/api/' + route, status: res.status()});
            }
            proof('reader_write_denials', denied.length); expect(sameRecords(await hours(), beforeDenials)).toBe(true);
            const readerReport = await read('instructor/report?' + requestRange, reader.api);
            proof('reader_report_read_allowed', sameRecords(readerReport.data.results, reportData.results));
        } catch (error) {
            primaryError = error;
            throw error;
        } finally {
            // Remove only resources this invocation created, even after assertion failure.
            // Hours deletion also removes their generated Document, prior to payment deletion.
            if (owned.course) {
                try {
                    for (const payment of await allPayments()) {
                        if (payment.course?.course_id === owned.course) owned.payments.add(payment.payment_id);
                    }
                    const linkedSubscriptions = (await read(`course-subscriptions/list?course_id=${owned.course}`)).data;
                    owned.memberships = [...new Set([...owned.memberships, ...linkedSubscriptions.map(item => item.course_subscription_id)])];
                } catch {
                    cleanupError = cleanupError || 'Owned course reconciliation read failed';
                }
            }
            if (owned.instructor) {
                let remaining = {data: []};
                try {
                    remaining = await read(`instructor/${owned.instructor}/hours/list?pagination[perpage]=100`);
                } catch {
                    cleanupError = cleanupError || 'Owned hours reconciliation read failed';
                }
                for (const row of remaining.data) {
                    if (row.payment) owned.payments.add(row.payment);
                    // This newly created instructor has no preexisting rows. Reconcile
                    // UI-created IDs even when a later assertion failed before recording them.
                    const res = await api(`instructor/${owned.instructor}/hours/${row.instructor_hours_id}/delete`, {method: 'DELETE'});
                    if (![200, 404].includes(res.status())) cleanupError = 'Owned hours cleanup failed';
                }
            }
            for (const uid of owned.payments) {
                const res = await api(`payment/${uid}/delete`, {method: 'DELETE'});
                if (![200, 404].includes(res.status())) cleanupError = 'Owned payment cleanup failed';
            }
            for (const uid of owned.memberships) {
                const res = await api(`course-subscriptions/${uid}/delete`, {method: 'DELETE'});
                if (![204, 404].includes(res.status())) cleanupError = 'Owned course subscription cleanup failed';
            }
            if (owned.course) {
                const removed = owned.calendar ? await api(`course/${owned.course}/calendar/update`, {method: 'DELETE'}) : null;
                if (removed && ![200, 404].includes(removed.status())) cleanupError = 'Owned lesson cleanup failed';
                const res = await api(`course/${owned.course}/delete`, {method: 'POST'});
                if (![200, 404].includes(res.status())) cleanupError = 'Owned course cleanup failed';
            }
            if (owned.instructor) {
                const res = await api(`instructor/${owned.instructor}/delete`, {method: 'DELETE'});
                if (![200, 404].includes(res.status())) cleanupError = 'Owned instructor cleanup failed';
            }
            if (owned.category) {
                const res = await api(`payment/category/${owned.category}/delete`, {method: 'DELETE'});
                if (![200, 404].includes(res.status())) cleanupError = 'Owned category cleanup failed';
            }
            // Report cleanup privately without hiding the original assertion.
            if (cleanupError) report.cleanup_error = cleanupError;
            if (cleanupError && !primaryError) throw new Error(cleanupError);
        }
        proof('baseline_records_preserved', sameRecords(await allPayments(), originalPayments)
            && sameRecords((await read('subscription/list?pagination[perpage]=100')).data, originalMembers)
            && sameRecords((await read('course/list?all=1')).data, originalCourses));
        proof('owned_resources_cleaned', sameRecords((await read('instructor/list')).data, originalInstructors));
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.checks = ['real percentage calculation, exclusion and persisted amount', 'compensation cancellation and scoped unpaid expense links',
            'real PDF generation via hours-list fallback, retrieval and preview', 'real attendance row matches parsed XLSX report',
            'reader writes refused, report readable; owned resources removed'];
        report.external_gaps = [{operation: 'compensation-signature', status: 'needs_external_verification'},
            {operation: 'external-instructor-payment', status: 'needs_external_verification'}];
    },
});
