// Real UI handlers and persisted API reads; the run owner executes this file.
import {scenario, expect} from './scenario.mjs';
import {campsCalendarSources} from './camps-calendar-sources.mjs';
// Seeded and frozen-clock rows share timestamps (or the list is unordered), so compare
// record sets: identical rows and contents, independent of physical row order.
const records = rows => (Array.isArray(rows) ? rows.map(row => JSON.stringify(row))
    : Object.entries(rows).map(entry => JSON.stringify(entry))).sort();

await scenario({id: 'camps-calendar-manage', prefix: 'images/camp-calendario/gestione',
    sources: campsCalendarSources, actions: async ({page, api, actor, open, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const json = async (route, client = api) => {
            const response = await client(route);
            expect(response.status()).toBe(200);
            return response.json();
        };
        const take = async (number, checkpoint, locator, browserPage = page) => {
            await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            await capture(browserPage, number, checkpoint, locator, {blurEditableFocus: true});
        };
        const save = async (route, method, button, status = 200) => {
            const response = page.waitForResponse(result => new URL(result.url()).pathname === '/api/' + route
                && result.request().method() === method);
            await button.click();
            const result = await response;
            expect(result.status()).toBe(status);
            return result.json();
        };
        const choosePeriod = async (start, end) => {
            await page.locator('#camps_period_range').click();
            const picker = page.locator('.drp-panel');
            await expect(picker).toBeVisible();
            const september = picker.locator('.month-grid').filter({has: page.getByText('Settembre 2026', {exact: true})});
            await september.getByRole('button', {name: String(start), exact: true}).click();
            await september.getByRole('button', {name: String(end), exact: true}).click();
            await picker.getByRole('button', {name: 'Applica', exact: true}).click();
            await expect(page.locator('#camps_and_retreats_periods_form [name="start_date"]')).toHaveValue(`${String(start).padStart(2, '0')}/09/2026`);
            await expect(page.locator('#camps_and_retreats_periods_form [name="end_date"]')).toHaveValue(`${String(end).padStart(2, '0')}/09/2026`);
        };
        const originalPayments = (await json('payment/list?pagination[perpage]=100')).data;
        expect((await json('camps-and-retreats/list')).data).toEqual([]);
        await open('Attività', '/#/course/camps-and-retreats/list');
        // The heading includes its explanatory subtitle in the accessible name.
        await expect(page.getByRole('heading', {name: /^Camp e Ritiri\b/})).toBeVisible();
        await take(1, 'camp-list-empty-and-create-control');
        await page.getByRole('button', {name: 'Camp e Ritiri', exact: true}).click();
        const campForm = page.locator('#camps_and_retreats_form');
        await campForm.locator('[name="title"]').fill('Ritiro Aurora 2026');
        await campForm.locator('[name="description"]').fill('Due giornate di allenamento per il gruppo Aurora.');
        await take(2, 'camp-title-description-before-create', page.locator('#camps-and-retreats .modal-content'));
        const created = await save('camps-and-retreats/add', 'POST', campForm.getByRole('button', {name: 'Crea', exact: true}), 201);
        const campId = created.camp_and_retreat.camps_and_retreats_id;
        await expect(page).toHaveURL(new RegExp('/overview/' + campId));
        await page.reload();
        await expect(page.getByRole('heading', {name: 'Ritiro Aurora 2026', exact: true})).toBeVisible();
        const camp = (await json(`camps-and-retreats/${campId}/info`)).data;
        expect(camp.title).toBe('Ritiro Aurora 2026');
        expect(camp.description).toBe('Due giornate di allenamento per il gruppo Aurora.');
        expect(camp.periods).toEqual([]);
        await take(3, 'camp-created-and-public-link-visible-after-reload');
        await page.getByRole('button', {name: 'Periodo', exact: true}).click();
        const periodForm = page.locator('#camps_and_retreats_periods_form');
        await choosePeriod(28, 29);
        await periodForm.locator('[name="fee"]').fill('80,00');
        await periodForm.locator('[name="max_participants"]').fill('20');
        await periodForm.locator('[name="title"]').fill('Due giornate Aurora');
        await periodForm.locator('[name="description"]').fill('Allenamento e preparazione atletica.');
        await take(4, 'period-selected-range-fee-capacity-before-create', page.locator('#camps-and-retreats-add-modal .modal-content'));
        const addedPeriod = await save('camps-and-retreats/periods/add', 'POST', periodForm.getByRole('button', {name: 'Crea', exact: true}), 201);
        const periodId = addedPeriod.period.camps_and_retreats_period_id;
        expect(addedPeriod.period.start_date.slice(0, 10)).toBe('2026-09-28');
        expect(addedPeriod.period.end_date.slice(0, 10)).toBe('2026-09-29');
        await Promise.all([page.waitForResponse(response => new URL(response.url()).pathname === `/api/camps-and-retreats/${campId}/info`), page.reload()]);
        await expect(page.locator('.font-weight-boldest.font-size-h4').filter({hasText: 'Due giornate Aurora'})).toBeVisible();
        expect((await json(`camps-and-retreats/${campId}/info`)).data.periods).toHaveLength(1);
        await take(5, 'created-period-and-dates-persist-after-reload');
        await page.getByRole('link', {name: 'Dettagli', exact: true}).click();
        await expect(periodForm.locator('[name="title"]')).toHaveValue('Due giornate Aurora');
        await choosePeriod(29, 30);
        await periodForm.locator('[name="title"]').fill('Due giornate aggiornate');
        await periodForm.locator('[name="fee"]').fill('90,00');
        await periodForm.locator('[name="max_participants"]').fill('');
        await periodForm.locator('[name="description"]').fill('Allenamento e preparazione aggiornati.');
        await take(6, 'period-updated-dates-fee-and-unlimited-capacity-before-save');
        await save(`camps-and-retreats/periods/${periodId}/update`, 'PATCH', page.getByRole('button', {name: 'Salva', exact: true}));
        await page.reload();
        const period = (await json(`camps-and-retreats/periods/${periodId}/info`)).data;
        expect(period.start_date).toBe('29/09/2026');
        expect(period.end_date).toBe('30/09/2026');
        expect(period.title).toBe('Due giornate aggiornate');
        expect(period.fee.replace(',', '.')).toBe('90.00');
        expect(period.max_participants).toBeNull();
        await expect(periodForm.locator('[name="title"]')).toHaveValue(period.title);
        await expect(page.locator('#camps_period_range')).toHaveValue('29/09/2026 al 30/09/2026');
        await take(7, 'updated-period-persists-after-reload');
        await page.getByRole('button', {name: 'Servizio', exact: true}).click();
        const serviceForm = page.locator('#camps_and_retreats_services_form');
        await serviceForm.locator('[name="fee"]').fill('15,00');
        await serviceForm.locator('[name="payment_category"]').selectOption(input.payment_category_id);
        await serviceForm.locator('[name="title"]').fill('Pranzo');
        await serviceForm.locator('[name="description"]').fill('Pranzo dopo l’allenamento.');
        await take(8, 'service-title-fee-income-category-before-create', page.locator('#camps-and-retreats-add-modal .modal-content'));
        const addedService = await save('camps-and-retreats/periods/services/add', 'POST', serviceForm.getByRole('button', {name: 'Crea', exact: true}), 201);
        const serviceId = addedService.service.camps_and_retreats_period_service_id;
        await page.reload();
        await expect(page.locator('.font-weight-boldest.font-size-h4').filter({hasText: 'Pranzo'})).toBeVisible();
        expect((await json(`camps-and-retreats/periods/${periodId}/info`)).data.services[0].payment_category).toBe(input.payment_category_id);
        await page.locator('.font-weight-boldest.font-size-h4').filter({hasText: 'Pranzo'}).scrollIntoViewIfNeeded();
        await take(9, 'service-created-and-income-category-persists-after-reload');
        await page.getByRole('button', {name: 'Modifica', exact: true}).click();
        await serviceForm.locator('[name="title"]').fill('Pranzo completo');
        await serviceForm.locator('[name="fee"]').fill('18,00');
        await take(10, 'service-edit-title-cost-and-delete-control', page.locator('#camps-and-retreats-add-modal .modal-content'));
        await save(`camps-and-retreats/periods/services/${serviceId}/update`, 'PATCH', serviceForm.getByRole('button', {name: 'Salva', exact: true}));
        await page.reload();
        const editedService = (await json(`camps-and-retreats/periods/${periodId}/info`)).data.services[0];
        expect(editedService.title).toBe('Pranzo completo');
        expect(editedService.fee.replace(',', '.')).toBe('18.00');
        await expect(page.locator('.font-weight-boldest.font-size-h4').filter({hasText: 'Pranzo completo'})).toBeVisible();
        await page.locator('.font-weight-boldest.font-size-h4').filter({hasText: 'Pranzo completo'}).scrollIntoViewIfNeeded();
        await take(11, 'edited-service-persists-after-reload');
        await page.getByRole('button', {name: 'Modifica', exact: true}).click();
        await save(`camps-and-retreats/periods/services/${serviceId}/delete`, 'DELETE', serviceForm.getByRole('button', {name: 'Elimina', exact: true}));
        await page.reload();
        expect((await json(`camps-and-retreats/periods/${periodId}/info`)).data.services).toEqual([]);
        await expect(page.locator('.font-weight-boldest.font-size-h4').filter({hasText: 'Pranzo completo'})).toHaveCount(0);
        await page.getByRole('heading', {name: 'Servizi della settimana', exact: true}).scrollIntoViewIfNeeded();
        await take(12, 'deleted-service-absent-after-reload');
        expect((await json(`camps-and-retreats/${campId}/subscriptions/list`)).data).toEqual([]);
        expect(records((await json('payment/list?pagination[perpage]=100')).data)).toEqual(records(originalPayments));
        const reader = await actor('reader');
        await reader.open('Attività', '/#/course/camps-and-retreats/list');
        await expect(reader.page.getByRole('button', {name: 'Camp e Ritiri', exact: true})).toHaveCount(0);
        await reader.page.getByRole('link', {name: 'Ritiro Aurora 2026', exact: true}).click();
        await expect(reader.page.getByRole('button', {name: 'Periodo', exact: true})).toHaveCount(0);
        await reader.page.getByRole('link', {name: 'Dettagli', exact: true}).click();
        await expect(reader.page.getByRole('button', {name: 'Salva', exact: true})).toHaveCount(0);
        await expect(reader.page.getByRole('button', {name: 'Servizio', exact: true})).toHaveCount(0);
        await take(13, 'reader-consults-period-without-create-or-save-controls', undefined, reader.page);
        const deniedCamp = await reader.api(`camps-and-retreats/periods/${periodId}/update`, {method: 'PATCH', data: {title: 'Non salvare'}});
        expect(deniedCamp.status()).toBe(403);
        expect((await json(`camps-and-retreats/periods/${periodId}/info`)).data.title).toBe(period.title);
        await open('Calendario', '/#/calendar');
        await expect(page.getByRole('heading', {name: 'Eventi e Promemoria', exact: true})).toBeVisible();
        await expect(page.locator('#general_calendar .ec-toolbar')).toBeVisible();
        await expect(page.getByRole('button', {name: 'lista giorno', exact: true})).toHaveClass(/ec-active/);
        await take(14, 'general-calendar-default-day-list-and-toolbar');
        const beforeViews = (await json('calendar/events?get_lesson=false')).data.events;
        await page.getByRole('button', {name: 'mese', exact: true}).click();
        await expect(page.getByRole('button', {name: 'mese', exact: true})).toHaveClass(/ec-active/);
        const calendarTitle = page.locator('#general_calendar .ec-title');
        const currentMonth = (await calendarTitle.textContent()).trim();
        await page.locator('#general_calendar button.ec-prev').click();
        await expect(calendarTitle).not.toHaveText(currentMonth);
        await page.locator('#general_calendar button.ec-next').click();
        await expect(calendarTitle).toHaveText(currentMonth);
        await page.locator('#general_calendar button.ec-next').click();
        await expect(calendarTitle).not.toHaveText(currentMonth);
        await page.getByRole('button', {name: 'Oggi', exact: true}).click();
        await expect(calendarTitle).toHaveText(currentMonth);
        await take(15, 'general-calendar-month-view-and-navigation');
        await page.getByRole('button', {name: 'settimana', exact: true}).click();
        await expect(page.getByRole('button', {name: 'settimana', exact: true})).toHaveClass(/ec-active/);
        await take(16, 'general-calendar-week-view');
        await page.getByRole('button', {name: 'giorno', exact: true}).click();
        await expect(page.getByRole('button', {name: 'giorno', exact: true})).toHaveClass(/ec-active/);
        await take(17, 'general-calendar-day-view');
        await page.getByRole('button', {name: 'lista settimana', exact: true}).click();
        await expect(page.getByRole('button', {name: 'lista settimana', exact: true})).toHaveClass(/ec-active/);
        await take(18, 'general-calendar-week-list-view');
        await page.getByRole('button', {name: 'lista giorno', exact: true}).click();
        await page.locator('#general_calendar button.ec-prev').click();
        await page.getByRole('button', {name: 'Oggi', exact: true}).click();
        const modal = page.locator('#addElement');
        await expect(page.getByRole('button', {name: 'lista giorno', exact: true})).toHaveClass(/ec-active/);
        expect(records((await json('calendar/events?get_lesson=false')).data.events)).toEqual(records(beforeViews));
        const fillEvent = async (title, description) => {
            await page.getByRole('button', {name: 'Nuovo evento', exact: true}).click();
            await expect(modal).toBeVisible();
            await modal.locator('[name="event_title"]').fill(title);
            await modal.locator('[name="event_start"]').fill('2026-09-30T10:00');
            await modal.locator('[name="event_end"]').fill('2026-09-30T11:00');
            await modal.locator('[name="description"]').fill(description);
        };
        await fillEvent('Riunione organizzativa Aurora', 'Preparazione del ritiro in segreteria.');
        await take(19, 'global-event-name-dates-description-without-course-or-reminder', modal.locator('.modal-content'));
        await save('calendar/events/update', 'POST', modal.getByRole('button', {name: 'Salva', exact: true}));
        await page.reload();
        const events = (await json('calendar/events?get_lesson=false')).data.events;
        const globalEvent = events.find(event => event.title === 'Riunione organizzativa Aurora');
        expect(globalEvent).toBeTruthy();
        expect(globalEvent.extendedProps.course).toBeFalsy();
        expect(globalEvent.extendedProps.reminder_enabled).toBe(false);
        await expect(page.locator('#general_calendar .ec-event').filter({hasText: globalEvent.title})).toBeVisible();
        await take(20, 'global-event-persists-after-reload');
        await page.locator('#general_calendar .ec-event').filter({hasText: globalEvent.title}).click();
        await expect(modal.locator('[name="event_start"]')).toBeDisabled();
        await modal.locator('[name="event_title"]').fill('Riunione Aurora aggiornata');
        await modal.locator('[name="description"]').fill('Controllo del programma e dei materiali.');
        await take(21, 'global-event-edit-title-description-and-fixed-dates', modal.locator('.modal-content'));
        await save('calendar/events/update', 'POST', modal.getByRole('button', {name: 'Salva', exact: true}));
        await page.reload();
        const updatedGlobal = (await json('calendar/events?get_lesson=false')).data.events.find(event => event.event_id === globalEvent.event_id);
        expect(updatedGlobal.title).toBe('Riunione Aurora aggiornata');
        expect(updatedGlobal.extendedProps.description).toBe('Controllo del programma e dei materiali.');
        expect(updatedGlobal.start).toBe(globalEvent.start);
        expect(updatedGlobal.end).toBe(globalEvent.end);
        await expect(page.locator('#general_calendar .ec-event').filter({hasText: updatedGlobal.title})).toBeVisible();
        await take(22, 'updated-global-event-persists-after-reload');
        await fillEvent('Lezione Aurora dal calendario', 'Lezione collegata a Ginnastica per tutti.');
        await modal.getByPlaceholder('Seleziona il corso', {exact: true}).fill('Ginnastica per tutti');
        await modal.getByText('Ginnastica per tutti', {exact: true}).click();
        await take(23, 'new-event-with-course-selected-before-save', modal.locator('.modal-content'));
        await save(`course/${input.course_id}/calendar/update`, 'POST', modal.getByRole('button', {name: 'Salva', exact: true}));
        await page.reload();
        const courseCalendar = (await json(`course/${input.course_id}/calendar`)).data;
        expect(courseCalendar.events).toHaveLength(1);
        expect(courseCalendar.events[0].title).toBe('Lezione Aurora dal calendario');
        expect(courseCalendar.status).toBe(2);
        const combined = (await json('calendar/events?get_lesson=false')).data.events;
        expect(combined.map(event => event.title)).toEqual(expect.arrayContaining([updatedGlobal.title, courseCalendar.events[0].title]));
        await expect(page.locator('#general_calendar .ec-event').filter({hasText: courseCalendar.events[0].title})).toBeVisible();
        await take(24, 'global-calendar-shows-persisted-course-lesson-and-global-event');
        const downloaded = page.waitForEvent('download');
        await page.getByRole('button', {name: 'Esporta', exact: true}).click();
        const download = await downloaded;
        expect(download.suggestedFilename()).toBe('2026-09-30_calendario_completo.ics');
        const stream = await download.createReadStream();
        let ics = '';
        for await (const chunk of stream) ics += chunk.toString('utf8');
        expect(ics).toContain('BEGIN:VCALENDAR');
        expect(ics).toContain('Lezione Aurora dal calendario');
        expect(ics).not.toContain(updatedGlobal.title);
        await page.goto(input.origin + `/#/course/overview/${input.course_id}/calendar`);
        await expect(page.getByRole('heading', {name: 'Calendario delle lezioni', exact: true})).toBeVisible();
        await page.locator('#course_attendance_calendar').scrollIntoViewIfNeeded();
        await expect(page.locator('#course_attendance_calendar .ec-event').filter({hasText: courseCalendar.events[0].title})).toBeVisible();
        await take(25, 'course-calendar-contains-lesson-created-from-general-calendar');
        await page.getByText('Condividi', {exact: true}).click();
        await expect(page.locator('#share-link')).toBeVisible();
        await expect(page.locator('#share-link textarea.text-left')).toHaveValue(new RegExp('/#/shared-calendar/' + input.course_id));
        await take(26, 'course-share-modal-direct-link-embed-whatsapp-and-email-controls', page.locator('#share-link .modal-content'));
        const anonymous = await api(`course/${input.course_id}/calendar`, {headers: {Authorization: ''}});
        expect(anonymous.status()).toBe(200);
        expect((await anonymous.json()).data.events[0].title).toBe(courseCalendar.events[0].title);
        await page.locator('#share-link').getByRole('button', {name: 'Chiudi', exact: true}).first().click();
        await reader.open('Calendario', '/#/calendar');
        await expect(reader.page.getByRole('button', {name: 'Nuovo evento', exact: true})).toHaveCount(0);
        await reader.page.locator('#general_calendar .ec-event').filter({hasText: updatedGlobal.title}).click();
        await expect(reader.page.locator('#addElement').getByRole('button', {name: 'Salva', exact: true})).toBeDisabled();
        await expect(reader.page.locator('#addElement').getByRole('button', {name: 'Elimina', exact: true})).toBeDisabled();
        await take(27, 'reader-sees-global-event-with-disabled-save-and-delete', reader.page.locator('#addElement .modal-content'), reader.page);
        const deniedEvent = await reader.api('calendar/events/update', {method: 'POST', data: {action: 'update', events: [{...updatedGlobal, title: 'Non salvare'}]}});
        expect(deniedEvent.status()).toBe(403);
        expect((await json('calendar/events?get_lesson=false')).data.events.find(event => event.event_id === globalEvent.event_id).title).toBe(updatedGlobal.title);
        await open('Calendario', '/#/calendar');
        await page.locator('#general_calendar .ec-event').filter({hasText: updatedGlobal.title}).click();
        await modal.getByRole('button', {name: 'Elimina', exact: true}).click();
        await expect(page.locator('.swal2-popup')).toContainText("Vuoi eliminare l'evento a calendario?");
        await save('calendar/events/update', 'POST', page.locator('.swal2-popup').getByRole('button', {name: 'Elimina', exact: true}));
        await page.reload();
        const afterDelete = (await json('calendar/events?get_lesson=false')).data.events;
        expect(afterDelete.some(event => event.event_id === globalEvent.event_id)).toBe(false);
        expect(afterDelete.some(event => event.title === courseCalendar.events[0].title)).toBe(true);
        await expect(page.locator('#general_calendar .ec-event').filter({hasText: updatedGlobal.title})).toHaveCount(0);
        await take(28, 'deleted-global-event-absent-course-lesson-preserved-after-reload');
        report.camps_calendar_manage = {camp_title: 'Ritiro Aurora 2026', camp_persisted_after_reload: true,
            period_initial_start: '2026-09-28', period_initial_end: '2026-09-29',
            period_updated_start: '2026-09-29', period_updated_end: '2026-09-30', period_fee: '90.00',
            period_unlimited_capacity: true, period_persisted_after_reload: true,
            service_initial_fee: '15.00', service_updated_fee: '18.00', service_category_preserved: true,
            service_persisted_after_reload: true, service_deleted_after_reload: true, no_camp_enrollments_or_payments: true,
            reader_camp_update_status: deniedCamp.status(), global_event_created_after_reload: true,
            global_event_updated_after_reload: true, global_event_dates_preserved: true,
            global_event_deleted_after_reload: true, course_lesson_persisted_after_reload: true,
            course_calendar_published: true, course_lesson_survived_global_deletion: true,
            views_exercised: 'month,week,day,listDay,listWeek', anonymous_course_calendar_status: anonymous.status(),
            view_selection_buttons_active: true, previous_next_today_navigation_preserves_events: true,
            share_panel_exercised: true, ics_download_contains_course_lesson: true, ics_download_excludes_global_event: true,
            reader_global_update_status: deniedEvent.status(), reminders_enabled: false,
            camp_registration_exercised: false, attendance_write_exercised: false, print_dialog_exercised: false,
            external_share_dispatch_exercised: false, google_link_export_revoke_exercised: false};
        report.external_gaps = [{operation: 'google-calendar-oauth-export-revocation', status: 'needs_external_verification'},
            {operation: 'calendar-reminder-delivery', status: 'needs_external_verification'},
            {operation: 'calendar-share-whatsapp-email-dispatch', status: 'needs_external_verification'}];
        report.checks = ['actual camp and period UI POST/PATCH persist selected dates, fee and blank capacity',
            'service UI create/update/delete persists without enrollment/payment changes',
            'global event UI create/update/delete preserves the published course lesson',
            'course calendar and anonymous API read expose the same saved course lesson',
            'real ICS download contains course lessons and excludes global events',
            'read-only collaborator cannot mutate camp periods or global events'];
    }});
