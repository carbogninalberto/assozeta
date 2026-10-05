import {scenario, expect} from './scenario.mjs';
import {instructorMaintenanceAuthoredWorkflows} from '../../../../docs/manuale/instructor-maintenance-authored-workflows.mjs';
import crypto from 'node:crypto';

const id = 'instructor-maintenance';
const spec = instructorMaintenanceAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async (route, requestApi = api) => {const res = await requestApi(route);
            expect(res.status(), route).toBe(200); return res.json();};
        const write = async (route, method, data, status = 200) => {const res = await api(route, {method, data});
            expect(res.status(), route).toBe(status); return res.json();};
        const take = async (checkpoint, locator, currentPage = page) => {
            const number = spec.checkpoints.findIndex(item => item.id === checkpoint) + 1;
            expect(number).toBeGreaterThan(0);
            await expect(currentPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await capture(currentPage, number, checkpoint, locator);
        };
        const facts = {};
        const proof = (field, value) => {expect(value, field).toEqual(spec.outcome.expected[field]); facts[field] = value;};
        const allPayments = async () => Object.values((await read('payment/list?pagination[perpage]=100')).data);
        const allInstructors = async () => (await read('instructor/list')).data;
        const allCourses = async () => (await read('course/list?all=1')).data;
        const baseline = {payments: await allPayments(), instructors: await allInstructors(), courses: await allCourses(),
            members: (await read('subscription/list?pagination[perpage]=100')).data};
        const owned = {instructors: new Set(), payments: new Set(), memberships: new Set(), documents: new Set(), course: null, published: false};
        let uid, secondUid, paymentId;
        const italian = date => date.split('-').reverse().join('/');
        const monthStart = input.reference_date.slice(0, 8) + '01';
        const end = new Date(input.reference_date + 'T12:00:00Z'); end.setUTCMonth(end.getUTCMonth() + 1, 0);
        const monthEnd = end.toISOString().slice(0, 10);
        const period = italian(monthStart) + ' al ' + italian(monthEnd);
        const hours = async () => (await read(`instructor/${uid}/hours/list?pagination[perpage]=100`)).data;
        const calendar = async () => (await read(`course/${owned.course}/calendar`)).data;
        const info = async () => read(`instructor/${uid}/info?date_range=${encodeURIComponent(period)}`);
        const stableHour = row => Object.fromEntries(['instructor_hours_id', 'instructor', 'date', 'hours', 'hourly_billing',
            'percentage_billing', 'compensation_type', 'amount', 'paid', 'payment', 'notes', 'courses', 'period', 'calculation_data'].map(key => [key, row[key]]));
        const stableHours = rows => rows.map(stableHour).sort((a, b) => a.instructor_hours_id.localeCompare(b.instructor_hours_id));
        const rememberDocuments = rows => rows.forEach(row => {if (row.document) owned.documents.add(row.document);});
        const instructorRow = (currentPage, name) => currentPage.locator('[data-row]').filter({hasText: name});
        const openCard = async () => {
            await open('Attività', '/#/course/instructor/list/');
            await instructorRow(page, 'IRENE FERRI').getByRole('link', {name: 'IRENE FERRI', exact: true}).click();
            await expect(page.getByText('Scheda compensi', {exact: true})).toBeVisible();
        };
        const rowByNotes = notes => page.locator('[data-row]').filter({hasText: notes});
        const selectInstructor = async (modal, name) => {
            const selector = modal.locator('label').filter({hasText: /^Istruttore(?: lezione)?$/}).locator('..');
            const input=selector.locator('input:not([type="hidden"])');await input.click();await input.fill(name);
            await page.locator('.list-item:visible').filter({hasText: name}).click();
            await expect(page.locator('.list-item:visible')).toHaveCount(0);
        };
        const assignmentIds = event => {
            const data = event.extendedProps?.instructor;
            return (Array.isArray(data) ? data : data ? [data] : []).map(item => item.instructor_id).sort();
        };
        let originalError;
        try {
            // All preparatory records belong only to this disposable scenario. Preparation
            // does not promote creation, compensation, settlement or email procedures.
            for (const [first_name, last_name, email] of [['Irene', 'Ferri', 'irene@example.test'], ['Davide', 'Costa', 'davide@example.test']]) {
                const before = await allInstructors();
                await write('instructor/add', 'POST', {first_name, last_name, email, default_hourly_billing: '15.00', default_percentage_billing: '20.00'});
                const created = (await allInstructors()).filter(item => !before.some(old => old.instructor_id === item.instructor_id));
                expect(created).toHaveLength(1); owned.instructors.add(created[0].instructor_id);
                if (first_name === 'Irene') uid = created[0].instructor_id; else secondUid = created[0].instructor_id;
            }
            await write('course/add', 'POST', {new_course: {title: 'Ginnastica manutenzione istruttori',
                description: 'Corso dimostrativo per assegnazioni e manutenzione.', fee: '120.00', course_type: 1}, subscriptions: []});
            const course = (await allCourses()).filter(item => !baseline.courses.some(old => old.course_id === item.course_id));
            expect(course).toHaveLength(1); owned.course = course[0].course_id;
            const memberships = await write('course-subscriptions/add', 'POST', [{subscription_id: input.subscription_ids[0], course: owned.course}], 201);
            expect(memberships).toHaveLength(1); owned.memberships.add(memberships[0].course_subscription_id);
            const sourcePayments = (await allPayments()).filter(item => item.course?.course_id === owned.course);
            expect(sourcePayments).toHaveLength(1); owned.payments.add(sourcePayments[0].payment_id);
            await write(`payment/${sourcePayments[0].payment_id}/update`, 'PATCH', {paid: true, payment_date: monthStart + 'T10:00:00+02:00'});
            const sourceSnapshot = (await allPayments()).find(item => item.payment_id === sourcePayments[0].payment_id);
            const paid = await write('payment/add', 'POST', {description: 'Compenso di controllo Irene Ferri',
                amount: '20.00', type: 'cash', expense: true, creation_date: input.reference_date, payment_date: input.reference_date,
                custom_accounts: input.cash_account_id, payment_category: input.payment_category_id,
                complex_item: {group: 'Istruttori', instructor_id: uid}}, 201);
            paymentId = paid.payment_id; expect(paymentId).toBeTruthy(); owned.payments.add(paymentId);
            const compensationSnapshot = (await allPayments()).find(item => item.payment_id === paymentId);
            const courses = [{course_id: owned.course, value: owned.course, label: course[0].title}];
            const result = (await write(`instructor/${uid}/hours/calculate`, 'POST', {courses, period, percentage: 20})).data;
            expect(Number(result.amount)).toBe(24); expect(result.calculation_data).toHaveLength(1);
            for (const data of [
                {hours: '2.00', hourly_billing: '15.00', amount: '30.00', notes: 'Ore da correggere', compensation_type: 'hourly'},
                {hours: '1.00', hourly_billing: '10.00', amount: '10.00', notes: 'Registrazione da eliminare', compensation_type: 'hourly'},
                {hours: '1.00', hourly_billing: '20.00', amount: '20.00', notes: 'Ore con pagamento conservato', compensation_type: 'hourly', payment: paymentId},
                {hours: '0.00', hourly_billing: '0.00', amount: String(result.amount), notes: 'Percentuale da correggere',
                    compensation_type: 'percentage', percentage_billing: '20.00', courses, period, calculation_data: result.calculation_data},
            ]) await write(`instructor/${uid}/hours/add`, 'POST', {date: italian(input.reference_date), paid: false, ...data});
            const historyEvent = crypto.randomUUID();
            await write(`course/${owned.course}/calendar/update`, 'POST', {status: 2, events: [{event_id: historyEvent,
                title: 'Lezione storica Irene', start: input.reference_date + 'T14:00:00.000Z', end: input.reference_date + 'T15:00:00.000Z',
                allDay: false, extendedProps: {instructor: [{instructor_id: uid, value: uid, label: 'Irene Ferri'}], reminder_enabled: false}}]});
            owned.published = true;
            expect((await calendar()).google_sync_enabled).toBe(false);
            report.fixture_preparation = {backend: 'real', instructors: 2, course: 1, memberships: 1, source_payments: 1,
                source_payment_settlement: 'authorized fixture PATCH', control_compensation_payment: 1, hours: 4, history_lesson: 1,
                email_requested: false, external_transaction: false};
            const initial = await hours(); expect(initial).toHaveLength(4); rememberDocuments(initial);
            const hourly = initial.find(item => item.notes === 'Ore da correggere');
            const percentage = initial.find(item => item.notes === 'Percentuale da correggere');
            const removable = initial.find(item => item.notes === 'Registrazione da eliminare');
            const linked = initial.find(item => item.notes === 'Ore con pagamento conservato');
            for (const row of [hourly, percentage, removable, linked]) expect(row).toBeTruthy();
            const otherRows = stableHours([removable, linked]);
            await openCard();
            await rowByNotes(hourly.notes).locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
            const editHour = page.locator(`#modal-${hourly.instructor_hours_id}`);
            await expect(editHour).toContainText('Modifica Orario Istruttore');
            await expect(editHour.getByRole('button', {name: 'Orario', exact: true})).toBeDisabled();
            await expect(editHour.getByRole('button', {name: 'Percentuale', exact: true})).toBeDisabled();
            proof('hourly_type_fixed', await editHour.getByRole('button', {name: 'Percentuale', exact: true}).isDisabled());
            await editHour.locator('[name="hours"]').fill('3');
            await editHour.locator('[name="hourly_billing"]').fill('18');
            await editHour.locator('[name="notes"]').fill('Ore corrette e riaperte');
            await expect(editHour.locator('[name="amount"]')).toHaveValue('54');
            await take('hourly-edit-with-fixed-type', editHour.locator('.modal-content'));
            const hourlySaved = page.waitForResponse(res => new URL(res.url()).pathname === `/api/instructor/${uid}/hours/${hourly.instructor_hours_id}/update`
                && res.request().method() === 'PATCH');
            await editHour.getByRole('button', {name: 'Salva', exact: true}).click(); expect((await hourlySaved).status()).toBe(200);
            await expect(editHour).not.toBeVisible(); await page.reload();
            const edited = (await hours()).find(item => item.instructor_hours_id === hourly.instructor_hours_id);
            proof('hourly_amount_after_edit', Number(edited.amount)); expect(edited.compensation_type).toBe('hourly');
            await rowByNotes(edited.notes).locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
            await expect(editHour.locator('[name="hours"]')).toHaveValue('3.00');
            await expect(editHour.locator('[name="hourly_billing"]')).toHaveValue('18.00');
            await expect(editHour.locator('[name="notes"]')).toHaveValue(edited.notes);
            await take('hourly-edit-reopened', editHour.locator('.modal-content'));
            await editHour.locator('button[aria-label="Close"]').click();
            await expect(editHour).not.toBeVisible();

            await rowByNotes(percentage.notes).locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
            const editPercentage = page.locator(`#modal-${percentage.instructor_hours_id}`);
            await expect(editPercentage.getByRole('button', {name: 'Orario', exact: true})).toBeDisabled();
            proof('percentage_type_fixed', await editPercentage.getByRole('button', {name: 'Orario', exact: true}).isDisabled());
            await editPercentage.locator('[name="percentage_billing"]').fill('25');
            await editPercentage.locator('#compensation-range').click();
            await page.locator('.drp-panel').getByRole('button', {name: 'Questo mese', exact: true}).click();
            const recalculated = page.waitForResponse(res => new URL(res.url()).pathname === `/api/instructor/${uid}/hours/calculate`
                && res.request().method() === 'POST');
            await editPercentage.getByRole('button', {name: 'Calcola', exact: true}).click();
            const recalculation = await recalculated; expect(recalculation.status()).toBe(200);
            const calculation = (await recalculation.json()).data;
            expect(Number(calculation.amount)).toBe(30); expect(calculation.calculation_data).toEqual(result.calculation_data);
            await editPercentage.locator('[name="notes"]').fill('Percentuale corretta e riaperta');
            await expect(editPercentage.locator('[name="amount"]')).toHaveValue('30');
            await take('percentage-edit-calculated-detail', editPercentage.locator('.modal-content'));
            const percentageSaved = page.waitForResponse(res => new URL(res.url()).pathname === `/api/instructor/${uid}/hours/${percentage.instructor_hours_id}/update`
                && res.request().method() === 'PATCH');
            await editPercentage.getByRole('button', {name: 'Salva', exact: true}).click(); expect((await percentageSaved).status()).toBe(200);
            await expect(editPercentage).not.toBeVisible(); await page.reload();
            const changedPercentage = (await hours()).find(item => item.instructor_hours_id === percentage.instructor_hours_id);
            proof('percentage_amount_after_edit', Number(changedPercentage.amount));
            expect(Number(changedPercentage.percentage_billing)).toBe(25); expect(changedPercentage.compensation_type).toBe('percentage');
            expect(changedPercentage.period).toBe(period); expect(changedPercentage.calculation_data).toEqual(result.calculation_data);
            await rowByNotes(changedPercentage.notes).locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
            await expect(editPercentage.locator('[name="percentage_billing"]')).toHaveValue('25.00');
            // Reopening must restore actual period as well as persisted database values.
            await expect(editPercentage.locator('#compensation-range')).toHaveValue(period);
            await expect(editPercentage).toContainText(course[0].title);
            await expect(editPercentage.locator('[name="amount"]')).toHaveValue('30');
            await take('percentage-edit-reopened', editPercentage.locator('.modal-content'));
            await editPercentage.locator('button[aria-label="Close"]').click();
            proof('edits_reopened_and_persisted', Number(edited.hours) === 3 && Number(edited.hourly_billing) === 18
                && changedPercentage.period === period && Number(changedPercentage.percentage_billing) === 25);
            const afterEdits = await hours(); rememberDocuments(afterEdits);
            proof('other_hours_preserved', JSON.stringify(stableHours(afterEdits.filter(item => [removable.instructor_hours_id, linked.instructor_hours_id]
                .includes(item.instructor_hours_id)))) === JSON.stringify(otherRows));
            proof('source_course_payment_preserved', JSON.stringify((await allPayments()).find(item => item.payment_id === sourceSnapshot.payment_id)) === JSON.stringify(sourceSnapshot));
            await expect(rowByNotes(linked.notes).locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled();
            await expect(rowByNotes(linked.notes).locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
            proof('linked_payment_edit_delete_disabled', await rowByNotes(linked.notes).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').isDisabled());
            await take('linked-payment-row-controls-disabled', page.locator('.datatable-table').first());
            await rowByNotes(removable.notes).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
            const confirm = page.locator('.swal2-popup'); await expect(confirm).toContainText("Vuoi eliminare l'istruttore?");
            await take('single-hours-delete-confirmation', confirm);
            const beforeCancel = (await hours()).map(stableHour);
            await confirm.getByRole('button', {name: 'Annulla', exact: true}).click();
            proof('row_delete_cancel_preserved_state', JSON.stringify((await hours()).map(stableHour)) === JSON.stringify(beforeCancel));
            await rowByNotes(removable.notes).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
            const removed = page.waitForResponse(res => new URL(res.url()).pathname === `/api/instructor/${uid}/hours/${removable.instructor_hours_id}/delete`
                && res.request().method() === 'DELETE');
            await confirm.getByRole('button', {name: 'Elimina', exact: true}).click(); expect((await removed).status()).toBe(200);
            await page.reload(); await expect(rowByNotes(linked.notes)).toBeVisible();
            await expect(rowByNotes(removable.notes)).toHaveCount(0);
            proof('deleted_hours_absent_after_reload', !(await hours()).some(item => item.instructor_hours_id === removable.instructor_hours_id));
            proof('summary_after_row_removal', Number((await info()).stats.total_amount));
            await expect(page.locator('.card-widget').filter({hasText: 'COMPENSO TOTALE'})).toContainText('104,00');
            await take('single-hours-deleted-and-summary-reopened');

            const beforeLessons = (await hours()).map(stableHour);
            const beforeLessonPayments = await allPayments();
            await open('Attività', '/#/course/list');
            await page.locator('[data-row]').filter({hasText: course[0].title}).getByText(course[0].title, {exact: true}).click();
            await page.locator('.btn-group').getByText('Calendario', {exact: true}).click();
            const day = page.locator('.ec-day').filter({has: page.locator(`time[datetime="${input.reference_date}"]`)});
            await expect(day).toBeVisible(); await day.click();
            const addLesson = page.locator('#addElement'); await expect(addLesson).toBeVisible();
            await addLesson.locator('[name="event_title"]').fill('Lezione con due istruttori');
            await addLesson.locator('[name="event_start"]').fill(input.reference_date + 'T08:00');
            await addLesson.locator('[name="event_end"]').fill(input.reference_date + 'T09:00');
            await selectInstructor(addLesson, 'Irene Ferri'); await selectInstructor(addLesson, 'Davide Costa');
            await take('new-lesson-two-instructors', addLesson.locator('.modal-content'));
            const lessonSaved = page.waitForResponse(res => new URL(res.url()).pathname === `/api/course/${owned.course}/calendar/update`
                && res.request().method() === 'POST');
            await addLesson.getByRole('button', {name: 'Crea', exact: true}).click(); expect((await lessonSaved).status()).toBe(200);
            const createdLesson = (await calendar()).events.find(item => item.title === 'Lezione con due istruttori');
            expect(createdLesson).toBeTruthy(); expect(assignmentIds(createdLesson)).toEqual([uid, secondUid].sort());
            proof('lesson_initial_instructors', assignmentIds(createdLesson).length);
            const eventUrl = input.origin + `/#/course/overview/${owned.course}/calendar?event_id=${createdLesson.event_id}`;
            await page.goto(eventUrl);
            const editLesson = page.locator('#editEventElement'); await expect(editLesson).toBeVisible();
            await page.reload(); await expect(editLesson).toBeVisible();
            await expect(editLesson).toContainText('Irene Ferri'); await expect(editLesson).toContainText('Davide Costa');
            await expect(editLesson).toContainText('08:00'); await expect(editLesson).toContainText('09:00');
            await take('new-lesson-assignment-reopened', editLesson.locator('.modal-content'));
            const calendarHours = async id => Number((await read(`instructor/${id}/lessons-hours?start_date=${italian(monthStart)}&end_date=${italian(monthEnd)}`)).data.total_hours);
            proof('calendar_hours_two_instructors', await calendarHours(uid) === 2 && await calendarHours(secondUid) === 1);
            const selectedIrene = editLesson.locator('.multi-item').filter({hasText: 'Irene Ferri'});
            await selectedIrene.locator('.multi-item-clear').click();
            await expect(selectedIrene).toHaveCount(0); await expect(editLesson).toContainText('Davide Costa');
            await take('existing-lesson-replacement-before-save', editLesson.locator('.modal-content'));
            const replacement = page.waitForResponse(res => new URL(res.url()).pathname === `/api/course/${owned.course}/calendar/update`
                && res.request().method() === 'POST');
            await editLesson.getByRole('button', {name: 'Salva', exact: true}).click(); expect((await replacement).status()).toBe(200);
            await expect(editLesson).not.toBeVisible();
            await page.goto(eventUrl); await page.reload(); await expect(editLesson).toBeVisible();
            const replaced = (await calendar()).events.find(item => item.event_id === createdLesson.event_id);
            expect(assignmentIds(replaced)).toEqual([secondUid]);
            proof('lesson_replacement_instructors', assignmentIds(replaced).length);
            proof('lesson_assignment_persisted', assignmentIds(replaced)[0] === secondUid);
            proof('lesson_times_preserved', replaced.start === createdLesson.start && replaced.end === createdLesson.end);
            await expect(editLesson.locator('.multi-item').filter({hasText: 'Irene Ferri'})).toHaveCount(0);
            await expect(editLesson).toContainText('Davide Costa');
            await take('existing-lesson-replacement-reopened', editLesson.locator('.modal-content'));
            await editLesson.getByRole('button', {name: 'Chiudi', exact: true}).first().click(); await expect(editLesson).not.toBeVisible();
            proof('replacement_calendar_hours_recomputed', await calendarHours(uid) === 1 && await calendarHours(secondUid) === 1);
            proof('lesson_did_not_create_compensation', JSON.stringify((await hours()).map(stableHour)) === JSON.stringify(beforeLessons)
                && JSON.stringify(await allPayments()) === JSON.stringify(beforeLessonPayments));

            const reader = await actor('reader');
            await reader.open('Attività', '/#/course/instructor/list/');
            await expect(instructorRow(reader.page, 'IRENE FERRI').locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
            await instructorRow(reader.page, 'IRENE FERRI').getByRole('link', {name: 'IRENE FERRI', exact: true}).click();
            const readHourly = reader.page.locator('[data-row]').filter({hasText: edited.notes});
            await expect(readHourly.locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled();
            await expect(readHourly.locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
            await take('reader-maintenance-controls-disabled', reader.page.locator('.datatable-table').first(), reader.page);
            const denials = [];
            const beforeDenials = {hours: (await hours()).map(stableHour), calendar: (await calendar()).events, payments: await allPayments()};
            for (const [route, method, data] of [
                [`instructor/${uid}/hours/${hourly.instructor_hours_id}/update`, 'PATCH', {amount: 1}],
                [`instructor/${uid}/hours/${hourly.instructor_hours_id}/delete`, 'DELETE', {}],
                [`instructor/${uid}/delete`, 'DELETE', {}],
                [`course/${owned.course}/calendar/update`, 'POST', {status: 2, events: []}],
            ]) {const res = await reader.api(route, {method, data}); expect(res.status(), route).toBe(403); denials.push(res.status());
                report.expected_denials.push({identity: 'reader', path: '/api/' + route, status: res.status()});}
            proof('reader_write_denials', denials.length);
            expect((await hours()).map(stableHour)).toEqual(beforeDenials.hours); expect((await calendar()).events).toEqual(beforeDenials.calendar);
            expect(await allPayments()).toEqual(beforeDenials.payments);

            await openCard();
            const history = await hours(); rememberDocuments(history); expect(history).toHaveLength(3);
            expect(history.find(item => item.instructor_hours_id === linked.instructor_hours_id).payment).toBe(paymentId);
            const calendarBeforeRemoval = (await calendar()).events;
            expect(assignmentIds(calendarBeforeRemoval.find(item => item.event_id === historyEvent))).toEqual([uid]);
            await expect(page.getByRole('button', {name: 'Ore a calendario: apri il dettaglio delle lezioni'})).toContainText('1,00');
            await take('instructor-history-before-removal');
            await open('Attività', '/#/course/instructor/list/');
            const targetRow = instructorRow(page, 'IRENE FERRI'); await expect(targetRow).toContainText('irene@example.test');
            await targetRow.locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click(); await expect(confirm).toContainText("Vuoi eliminare l'istruttore?");
            await take('instructor-list-delete-confirmation', confirm);
            const beforeDeleteCancel = await allInstructors(); await confirm.getByRole('button', {name: 'Annulla', exact: true}).click();
            proof('instructor_delete_cancel_preserved_state', JSON.stringify(await allInstructors()) === JSON.stringify(beforeDeleteCancel));
            await targetRow.locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
            const instructorRemoved = page.waitForResponse(res => new URL(res.url()).pathname === `/api/instructor/${uid}/delete`
                && res.request().method() === 'DELETE');
            await confirm.getByRole('button', {name: 'Elimina', exact: true}).click(); expect((await instructorRemoved).status()).toBe(200);
            owned.instructors.delete(uid); await page.reload(); await expect(targetRow).toHaveCount(0);
            proof('instructor_removed_after_reload', !(await allInstructors()).some(item => item.instructor_id === uid));
            proof('removed_instructor_hours', (await hours()).length);
            const survivingPayment = (await allPayments()).find(item => item.payment_id === paymentId);
            expect(survivingPayment).toBeTruthy();
            proof('related_payment_survives', Number(survivingPayment.amount) === Number(compensationSnapshot.amount)
                && survivingPayment.paid === compensationSnapshot.paid && survivingPayment.expense === compensationSnapshot.expense
                && survivingPayment.description === compensationSnapshot.description && survivingPayment.instructor === null);
            proof('calendar_survives_instructor_removal', JSON.stringify((await calendar()).events) === JSON.stringify(calendarBeforeRemoval));
            proof('second_instructor_preserved', (await allInstructors()).some(item => item.instructor_id === secondUid));
            await take('instructor-absent-after-reload');
        } catch (error) {originalError = error; throw error;}
        finally {
            const failures = [];
            const remove = async (route, method, statuses, data = {}) => {
                try {const res = await api(route, {method, data}); if (!statuses.includes(res.status())) failures.push(route + ': ' + res.status());}
                catch {failures.push(route + ': cleanup unavailable');}
            };
            const cleanupRead = async route => {
                try {return await read(route);} catch {failures.push(route + ': cleanup lookup unavailable'); return null;}
            };
            // Derive any newly saved IDs from the known owned parents after a failed
            // assertion. Never delete a fixture or unrelated payment/instructor.
            if (owned.course) {
                const paymentData = await cleanupRead('payment/list?pagination[perpage]=100');
                for (const payment of Object.values(paymentData?.data || {}))
                    if (payment.course?.course_id === owned.course) owned.payments.add(payment.payment_id);
                const memberships = await cleanupRead(`course-subscriptions/list?course_id=${owned.course}`);
                (memberships?.data || []).forEach(item => owned.memberships.add(item.course_subscription_id));
            }
            for (const instructorId of owned.instructors) {
                const hourData = await cleanupRead(`instructor/${instructorId}/hours/list?pagination[perpage]=100`);
                const rows = hourData?.data || []; rememberDocuments(rows);
                for (const row of rows) await remove(`instructor/${instructorId}/hours/${row.instructor_hours_id}/delete`, 'DELETE', [200, 404]);
            }
            for (const documentId of owned.documents) await remove(`document/${documentId}/delete`, 'DELETE', [200, 404]);
            for (const paymentId of owned.payments) await remove(`payment/${paymentId}/delete`, 'DELETE', [200, 404]);
            for (const membershipId of owned.memberships) await remove(`course-subscriptions/${membershipId}/delete`, 'DELETE', [204, 404]);
            if (owned.course) {
                if (owned.published) await remove(`course/${owned.course}/calendar/update`, 'DELETE', [200, 404]);
                await remove(`course/${owned.course}/delete`, 'POST', [200, 404]);
            }
            for (const instructorId of owned.instructors) await remove(`instructor/${instructorId}/delete`, 'DELETE', [200, 404]);
            if (failures.length) {report.cleanup_failures = failures; if (!originalError) throw new Error('Owned instructor maintenance cleanup failed');}
        }
        const ordered=(rows,key)=>Object.values(rows).sort((left,right)=>left[key].localeCompare(right[key]));
        expect(ordered(await allPayments(),'payment_id')).toEqual(ordered(baseline.payments,'payment_id'));
        expect(ordered(await allCourses(),'course_id')).toEqual(ordered(baseline.courses,'course_id'));
        expect(ordered((await read('subscription/list?pagination[perpage]=100')).data,'subscription_id')).toEqual(ordered(baseline.members,'subscription_id'));
        proof('baseline_records_preserved', true);
        expect(ordered(await allInstructors(),'instructor_id')).toEqual(ordered(baseline.instructors,'instructor_id'));
        proof('owned_resources_cleaned', true);
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.checks = ['real hourly and percentage PATCH values reopen after reload', 'row removal cancellation and persisted deletion with updated summary',
            'new lesson multi-assignment and existing lesson replacement persist', 'calendar hours are distinct from compensation records',
            'reader writes return 403 and leave state unchanged', 'instructor removal clears hours and FK but preserves payment and calendar JSON',
            'cleanup touches only recorded owned parents and IDs'];
        report.external_gaps = [{operation: 'external-instructor-payment', status: 'needs_external_verification'},
            {operation: 'instructor-signature-and-email-delivery', status: 'needs_external_verification'}];
    },
});
