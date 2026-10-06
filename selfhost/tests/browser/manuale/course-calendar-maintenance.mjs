import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {courseCalendarMaintenanceAuthoredWorkflows} from '../../../../docs/manuale/course-calendar-maintenance-authored-workflows.mjs';
// Seeded and frozen-clock rows share timestamps (or the list is unordered), so compare
// record sets: identical rows and contents, independent of physical row order.
const records = rows => (Array.isArray(rows) ? rows.map(row => JSON.stringify(row))
    : Object.entries(rows).map(entry => JSON.stringify(entry))).sort();

const id = 'course-calendar-maintenance';
const spec = courseCalendarMaintenanceAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async route => {const response = await api(route); expect(response.status(), route).toBe(200); return response.json();};
        const write = async (route, method, data, status = 200) => {
            const response = await api(route, {method, data}); expect(response.status(), route).toBe(status);
            return status === 204 ? null : response.json();
        };
        const courses = async () => (await read('course/list?all=1')).data;
        const payments = async () => Object.values((await read('payment/list?pagination[perpage]=100')).data);
        const members = async () => (await read('subscription/list?pagination[perpage]=100')).data;
        const instructors = async () => (await read('instructor/list')).data;
        const receipts = async () => (await read('invoice/list?pagination[perpage]=100')).data;
        const baseline = {courses: await courses(), payments: await payments(), members: await members(),
            instructors: await instructors(), receipts: await receipts(),
            calendar: (await read(`course/${input.course_id}/calendar`)).data,
            enrollments: (await read(`course-subscriptions/list?course_id=${input.course_id}`)).data};
        // The example has no issued receipts. Do not silently claim retention of
        // an invoice or PDF from a fixture that never contained one.
        expect(Object.values(baseline.receipts)).toHaveLength(0);
        const owned = {course: null, instructor: null, payments: new Set(), enrollments: new Set()};
        const title = 'Ginnastica manutenzione calendario';
        const timedTitle = 'Serie mattutina di ginnastica';
        const allDayTitle = 'Serie attività tutto il giorno';
        const facts = {};
        const proof = (field, value) => {expect(value, field).toEqual(spec.outcome.expected[field]); facts[field] = value;};
        const take = async (checkpoint, focus, currentPage = page) => {
            const index = spec.checkpoints.findIndex(item => item.id === checkpoint); expect(index).toBeGreaterThanOrEqual(0);
            await expect(currentPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await capture(currentPage, index + 1, checkpoint, focus);
        };
        const day = offset => {const date = new Date(input.reference_date + 'T12:00:00Z'); date.setUTCDate(date.getUTCDate() + offset); return date.toISOString().slice(0, 10);};
        const calendar = async () => (await read(`course/${owned.course}/calendar`)).data;
        const attendance = async () => (await read(`course/${owned.course}/attendees`)).data.events;
        const enrollments = async () => (await read(`course-subscriptions/list?course_id=${owned.course}`)).data;
        const course = async () => (await read(`course/${owned.course}/overview`)).data.course;
        const coursePayments = async () => (await payments()).filter(row => row.meta?.course_id === owned.course
            || row.course?.value === owned.course || row.course?.course_id === owned.course);
        const state = async () => ({calendar: await calendar(), attendance: await attendance(), enrollments: await enrollments(), payments: await payments()});
        const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
        const stateRecords = value => Object.fromEntries(Object.entries(value).map(([key, item]) =>
            [key, Array.isArray(item) ? records(item) : item]));
        const membersWithCurrentPaymentSummary = async () => {
            // The subscription serializer derives exactly these two fields from
            // active payments. Course preparation/removal changes those totals,
            // while every underlying member field must remain identical.
            const active = await payments();
            const expected = Object.values(baseline.members).map(member => {
                const associated = active.filter(payment => payment.associate?.associate_id === member.associate.associate_id);
                return {...member, all_payments_paid: associated.every(payment => payment.paid), payments_info: {
                    total: associated.reduce((sum, payment) => sum + Number(payment.amount), 0),
                    to_be_paid: associated.filter(payment => !payment.paid).reduce((sum, payment) => sum + Number(payment.amount), 0)}};
            });
            const sorted = rows => [...rows].sort((a, b) => a.subscription_id.localeCompare(b.subscription_id));
            return same(sorted(Object.values(await members())), sorted(expected));
        };
        const stablePayment = row => Object.fromEntries(['payment_id', 'amount', 'paid', 'payment_date', 'creation_date',
            'description', 'associate', 'expense', 'subject', 'type', 'invoice', 'sport_association', 'custom_accounts'].map(key => [key, row?.[key]]));
        const responseFor = (route, method) => page.waitForResponse(response => new URL(response.url()).pathname === '/api/' + route
            && response.request().method() === method);
        const row = (name, currentPage = page) => currentPage.locator('[data-row]').filter({hasText: name});
        const cardUrl = () => input.origin + `/#/course/overview/${owned.course}`;
        const lesson = page.locator('#editEventElement');
        const openLesson = async event => {
            const url=cardUrl() + '/calendar?event_id=' + event.event_id;
            if(page.url()===url) await page.reload(); else await page.goto(url);
            await expect(lesson).toBeVisible(); await expect(lesson.locator('[name="event_title"]')).toHaveValue(event.title);
        };
        const popup = page.locator('.swal2-popup');
        const confirmLessonRemoval = async (event, scope, checkpoint, cancel = false) => {
            await openLesson(event);
            if (checkpoint && scope !== 'Elimina') await take(checkpoint, lesson.locator('.modal-content'));
            await lesson.getByRole('button', {name: scope, exact: true}).click();
            await expect(popup).toContainText('Saranno eliminate anche le presenze');
            if (checkpoint && scope === 'Elimina') await take(checkpoint, popup);
            if (cancel) {await popup.getByRole('button', {name: 'Annulla', exact: true}).click(); return;}
            const removed = responseFor(`course/${owned.course}/calendar/update`, 'DELETE');
            await popup.getByRole('button', {name: 'Elimina', exact: true}).click(); expect((await removed).status()).toBe(200);
            await expect(popup).not.toBeVisible(); await page.reload();
        };
        let originalError;
        try {
            // Real API preparation is restricted to recorded scenario-owned parents.
            // It is not evidence for course creation, attendance marking or settlement.
            await write('course/add', 'POST', {new_course: {title, description: 'Corso dedicato alla manutenzione delle lezioni.',
                fee: '120.00', course_type: 1, multi_payments_split: true,
                events: [{id: 0, amount: '60.00', payment_date: day(1).split('-').reverse().join('/')},
                    {id: 1, amount: '60.00', payment_date: day(2).split('-').reverse().join('/')}]}, subscriptions: []});
            const created = (await courses()).filter(item => !baseline.courses.some(old => old.course_id === item.course_id));
            expect(created).toHaveLength(1); owned.course = created[0].course_id;
            const enrolled = await write('course-subscriptions/add', 'POST', input.subscription_ids.slice(0, 2)
                .map(subscription_id => ({subscription_id, course: owned.course, multi_payments: true})), 201);
            expect(enrolled).toHaveLength(2); enrolled.forEach(item => owned.enrollments.add(item.course_subscription_id));
            const target = enrolled.find(item => item.subscription?.subscription_id === input.subscription_ids[0]);
            const control = enrolled.find(item => item.subscription?.subscription_id === input.subscription_ids[1]);
            expect(target).toBeTruthy(); expect(control).toBeTruthy();
            const initialPayments = await coursePayments(); expect(initialPayments).toHaveLength(4);
            initialPayments.forEach(item => owned.payments.add(item.payment_id));
            const targetPayments = initialPayments.filter(item => item.meta?.course_subscription_id === target.course_subscription_id);
            expect(targetPayments).toHaveLength(2);
            const paidId = targetPayments[0].payment_id;
            const unpaidId = targetPayments[1].payment_id;
            await write(`payment/${paidId}/update`, 'PATCH', {paid: true, payment_date: input.reference_date + 'T10:00:00+02:00'});
            const paidSnapshot = stablePayment((await payments()).find(item => item.payment_id === paidId));
            expect(paidSnapshot.invoice).toBeNull(); expect(paidSnapshot.paid).toBe(true); expect(Number(paidSnapshot.amount)).toBe(60);
            const preparedEnrollments = await enrollments();
            const targetRates = preparedEnrollments.find(item => item.course_subscription_id === target.course_subscription_id).installments;
            expect(targetRates.map(rate => Number(rate.amount))).toEqual([60, 60]);
            expect(targetRates.filter(rate => rate.paid)).toHaveLength(1);
            expect(preparedEnrollments.find(item => item.course_subscription_id === control.course_subscription_id).installments
                .every(rate => !rate.paid && Number(rate.amount) === 60)).toBe(true);
            await write('instructor/add', 'POST', {first_name: 'Elena', last_name: 'Marini', email: 'elena@example.test'});
            const addedInstructors = (await instructors()).filter(item => !baseline.instructors.some(old => old.instructor_id === item.instructor_id));
            expect(addedInstructors).toHaveLength(1); owned.instructor = addedInstructors[0].instructor_id;
            report.fixture_preparation = {backend: 'real', owned_course: 1, standard_installment_enrollments: 2,
                installment_payments: 4, paid_installment: 'authorized fixture PATCH without receipt generation', optional_instructor: 1,
                email_requested: false, google_sync_requested: false, external_transaction: false};

            await open('Attività', '/#/course/list');
            await row(title).getByRole('link', {name: title, exact: true}).click();
            await expect(page.getByRole('heading', {name: title, exact: true}).and(page.locator('h3.card-title'))).toBeVisible();
            await expect(row('GIULIA BIANCHI')).toBeVisible(); await expect(row('LUCA VERDI')).toBeVisible();
            await take('course-card-and-owned-enrollments');
            const beforeGeneral = {enrollments: await enrollments(), payments: await payments()};
            await page.getByRole('button', {name: 'Modifica', exact: true}).filter({hasText: /^Modifica$/}).click();
            for (const [index, amount] of ['70,00', '50,00'].entries()) {
                await page.locator(`#bkn_inputmask_fee_${index}`).fill(amount); await page.locator(`#bkn_inputmask_fee_${index}`).blur();
            }
            await take('general-plan-edit', page.locator('#bkn_inputmask_fee_0').locator('xpath=ancestor::div[contains(@class,"row")][1]'));
            const generalSaved = responseFor(`course/${owned.course}/update`, 'PATCH');
            await page.getByRole('button', {name: 'Salva', exact: true}).click(); expect((await generalSaved).status()).toBe(200); await page.reload();
            const general = await course(); proof('general_plan_reopened', same(general.events.map(event => Number(event.amount)), [70, 50]));
            proof('assigned_rates_and_payments_preserved', same(await enrollments(), beforeGeneral.enrollments) && same(await payments(), beforeGeneral.payments));
            await row('GIULIA BIANCHI').getByRole('button', {name: 'Pagamenti', exact: true}).click();
            const paymentModal = page.locator('#payments-modal-' + input.subscription_ids[0]);
            await expect(paymentModal).toBeVisible(); await expect(paymentModal.locator('[data-row]')).toHaveCount(2);
            await take('general-plan-reopened-individual-payments-unchanged', paymentModal.locator('.modal-content'));
            await paymentModal.getByRole('button', {name: 'Chiudi', exact: true}).first().click(); await expect(paymentModal).not.toBeVisible();
            await page.getByRole('button', {name: 'Presenze', exact: true}).click();
            expect(await attendance()).toHaveLength(0);
            await page.locator('.btn-group').getByText('Calendario', {exact: true}).click();
            await expect(page.locator('#course_attendance_calendar')).toBeVisible();
            expect((await calendar()).google_sync_enabled).toBe(false);

            // The displayed month must not become the generator's start date.
            await page.locator('#course_attendance_calendar button.ec-next').click();
            await page.getByRole('button', {name: 'Crea lezioni periodiche', exact: true}).click();
            await page.getByPlaceholder('Nome', {exact: true}).fill(timedTitle);
            await page.locator('[name="event_date"]').fill(day(5));
            const emptyCalendar = await calendar();
            await page.getByRole('button', {name: 'Crea lezioni periodiche', exact: true}).click();
            await expect(page.getByText('Seleziona almeno un giorno e una data finale valida.', {exact: true})).toBeVisible();
            expect(await calendar()).toEqual(emptyCalendar);
            for (const name of ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'])
                await setCheckbox(page.locator(`#days-checkboxes [name="${name}"]`), true);
            await page.locator('[name="event_date"]').fill(input.reference_date);
            await page.getByRole('button', {name: 'Crea lezioni periodiche', exact: true}).click();
            await expect(page.getByText('Seleziona almeno un giorno e una data finale valida.', {exact: true}).first()).toBeVisible();
            proof('invalid_periodic_requests_preserved_state', same(await calendar(), emptyCalendar));
            await page.locator('[name="event_date"]').fill(day(5));
            await take('periodic-name-and-weekdays', page.locator('#days-checkboxes').locator('xpath=ancestor::div[contains(@class,"col-md-3")][1]'));
            await setCheckbox(page.locator('[name="event_allday"]'), false);
            await page.locator('[name="event_start"]').fill('09:00'); await page.locator('[name="event_end"]').fill('10:00');
            const instructorSelect = page.locator('.form-group').filter({has: page.locator('label').filter({hasText: /^Istruttore lezione$/})});
            await instructorSelect.locator('input:not([type="hidden"])').fill('Elena');
            await page.locator('.list-item').filter({hasText: 'Elena Marini'}).click();
            await take('periodic-exclusive-end-and-times', page.locator('[name="event_date"]').locator('xpath=ancestor::div[contains(@class,"col-md-3")][1]'));
            const generated = responseFor(`course/${owned.course}/calendar/update`, 'POST');
            await page.getByRole('button', {name: 'Crea lezioni periodiche', exact: true}).click(); expect((await generated).status()).toBe(200);
            await page.reload();
            const timed = (await calendar()).events; proof('timed_series_count', timed.length);
            expect(timed.map(event => event.start.slice(0, 10))).toEqual([day(0), day(1), day(2), day(3), day(4)]);
            expect(new Set(timed.map(event => event.extendedProps.groupId)).size).toBe(1);
            expect(new Set(timed.map(event => event.event_id)).size).toBe(5);
            proof('periodic_start_is_current_day', timed[0].start.slice(0, 10) === input.reference_date);
            proof('exclusive_end_preserved', !timed.some(event => event.start.slice(0, 10) === day(5)));
            const localTime = value => new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Rome', hour: '2-digit', minute: '2-digit'}).format(new Date(value));
            expect(timed.every(event => !event.allDay && localTime(event.start) === '09:00' && localTime(event.end) === '10:00')).toBe(true);
            proof('optional_instructor_persisted', timed.every(event => event.extendedProps.instructor?.some(item => item.instructor_id === owned.instructor)));
            await expect(page.locator('.ec-event').filter({hasText: timedTitle}).first()).toBeVisible();
            await take('periodic-series-reopened', page.locator('#course_attendance_calendar'));
            const seriesAttendance = await attendance(); expect(seriesAttendance).toHaveLength(5);
            const selectedAttendance = seriesAttendance.find(item => item.start === timed[2].start);
            expect(selectedAttendance).toBeTruthy();
            await write(`course/${owned.course}/attendees/${selectedAttendance.attendance_day_id}/update`, 'POST',
                {attendees: [{course_subscription_id: target.course_subscription_id}]});
            report.fixture_preparation.saved_attendance_for_deleted_lesson = 1;

            await page.getByRole('button', {name: 'Crea lezioni periodiche', exact: true}).click();
            await page.getByPlaceholder('Nome', {exact: true}).fill(allDayTitle);
            for (const name of ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'])
                await setCheckbox(page.locator(`#days-checkboxes [name="${name}"]`), true);
            await page.locator('[name="event_date"]').fill(day(3)); await setCheckbox(page.locator('[name="event_allday"]'), true);
            await expect(page.locator('[name="event_start"]')).toBeDisabled(); await expect(page.locator('[name="event_end"]')).toBeDisabled();
            await take('all-day-periodic-before-save', page.locator('[name="event_date"]').locator('xpath=ancestor::div[contains(@class,"col-md-3")][1]'));
            const allDaySaved = responseFor(`course/${owned.course}/calendar/update`, 'POST');
            await page.getByRole('button', {name: 'Crea lezioni periodiche', exact: true}).click(); expect((await allDaySaved).status()).toBe(200);
            await page.reload();
            const allDay = (await calendar()).events.filter(event => event.title === allDayTitle);
            proof('all_day_series_count', allDay.length);
            expect(allDay.map(event => event.start.slice(0, 10))).toEqual([day(0), day(1), day(2)]);
            proof('all_day_without_instructor_persisted', allDay.every(event => event.allDay === true && event.start.endsWith('T00:00:00.000Z')
                && (!event.extendedProps.instructor || event.extendedProps.instructor.length === 0)));
            expect(allDay[0].extendedProps.groupId).not.toBe(timed[0].extendedProps.groupId);
            const beforeLessonChanges = {payments: await payments(), enrollments: await enrollments()};

            // Exercise an actual calendar event click before using the supported
            // event_id link to select later dates unambiguously across month edges.
            await expect(page.locator('svg').filter({has: page.locator('title#loading-aria')})).toHaveCount(0);
            await page.locator('#course_attendance_calendar .ec-event').filter({hasText: timedTitle}).first().click();
            await expect(lesson).toBeVisible(); await expect(lesson).toContainText('09:00');
            await lesson.getByRole('button', {name: 'Chiudi', exact: true}).first().click(); await expect(lesson).not.toBeVisible();
            await openLesson(timed[2]);
            await lesson.locator('[name="event_title"]').fill('Lezione corretta di ginnastica');
            await lesson.locator('[name="description"]').fill('Descrizione corretta della sola lezione.');
            await take('lesson-edit-before-save', lesson.locator('.modal-content'));
            const edited = responseFor(`course/${owned.course}/calendar/update`, 'POST');
            await lesson.getByRole('button', {name: 'Salva', exact: true}).click(); expect((await edited).status()).toBe(200);
            await page.reload();
            const changed = (await calendar()).events.find(event => event.event_id === timed[2].event_id);
            expect(changed).toMatchObject({title: 'Lezione corretta di ginnastica', start: timed[2].start, end: timed[2].end,
                extendedProps: {groupId: timed[2].extendedProps.groupId, description: 'Descrizione corretta della sola lezione.'}});
            await openLesson(changed); await expect(lesson.locator('[name="description"]')).toHaveValue(changed.extendedProps.description);
            await take('lesson-edit-reopened', lesson.locator('.modal-content')); await lesson.getByRole('button', {name: 'Chiudi', exact: true}).first().click(); await expect(lesson).not.toBeVisible();
            proof('lesson_edit_reopened', changed.event_id === timed[2].event_id && changed.title === 'Lezione corretta di ginnastica');
            const byEventId=events=>[...events].sort((left,right)=>left.event_id.localeCompare(right.event_id));
            expect(byEventId((await calendar()).events.filter(event => event.event_id !== changed.event_id))).toEqual(
                byEventId([...timed.filter(event => event.event_id !== changed.event_id), ...allDay]));
            proof('other_series_events_preserved', true);
            const beforeCancel = await state();
            const editedAttendance = beforeCancel.attendance.find(item => item.attendance_day_id === selectedAttendance.attendance_day_id);
            proof('edited_attendance_title_and_presence_preserved', editedAttendance.title === changed.title
                && editedAttendance.start === timed[2].start
                && same(editedAttendance.attendees, [{course_subscription_id: target.course_subscription_id}]));
            expect(beforeCancel.attendance.find(item => item.attendance_day_id === selectedAttendance.attendance_day_id).attendees)
                .toEqual([{course_subscription_id: target.course_subscription_id}]);
            await confirmLessonRemoval(changed, 'Elimina', 'lesson-single-delete-confirmation', true);
            proof('lesson_delete_cancel_preserved_state', same(await state(), beforeCancel));
            await confirmLessonRemoval(changed, 'Elimina');
            const remainingAfterSingle = (await calendar()).events;
            const attendanceAfterSingle = await attendance();
            proof('single_delete_removed_only_selected_attendance', remainingAfterSingle.length === 7
                && !remainingAfterSingle.some(event => event.event_id === changed.event_id)
                && same(attendanceAfterSingle, beforeCancel.attendance.filter(item => item.attendance_day_id !== selectedAttendance.attendance_day_id)));
            await confirmLessonRemoval(timed[3], 'Questo e successivi', 'lesson-following-scope');
            proof('following_delete_preserved_earlier_and_other_group', same((await calendar()).events.map(event => event.event_id).sort(),
                [timed[0], timed[1], ...allDay].map(event => event.event_id).sort())
                && same(await attendance(), attendanceAfterSingle.filter(item => ![timed[3].start, timed[4].start].includes(item.start))));
            await confirmLessonRemoval(timed[1], 'Questo e precedenti', 'lesson-preceding-scope');
            expect(byEventId((await calendar()).events)).toEqual(byEventId(allDay));
            const byAttendanceId=rows=>[...rows].sort((left,right)=>left.attendance_day_id.localeCompare(right.attendance_day_id));
            expect(byAttendanceId(await attendance())).toEqual(byAttendanceId(attendanceAfterSingle.filter(item => allDay.some(event => event.start === item.start))));
            proof('preceding_delete_preserved_other_group', true);
            await page.getByRole('button', {name: 'Presenze', exact: true}).click(); await page.reload();
            await expect(page.locator('#bkn_content').getByText(allDayTitle, {exact: true})).toHaveCount(3);
            await take('calendar-and-register-after-scoped-deletions');
            proof('lesson_changes_preserved_payments_and_enrollments', same(await payments(), beforeLessonChanges.payments)
                && same(await enrollments(), beforeLessonChanges.enrollments));

            const reader = await actor('reader');
            await reader.page.goto(cardUrl() + '/calendar?event_id=' + allDay[0].event_id);
            const readerLesson = reader.page.locator('#editEventElement'); await expect(readerLesson).toBeVisible();
            await expect(readerLesson.getByRole('button', {name: 'Salva', exact: true})).toBeDisabled();
            await expect(readerLesson.getByRole('button', {name: 'Elimina', exact: true})).toBeDisabled();
            await expect(readerLesson.getByRole('button', {name: 'Questo e successivi', exact: true})).toHaveCount(0);
            proof('reader_controls_disabled', await readerLesson.getByRole('button', {name: 'Salva', exact: true}).isDisabled());
            await take('reader-calendar-controls-disabled', readerLesson.locator('.modal-content'), reader.page);
            await readerLesson.getByRole('button', {name: 'Chiudi', exact: true}).first().click(); await expect(readerLesson).not.toBeVisible();
            await expect(reader.page.getByRole('button', {name: 'Crea lezioni periodiche', exact: true})).toHaveCount(0);
            await reader.page.getByRole('button', {name: 'Iscritti al corso', exact: true}).click();
            await expect(row('GIULIA BIANCHI', reader.page).locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
            const beforeDenials = await state();
            const remainingAttendance = (await attendance())[0];
            let denials = 0;
            for (const [route, method, data] of [
                [`course/${owned.course}/update`, 'PATCH', {title: 'Modifica non consentita'}],
                [`course/${owned.course}/disable`, 'POST', {}], [`course/${owned.course}/delete`, 'POST', {}],
                [`course/${owned.course}/calendar/update`, 'POST', {status: 2, events: []}],
                [`course/${owned.course}/calendar/update`, 'DELETE', {event_id: allDay[0].event_id}],
                [`course-subscriptions/${target.course_subscription_id}/delete`, 'DELETE', {}],
                [`course/${owned.course}/attendees/${remainingAttendance.attendance_day_id}/update`, 'POST', {attendees: []}],
            ]) {const denied = await reader.api(route, {method, data}); expect(denied.status(), route).toBe(403); denials++;
                report.expected_denials.push({identity: 'reader', path: '/api/' + route, status: denied.status()});}
            proof('reader_write_denials', denials); expect(stateRecords(await state())).toEqual(stateRecords(beforeDenials));

            await page.goto(cardUrl()); await row('GIULIA BIANCHI').getByRole('button', {name: 'Pagamenti', exact: true}).click();
            await expect(paymentModal).toBeVisible(); await expect(paymentModal.locator('[data-row]')).toHaveCount(2);
            await take('athlete-installments-and-payments-before-removal', paymentModal.locator('.modal-content'));
            await paymentModal.getByRole('button', {name: 'Chiudi', exact: true}).first().click(); await expect(paymentModal).not.toBeVisible();
            const controlSnapshot = (await enrollments()).find(item => item.course_subscription_id === control.course_subscription_id);
            await row('GIULIA BIANCHI').locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
            await expect(popup).toContainText("Eliminare l'iscrizione?"); await expect(popup).toContainText('rate non pagate');
            await take('athlete-removal-confirmation', popup);
            const beforeMemberCancel = await state(); await popup.getByRole('button', {name: 'Annulla', exact: true}).click();
            proof('athlete_delete_cancel_preserved_state', same(await state(), beforeMemberCancel));
            await row('GIULIA BIANCHI').locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
            const removedEnrollment = responseFor(`course-subscriptions/${target.course_subscription_id}/delete`, 'DELETE');
            await popup.getByRole('button', {name: 'Elimina', exact: true}).click(); expect((await removedEnrollment).status()).toBe(204); await page.reload();
            await expect(row('GIULIA BIANCHI')).toHaveCount(0); await expect(row('LUCA VERDI')).toBeVisible();
            proof('athlete_removed_after_reload', !(await enrollments()).some(item => item.course_subscription_id === target.course_subscription_id));
            proof('control_enrollment_preserved', same((await enrollments())[0], controlSnapshot));
            proof('association_members_preserved', await membersWithCurrentPaymentSummary());
            const removedTarget = (await read(`course-subscriptions/list?course_id=${owned.course}&include_deleted=true`)).data
                .find(item => item.course_subscription_id === target.course_subscription_id);
            expect(removedTarget).toBeTruthy();
            proof('athlete_unpaid_rate_removed_paid_amount_retained', removedTarget.installments.length === 0
                && !(await payments()).some(item => item.payment_id === unpaidId)
                && same(stablePayment((await payments()).find(item => item.payment_id === paidId)), paidSnapshot));
            await take('athlete-removed-control-enrollment-preserved');
            await open('Pagamenti', '/#/payment/list'); await page.reload();
            const paidRow = row(paidSnapshot.description).filter({hasText: /GIULIA|Giulia/}); await expect(paidRow).toBeVisible();
            await expect(paidRow.locator('button:is([title="Annulla pagamento"],[data-original-title="Annulla pagamento"])')).toBeVisible();
            await take('paid-payment-after-athlete-removal', paidRow);

            const beforeArchive = await state();
            await open('Attività', '/#/course/list');
            await expect(row(title).locator('button:is([title="Elimina Corso"],[data-original-title="Elimina Corso"])')).toBeDisabled();
            await row(title).locator('button:is([title="Nascondi il corso dal profilo dell\'associazione archiviandolo"],[data-original-title="Nascondi il corso dal profilo dell\'associazione archiviandolo"])').click();
            await expect(popup).toContainText('Vuoi spostare in archivio il corso?'); await take('archive-course-confirmation', popup);
            await popup.getByRole('button', {name: 'Annulla', exact: true}).click();
            proof('archive_cancel_preserved_state', (await course()).status_flag === 2 && same(await state(), beforeArchive));
            await row(title).locator('button:is([title="Nascondi il corso dal profilo dell\'associazione archiviandolo"],[data-original-title="Nascondi il corso dal profilo dell\'associazione archiviandolo"])').click();
            const archived = responseFor(`course/${owned.course}/disable`, 'POST');
            await popup.getByRole('button', {name: 'Sposta in archivio', exact: true}).click(); expect((await archived).status()).toBe(200);
            await page.getByRole('button', {name: 'Archivio', exact: true}).click(); await page.reload();
            await expect(row(title)).toBeVisible(); await expect(row(title)).toContainText('non visibile');
            await expect(row(title).locator('button:is([title="Elimina Corso"],[data-original-title="Elimina Corso"])')).toBeEnabled();
            proof('archive_preserved_related_data', (await course()).status_flag === 1 && same(await state(), beforeArchive));
            await take('archived-course-before-deletion', row(title));
            await reader.page.goto(input.origin + '/#/course/archive');
            await expect(row(title, reader.page).locator('button:is([title="Elimina Corso"],[data-original-title="Elimina Corso"])')).toBeDisabled();
            await row(title).locator('button:is([title="Elimina Corso"],[data-original-title="Elimina Corso"])').click();
            await expect(popup).toContainText('Vuoi eliminare definitivamente il corso?'); await take('course-delete-confirmation', popup);
            const beforeCourseCancel = await state(); await popup.getByRole('button', {name: 'Annulla', exact: true}).click();
            proof('course_delete_cancel_preserved_state', same(await state(), beforeCourseCancel) && (await course()).status_flag === 1);
            const remainingUnpaidIds = (await coursePayments()).filter(item => !item.paid).map(item => item.payment_id);
            expect(remainingUnpaidIds).toHaveLength(2);
            expect((await coursePayments()).filter(item => !item.paid).every(item => item.meta?.course_subscription_id === control.course_subscription_id)).toBe(true);
            await row(title).locator('button:is([title="Elimina Corso"],[data-original-title="Elimina Corso"])').click(); const removedCourse = responseFor(`course/${owned.course}/delete`, 'POST');
            await popup.getByRole('button', {name: 'Elimina', exact: true}).click(); expect((await removedCourse).status()).toBe(200); await page.reload();
            await expect(row(title)).toHaveCount(0);
            proof('course_absent_after_reload', !(await courses()).some(item => item.course_id === owned.course));
            expect(await enrollments()).toHaveLength(0);
            expect((await read(`course-subscriptions/list?course_id=${owned.course}&include_deleted=true`)).data).toHaveLength(0);
            proof('course_unpaid_rates_removed_paid_amount_retained', !(await payments()).some(item => remainingUnpaidIds.includes(item.payment_id))
                && same(stablePayment((await payments()).find(item => item.payment_id === paidId)), paidSnapshot));
            await take('course-absent-after-reload');
            await open('Pagamenti', '/#/payment/list'); await page.reload(); await expect(paidRow).toBeVisible();
            await expect(paidRow.locator('button:is([title="Annulla pagamento"],[data-original-title="Annulla pagamento"])')).toBeVisible();
            await take('paid-payment-after-course-deletion', paidRow);
            proof('receipts_absent_and_unchanged', same(await receipts(), baseline.receipts));
            report.observation_limits = ['Standard course with two installment-based enrollments; paid fixture has no issued receipt',
                'No membership renewal, carnet, single-fee, refund, receipt/PDF retention or external synchronization claim',
                'Lesson editor changes title and description; dates and times are consulted, not changed',
                'Source review identifies attendance registry cascade on course deletion; inaccessible deleted endpoints are not queried as live proof'];
        } catch (error) {originalError = error; throw error;}
        finally {
            const failures = [];
            const cleanup = async (route, method, statuses) => {try {const response = await api(route, {method, data: {}});
                if (!statuses.includes(response.status())) failures.push(route + ': ' + response.status());}
                catch {failures.push(route + ': cleanup unavailable');}};
            // Include partial preparatory mutations only when their known parent
            // or unique scenario title proves ownership. Never clean fixture IDs.
            try {
                if (!owned.course) owned.course = (await courses()).find(item => item.title === title
                    && !baseline.courses.some(old => old.course_id === item.course_id))?.course_id || null;
                if (owned.course) {
                    (await coursePayments()).forEach(item => owned.payments.add(item.payment_id));
                    (await enrollments()).forEach(item => owned.enrollments.add(item.course_subscription_id));
                }
                if (!owned.instructor) owned.instructor = (await instructors()).find(item => item.email === 'elena@example.test'
                    && !baseline.instructors.some(old => old.instructor_id === item.instructor_id))?.instructor_id || null;
            } catch {failures.push('owned-parent cleanup discovery unavailable');}
            for (const paymentId of owned.payments) await cleanup(`payment/${paymentId}/delete`, 'DELETE', [200, 404]);
            for (const enrollmentId of owned.enrollments) await cleanup(`course-subscriptions/${enrollmentId}/delete`, 'DELETE', [204, 404]);
            if (owned.course) await cleanup(`course/${owned.course}/delete`, 'POST', [200, 404]);
            if (owned.instructor) await cleanup(`instructor/${owned.instructor}/delete`, 'DELETE', [200, 404]);
            if (failures.length) {report.cleanup_failures = failures; if (!originalError) throw new Error('Owned course-calendar cleanup failed');}
        }
        const ordered=(rows,key)=>Object.values(rows).sort((left,right)=>left[key].localeCompare(right[key]));
        expect(ordered(await courses(),'course_id')).toEqual(ordered(baseline.courses,'course_id'));
        expect(ordered(await payments(),'payment_id')).toEqual(ordered(baseline.payments,'payment_id'));
        expect(ordered(await members(),'subscription_id')).toEqual(ordered(baseline.members,'subscription_id'));
        expect(await receipts()).toEqual(baseline.receipts);
        expect((await read(`course/${input.course_id}/calendar`)).data).toEqual(baseline.calendar);
        expect((await read(`course-subscriptions/list?course_id=${input.course_id}`)).data).toEqual(baseline.enrollments);
        proof('baseline_records_preserved', true);
        expect(ordered(await instructors(),'instructor_id')).toEqual(ordered(baseline.instructors,'instructor_id'));
        proof('owned_resources_cleaned', true);
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.checks = ['real periodic generator persists current-day start, exclusive end, times, all-day and optional instructor',
            'single edit and single/before/after deletions reopen with exact calendar and attendance membership',
            'cancellation and reader denials preserve saved state', 'assigned plans and accounting records are checked separately',
            'Standard enrollment and course deletion remove unpaid installments and preserve the recorded paid amount',
            'cleanup touches only owned IDs and baseline resources remain unchanged'];
        report.external_gaps = [{operation: 'google-calendar-and-public-calendar-sharing', status: 'needs_external_verification'}];
    },
});
