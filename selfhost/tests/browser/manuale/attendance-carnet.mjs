import {scenario, expect} from './scenario.mjs';
import {attendanceCarnetSources} from './attendance-carnet-sources.mjs';
import {fileURLToPath} from 'node:url';

export async function runAttendanceCarnet({page, api, open, actor, input, capture, report}) {
    expect(input.fixture_version).toBe(8);
    expect(input.fixture_profile).toBe('baseline');
    const get = async endpoint => {
        const response = await api(endpoint);
        expect(response.status()).toBe(200);
        return (await response.json()).data;
    };
    const listRegistrations = () => get(`course-subscriptions/list?course_id=${input.course_id}`);
    const listPayments = async () => Object.values(await get('payment/list'));
    const courseUrl = `/#/course/overview/${input.course_id}`;
    expect(await listRegistrations()).toHaveLength(0);
    expect(Object.values(await get('carnet/list'))).toHaveLength(0);
    expect((await get(`course/${input.course_id}/attendees`)).events).toHaveLength(0);
    const initialPayments = await listPayments();
    expect(initialPayments).toHaveLength(3);
    await open('Attività', '/#/course/list');
    await page.getByRole('link', {name: 'Ginnastica per tutti', exact: true}).click();
    await expect(page).toHaveURL(input.origin + courseUrl);
    await page.getByRole('button', {name: 'Aggiungi tesserato', exact: true}).click();
    const enrollment = page.getByRole('dialog', {name: 'Iscrivi al corso', exact: true});
    await enrollment.getByPlaceholder("Seleziona l'atleta", {exact: true}).fill('Giulia');
    await enrollment.getByText('GIULIA BIANCHI (Anno corrente)', {exact: true}).click();
    await capture(page, 1, 'course-enrollment-selected-current-member', enrollment.locator('.modal-content'));
    const enrollmentSaved = page.waitForResponse(r => r.url().endsWith('/course-subscriptions/add') && r.request().method() === 'POST');
    await enrollment.getByRole('button', {name: 'Aggiungi', exact: true}).click();
    expect((await enrollmentSaved).status()).toBe(201);
    await page.reload();
    const enrolled = await listRegistrations();
    expect(enrolled).toHaveLength(1);
    const registration = enrolled[0];
    expect(registration.subscription.subscription_id).toBe(input.subscription_ids[0]);
    const coursePayment = (await listPayments()).find(p => !initialPayments.some(original => p.payment_id === original.payment_id));
    expect(Number(coursePayment.amount)).toBe(120);
    expect(coursePayment.paid).toBe(false);
    await expect(page.locator('[data-row]').filter({hasText: /Giulia|GIULIA/})).toBeVisible();
    await capture(page, 2, 'course-enrollment-persists-and-course-fee-created');

    await open('Attività', '/#/course/carnet/list/');
    await page.locator('.card-toolbar').getByRole('link', {name: 'Carnet', exact: true}).click();
    await expect(page.getByText('Nuovo Carnet', {exact: true})).toBeVisible();
    await page.locator('#bkn_form input[name="title"]').fill('Carnet ginnastica 5 lezioni');
    await page.locator('#bkn_form textarea[name="description"]').fill('Pacchetto dimostrativo di cinque lezioni.');
    await page.locator('#bkn_form input[name="fee"]').fill('50,00');
    await page.locator('#bkn_form input[type="number"]').fill('5');
    await capture(page, 3, 'filled-five-lesson-carnet-wizard');
    await page.locator('#next-step').click();
    await expect(page.locator('#bkn_select2_athletes')).toBeAttached();
    // Leave initial assignment empty, then exercise the separate assignment modal.
    await page.locator('#subscription_submit').click();
    const confirmation = page.getByRole('dialog').filter({hasText: 'Hai inserito i dati corretti?'});
    const created = page.waitForResponse(r => r.url().endsWith('/carnet/add') && r.request().method() === 'POST');
    await confirmation.getByRole('button', {name: 'Continua', exact: true}).click();
    expect((await created).status()).toBe(200);
    const carnets = Object.values(await get('carnet/list'));
    expect(carnets).toHaveLength(1);
    const carnet = carnets[0];
    expect(carnet.title).toBe('Carnet ginnastica 5 lezioni');
    expect(carnet.lessons_number).toBe(5);
    expect(Number(carnet.fee)).toBe(50);
    expect(carnet.public).toBe(true);
    expect(await listPayments()).toHaveLength(4);
    await expect(page.getByRole('link', {name: carnet.title, exact: true})).toBeVisible();
    await capture(page, 4, 'created-public-carnet-without-assignment-payment');
    await page.getByRole('link', {name: carnet.title, exact: true}).click();
    const details = page.locator('.card').filter({has: page.locator('input[name="title"]')});
    const visibility = details.locator('input[type="checkbox"]');
    await expect(visibility).toBeChecked();
    await visibility.locator('..').click();
    await expect(visibility).not.toBeChecked();
    await capture(page, 5, 'make-carnet-private-in-details');
    const visibilitySaved = page.waitForResponse(r => r.url().endsWith(`/carnet/${carnet.carnet_id}/update`) && r.request().method() === 'PATCH');
    await page.getByRole('button', {name: 'Salva', exact: true}).click();
    expect((await visibilitySaved).status()).toBe(200);
    expect((await get(`carnet/${carnet.carnet_id}/info`)).public).toBe(false);
    await page.reload();
    await expect(visibility).not.toBeChecked();
    const usageHref = `/#/course/carnet/list/detail/${carnet.carnet_id}/usage`;
    const usage = async () => {
        await page.goto(input.origin + usageHref);
        await expect(page.getByText('Utilizzo del Carnet', {exact: true})).toBeVisible();
    };
    await page.getByRole('link', {name: 'Utilizzo', exact: true}).click();
    const [availableMembers] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === '/api/subscription/list'
            && new URL(response.url()).searchParams.get('m') === '1'),
        page.getByRole('button', {name: 'Assegna carnet', exact: true}).click(),
    ]);
    expect(availableMembers.status()).toBe(200);
    const assignment = page.getByRole('dialog', {name: 'Assegna un carnet', exact: true});
    await assignment.getByRole('textbox', {name: 'Associa carnet a', exact: true}).fill('Giulia');
    const memberOption = assignment.locator('.list-item').getByText('Giulia Bianchi', {exact: true});
    await expect(memberOption).toHaveCount(1);
    await memberOption.click();
    await capture(page, 6, 'select-member-for-new-carnet-assignment', assignment.locator('.modal-content'));
    const assigned = page.waitForResponse(r => r.url().endsWith(`/carnet/${carnet.carnet_id}/assign/${input.subscription_ids[0]}`) && r.request().method() === 'POST');
    await assignment.getByRole('button', {name: 'Assegna', exact: true}).click();
    expect((await assigned).status()).toBe(200);
    await usage();
    const carnetInfo = () => get(`carnet/${carnet.carnet_id}/info`);
    const initialAssignment = (await carnetInfo()).subscriptions[0];
    expect(initialAssignment.subscription.subscription_id).toBe(input.subscription_ids[0]);
    expect(initialAssignment.meta).toEqual({lessons_counter: 5, lessons_left: 5, lessons_registry: []});
    expect(initialAssignment.course).toEqual([]);
    expect(await listPayments()).toHaveLength(5);
    const carnetPayment = (await listPayments()).find(p => p.payment_id === initialAssignment.payment);
    expect(Number(carnetPayment.amount)).toBe(50);
    expect(carnetPayment.paid).toBe(false);
    await expect(page.locator('[data-row]').filter({hasText: 'Giulia Bianchi'})).toContainText('5/5');
    await capture(page, 7, 'assigned-carnet-five-of-five-with-unpaid-payment');
    await page.getByRole('button', {name: /collega corso/}).click();
    const linkModal = page.getByRole('dialog').filter({hasText: 'Assegna carnet ad un corso'});
    await linkModal.getByText('Ginnastica per tutti', {exact: true}).click();
    await capture(page, 8, 'select-enrolled-course-for-carnet', linkModal.locator('.modal-content'));
    const linked = page.waitForResponse(r => r.url().endsWith(`/carnet/${carnet.carnet_id}/assign/${input.subscription_ids[0]}`) && r.request().method() === 'POST');
    // A successful assignment calls window.location.reload() itself; a concurrent
    // page.reload() would abort one of the two navigations. Let the app's finish first.
    const reloadedByApp = page.waitForEvent('load');
    await linkModal.getByRole('button', {name: 'Assegna carnet', exact: true}).click();
    expect((await linked).status()).toBe(200);
    await reloadedByApp;
    await expect(page.getByText('Utilizzo del Carnet', {exact: true})).toBeVisible();
    await page.reload();
    const linkedAssignment = (await carnetInfo()).subscriptions[0];
    expect(linkedAssignment.course).toHaveLength(1);
    expect(linkedAssignment.course[0].course_subscription_id).toBe(registration.course_subscription_id);
    expect(await listPayments()).toHaveLength(5);
    await expect(page.locator('[data-row]').filter({hasText: 'Giulia Bianchi'})).toContainText('Ginnastica per tutti');
    await capture(page, 9, 'linked-course-and-five-lessons-persist');

    await open('Pagamenti', '/#/payment/list');
    await page.locator(`#action-col-${carnetPayment.payment_id} button:is([title="Segna come pagato"],[data-original-title="Segna come pagato"])`).click();
    const paymentModal = page.getByRole('dialog', {name: 'Incassare il pagamento?', exact: true});
    await paymentModal.getByText('Genera ricevuta', {exact: true}).click();
    await expect(paymentModal.locator('input[id^="generate_invoice_"]')).not.toBeChecked();
    await expect(paymentModal.locator('input[id^="send_receipt_email_"]')).not.toBeChecked();
    await paymentModal.locator('input[name="payment_date"]').fill(input.reference_date);
    await capture(page, 10, 'approve-carnet-payment-without-pdf-or-email', paymentModal);
    const approved = page.waitForResponse(r => r.url().endsWith(`/payment/${carnetPayment.payment_id}/approve`) && r.request().method() === 'POST');
    await paymentModal.getByRole('button', {name: 'Incassa', exact: true}).click();
    expect((await approved).status()).toBe(200);
    expect((await listPayments()).find(p => p.payment_id === carnetPayment.payment_id).paid).toBe(true);
    const paymentDrawer = page.locator('.drawer').filter({hasText: 'Dettagli Pagamento'});
    await expect(paymentDrawer).toBeVisible();
    await paymentDrawer.locator('button.close').click();
    await expect(paymentDrawer).not.toBeVisible();

    await page.goto(input.origin + courseUrl + '/calendar');
    await expect(page.locator('.ec-day').filter({has: page.locator(`time[datetime="${input.reference_date}"]`)})).toBeVisible();
    await page.locator('.ec-day').filter({has: page.locator(`time[datetime="${input.reference_date}"]`)}).click();
    const lesson = page.locator('#addElement');
    await expect(lesson).toBeVisible();
    await lesson.locator('input[name="event_title"]').fill('Lezione dimostrativa di ginnastica');
    await lesson.locator('input[name="event_start"]').fill(input.reference_date + 'T08:00');
    await lesson.locator('input[name="event_end"]').fill(input.reference_date + 'T09:00');
    await capture(page, 11, 'create-single-lesson-with-date-and-time', lesson.locator('.modal-content'));
    const calendarSaved = page.waitForResponse(r => r.url().endsWith(`/course/${input.course_id}/calendar/update`) && r.request().method() === 'POST');
    await lesson.getByRole('button', {name: 'Crea', exact: true}).click();
    expect((await calendarSaved).status()).toBe(200);
    const calendar = await get(`course/${input.course_id}/calendar`);
    expect(calendar.status).toBe(2);
    expect(calendar.events).toHaveLength(1);
    await page.getByRole('button', {name: 'Presenze', exact: true}).click();
    const event = (await get(`course/${input.course_id}/attendees`)).events[0];
    expect(event.title).toBe('Lezione dimostrativa di ginnastica');
    expect(event.attendees).toEqual([]);
    await expect(page.locator('#bkn_content').getByText(event.title, {exact: true})).toBeVisible();
    await capture(page, 12, 'published-single-lesson-in-attendance-register');
    const openAttendance = async () => {
        await page.locator(`a[data-target="#attendance-day-${event.attendance_day_id}"]`).click();
        const dialog = page.locator(`#attendance-day-${event.attendance_day_id}`);
        await expect(dialog).toBeVisible();
        await expect(dialog.getByText('Bianchi Giulia', {exact: true})).toBeVisible();
        return dialog;
    };
    const attendanceModal = await openAttendance();
    const checkin = page.waitForResponse(r => r.url().endsWith(`/course/${input.course_id}/attendees/${event.attendance_day_id}/update`) && r.request().method() === 'POST');
    await attendanceModal.locator('input[type="checkbox"]').locator('..').click();
    expect((await checkin).status()).toBe(200);
    await expect(attendanceModal.locator('input[type="checkbox"]')).toBeChecked();
    await capture(page, 13, 'manual-attendance-saved-for-giulia', attendanceModal.locator('.modal-content'));
    await attendanceModal.getByText('Chiudi', {exact: true}).click();
    const present = (await get(`course/${input.course_id}/attendees`)).events[0];
    expect(present.attendees).toEqual([{course_subscription_id: registration.course_subscription_id}]);
    const consumed = (await carnetInfo()).subscriptions[0];
    expect(consumed.meta.lessons_left).toBe(4);
    expect(consumed.meta.lessons_registry).toHaveLength(1);
    expect(consumed.meta.lessons_registry[0]).toMatchObject({attendance_day_id: event.attendance_day_id,
        course: {id: input.course_id, title: 'Ginnastica per tutti'}, title: event.title});
    expect(new Date(consumed.meta.lessons_registry[0].date).toISOString()).toBe(new Date(present.date).toISOString());
    await usage();
    await expect(page.locator('[data-row]').filter({hasText: 'Giulia Bianchi'})).toContainText('4/5');
    await capture(page, 14, 'carnet-consumed-four-of-five-persists-after-reload');
    await page.goto(input.origin + courseUrl + '/attendance');
    const correction = await openAttendance();
    await expect(correction.locator('input[type="checkbox"]')).toBeChecked();
    const removed = page.waitForResponse(r => r.url().endsWith(`/course/${input.course_id}/attendees/${event.attendance_day_id}/update`) && r.request().method() === 'POST');
    await correction.locator('input[type="checkbox"]').locator('..').click();
    expect((await removed).status()).toBe(200);
    await expect(correction.locator('input[type="checkbox"]')).not.toBeChecked();
    await correction.getByText('Chiudi', {exact: true}).click();
    await usage();
    await page.reload();
    const restored = (await carnetInfo()).subscriptions[0];
    expect(restored.meta).toEqual({lessons_counter: 5, lessons_left: 5, lessons_registry: []});
    expect((await get(`course/${input.course_id}/attendees`)).events[0].attendees).toEqual([]);
    await expect(page.locator('[data-row]').filter({hasText: 'Giulia Bianchi'})).toContainText('5/5');
    await capture(page, 15, 'removed-attendance-restores-five-lessons-and-clears-usage');

    const reader = await actor('reader');
    await reader.open('Attività', '/#/course/carnet/list/');
    await expect(reader.page.getByRole('link', {name: carnet.title, exact: true})).toBeVisible();
    await expect(reader.page.locator('a[href$="/course/carnet/add"]')).toHaveCount(0);
    await reader.page.getByRole('link', {name: carnet.title, exact: true}).click();
    await expect(reader.page.getByRole('button', {name: 'Salva', exact: true})).toBeDisabled();
    await reader.page.getByRole('link', {name: 'Utilizzo', exact: true}).click();
    await expect(reader.page.getByRole('button', {name: 'Assegna carnet', exact: true})).toBeDisabled();
    await reader.page.goto(input.origin + courseUrl + '/attendance');
    await reader.page.locator(`a[data-target="#attendance-day-${event.attendance_day_id}"]`).click();
    await expect(reader.page.locator(`#attendance-day-${event.attendance_day_id} input[type="checkbox"]`)).toBeDisabled();
    const denials = [
        ['course-subscriptions/add', 'POST', [{course: input.course_id, subscription_id: input.subscription_ids[1]}]],
        ['carnet/add', 'POST', {title: 'Negato', description: 'Negato', fee: 1, lessons_number: 1, subscriptions: []}],
        [`carnet/${carnet.carnet_id}/update`, 'PATCH', {title: 'Negato'}],
        [`carnet/${carnet.carnet_id}/assign/${input.subscription_ids[1]}`, 'POST', {}],
        [`course/${input.course_id}/calendar/update`, 'POST', {status: 2, events: []}],
        [`course/${input.course_id}/attendees/${event.attendance_day_id}/update`, 'POST', {attendees: present.attendees}],
    ];
    for (const [endpoint, method, data] of denials) {
        const response = await reader.api(endpoint, {method, data});
        expect(response.status()).toBe(403);
    }
    expect(await listRegistrations()).toHaveLength(1);
    expect((await carnetInfo()).subscriptions).toHaveLength(1);
    expect((await carnetInfo()).subscriptions[0].meta).toEqual(restored.meta);
    expect((await get(`course/${input.course_id}/attendees`)).events[0].attendees).toEqual([]);
    expect((await get(`course/${input.course_id}/calendar`)).events).toHaveLength(1);
    expect(await listPayments()).toHaveLength(5);
    expect((await listPayments()).find(p => p.payment_id === coursePayment.payment_id).paid).toBe(false);
    report.attendance_carnet_workflow = {initial_registrations: 0, final_registrations: 1,
        initial_carnets: 0, final_carnets: 1, initial_assignments: 0, final_assignments: 1,
        course_fee: 120, course_payment_paid: false, carnet_fee: 50, carnet_private: true, privacy_persisted_after_reload: true,
        carnet_payment_paid: true, payment_count: 5, link_creates_payment: false,
        calendar_published: true, lesson_count: 1, lessons_initial: 5, lessons_after_checkin: 4,
        usage_after_checkin: 1, lessons_after_removal: 5, usage_after_removal: 0,
        attendance_after_removal: 0, persisted_after_reload: true, reader_write_denials: 6,
        denial_left_state_unchanged: true, email_sent: false, automatic_attendance_tested: false};
    report.checks = ['actual course enrollment and separate 120 euro unpaid course fee',
        'real five-lesson carnet creation, public default and private edit',
        'actual assignment creates its separate 50 euro payment; course link creates no second payment',
        'real payment approval with PDF and email explicitly unchecked',
        'single lesson UI save publishes calendar and creates attendance row',
        'manual check-in persists and consumes exactly one lesson with real usage record',
        'manual removal restores exactly one lesson and clears usage record after reload',
        'read-only controls disabled and six API write denials leave persisted records unchanged'];
    return {carnet, carnetPayment, coursePayment, registration, event, courseUrl, usageHref,
        carnetInfo, get, listPayments, openAttendance};
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
    await scenario({id: 'attendance-carnet-manual', prefix: 'images/registro-presenze/carnet-manuale',
        sources: attendanceCarnetSources, actions: runAttendanceCarnet});
}
