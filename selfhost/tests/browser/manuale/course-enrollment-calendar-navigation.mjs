import {scenario, expect} from './scenario.mjs';
import {campsPersonasAuthoredWorkflows} from '../../../../docs/manuale/camps-personas-authored-workflows.mjs';
// Seeded and frozen-clock rows share timestamps (or the list is unordered), so compare
// record sets: identical rows and contents, independent of physical row order.
const records = rows => (Array.isArray(rows) ? rows.map(row => JSON.stringify(row))
    : Object.entries(rows).map(entry => JSON.stringify(entry))).sort();
const id='course-enrollment-calendar-navigation',spec=campsPersonasAuthoredWorkflows[id];
await scenario({id,prefix:spec.prefix.replace(/\/$/,''),sources:spec.sources,actions:async({page,api,open,actor,input,capture,report})=>{
    expect(input.fixture_version).toBe(8);expect(input.fixture_profile||'baseline').toBe('baseline');
    const read=async route=>{const res=await api(route);expect(res.status(),route).toBe(200);return(await res.json()).data;};
    const courses=()=>read('course/list?all=1'),payments=async()=>Object.values(await read('payment/list?pagination[perpage]=100'));
    const members=()=>read('subscription/list?pagination[perpage]=100');
    const baseline={courses:await courses(),payments:await payments(),members:await members(),calendar:await read(`course/${input.course_id}/calendar`)};
    const owned={course:null,enrollment:null,payments:new Set()},title='Corso iscritti e presenze Aurora',lessonTitle='Lezione accessibile dal calendario';
    const facts={},proof=(key,value)=>{expect(value,key).toEqual(spec.outcome.expected[key]);facts[key]=value;};
    const responseFor=(route,method)=>page.waitForResponse(res=>new URL(res.url()).pathname==='/api/'+route&&res.request().method()===method);
    const enrollments=()=>read(`course-subscriptions/list?course_id=${owned.course}`),calendar=()=>read(`course/${owned.course}/calendar`),attendance=async()=>(await read(`course/${owned.course}/attendees`)).events;
    const take=async(cp,focus,browserPage=page)=>{await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0,{timeout:15000});await capture(browserPage,spec.checkpoints.findIndex(item=>item.id===cp)+1,cp,focus);};
    const card=()=>input.origin+`/#/course/overview/${owned.course}`,row=()=>page.locator('[data-row]').filter({hasText:'GIULIA BIANCHI'});
    const generalLesson=async(browserPage=page)=>{await browserPage.goto(input.origin+'/#/calendar');await browserPage.reload();await expect(browserPage.locator('#general_calendar')).toBeVisible();
        await browserPage.locator('.ec-event').filter({hasText:lessonTitle}).click();const modal=browserPage.locator('#addElement');await expect(modal).toBeVisible();await expect(modal.getByRole('button',{name:'Gestisci Presenze',exact:true})).toBeVisible();return modal;};
    let event,error;
    try{
        expect((await api('course/add',{method:'POST',data:{new_course:{title,description:'Corso dedicato alle iscrizioni e alla navigazione delle presenze.',fee:'120.00',course_type:1,multi_payments_split:false,events:[]},subscriptions:[]}})).status()).toBe(200);
        const created=(await courses()).filter(item=>!baseline.courses.some(old=>old.course_id===item.course_id));expect(created).toHaveLength(1);owned.course=created[0].course_id;
        report.fixture_preparation={backend:'real',owned_standard_course:1,single_fee:120,enrollments_prepared:0,lessons_prepared:0};
        await open('Attività','/#/course/list');await page.getByRole('link',{name:title,exact:true}).click();await page.getByRole('button',{name:'Aggiungi tesserato',exact:true}).click();
        const enroll=page.getByRole('dialog',{name:'Iscrivi al corso',exact:true});await enroll.getByPlaceholder("Seleziona l'atleta",{exact:true}).fill('Giulia');await enroll.getByText('GIULIA BIANCHI (Anno corrente)',{exact:true}).click();
        await take('course-current-athlete-selected-before-enrollment',enroll.locator('.modal-content'));
        const saved=responseFor('course-subscriptions/add','POST');await enroll.getByRole('button',{name:'Aggiungi',exact:true}).click();expect((await saved).status()).toBe(201);await page.reload();
        const registrations=await enrollments();expect(registrations).toHaveLength(1);owned.enrollment=registrations[0].course_subscription_id;expect(registrations[0].subscription.subscription_id).toBe(input.subscription_ids[0]);
        const addedPayments=(await payments()).filter(item=>!baseline.payments.some(old=>old.payment_id===item.payment_id));expect(addedPayments).toHaveLength(1);addedPayments.forEach(item=>owned.payments.add(item.payment_id));
        proof('ui_enrollment_saved_with_unpaid120',Number(addedPayments[0].amount)===120&&addedPayments[0].paid===false);await expect(row()).toBeVisible();await take('course-athlete-and-unpaid-fee-after-reload');
        await page.goto(card()+'/calendar');await page.locator('.ec-day').filter({has:page.locator(`time[datetime="${input.reference_date}"]`)}).click();
        const lesson=page.locator('#addElement');await expect(lesson).toBeVisible();await lesson.locator('[name="event_title"]').fill(lessonTitle);
        await lesson.locator('[name="event_start"]').fill(input.reference_date+'T08:00');await lesson.locator('[name="event_end"]').fill(input.reference_date+'T09:00');await take('course-lesson-ready-for-publication',lesson.locator('.modal-content'));
        const published=responseFor(`course/${owned.course}/calendar/update`,'POST');await lesson.getByRole('button',{name:'Crea',exact:true}).click();expect((await published).status()).toBe(200);await page.reload();
        const cal=await calendar();expect(cal.events).toHaveLength(1);event=(await attendance())[0];expect(event.title).toBe(lessonTitle);expect(event.attendees).toEqual([]);
        proof('lesson_ui_save_publishes_calendar_and_register',cal.status===2&&event.title===lessonTitle);await page.getByRole('button',{name:'Presenze',exact:true}).click();await expect(page.locator('#bkn_content').getByText(lessonTitle,{exact:true})).toBeVisible();await take('course-published-lesson-in-register');
        expect(cal.events[0].event_id).toBeTruthy();await page.goto(card()+'/calendar?event_id='+cal.events[0].event_id);const detail=page.locator('#editEventElement');await expect(detail).toBeVisible();await expect(detail.locator('[name="event_title"]')).toHaveValue(lessonTitle);
        proof('real_event_direct_route_opens_correct_course_lesson',true);await take('course-direct-event-route-opens-correct-lesson',detail.locator('.modal-content'));
        await detail.getByRole('button',{name:'Chiudi',exact:true}).click();await expect(detail).not.toBeVisible();
        const general=await generalLesson();await expect(general.locator('[name="event_title"]')).toHaveValue(lessonTitle);await take('general-calendar-lesson-has-attendance-navigation',general.locator('.modal-content'));
        await general.getByRole('button',{name:'Gestisci Presenze',exact:true}).click();const mark=page.locator('#attendance-day-'+event.attendance_day_id);await expect(mark).toBeVisible();
        const attendee=mark.locator('.list-item-attendees').filter({hasText:'Bianchi Giulia'});await expect(attendee).toHaveCount(1);await expect(attendee.locator('input[type="checkbox"]')).not.toBeChecked();
        const checked=responseFor(`course/${owned.course}/attendees/${event.attendance_day_id}/update`,'POST');await attendee.locator('input[type="checkbox"]').locator('..').click();expect((await checked).status()).toBe(200);await expect(attendee.locator('input[type="checkbox"]')).toBeChecked();
        await take('general-calendar-presence-saved-for-correct-person',mark.locator('.modal-content'));await mark.getByText('Chiudi',{exact:true}).click();await expect(mark).not.toBeVisible();
        const reopened=await generalLesson();await reopened.getByRole('button',{name:'Gestisci Presenze',exact:true}).click();await expect(page.locator('#attendance-day-'+event.attendance_day_id+' input[type="checkbox"]')).toBeChecked();
        const persisted=(await attendance())[0];proof('general_calendar_attendance_persists_in_same_register',JSON.stringify(persisted.attendees)===JSON.stringify([{course_subscription_id:owned.enrollment}]));await take('general-calendar-presence-persists-after-reload',page.locator('#attendance-day-'+event.attendance_day_id).locator('.modal-content'));
        const reader=await actor('reader');const readLesson=await generalLesson(reader.page);await readLesson.getByRole('button',{name:'Gestisci Presenze',exact:true}).click();await expect(reader.page.locator('#attendance-day-'+event.attendance_day_id+' input[type="checkbox"]')).toBeDisabled();
        const denied=(await reader.api(`course/${owned.course}/attendees/${event.attendance_day_id}/update`,{method:'POST',data:{attendees:[]}})).status();
        proof('reader_attendance_write_denied_without_mutation',denied===403&&JSON.stringify((await attendance())[0].attendees)===JSON.stringify(persisted.attendees));await take('general-calendar-reader-attendance-disabled',reader.page.locator('#attendance-day-'+event.attendance_day_id).locator('.modal-content'),reader.page);
        await mark.getByText('Chiudi',{exact:true}).click();await expect(mark).not.toBeVisible();
        await reopened.getByRole('button',{name:'Chiudi',exact:true}).click();await expect(reopened).not.toBeVisible();
        await page.goto(input.origin+'/#/calendar');await expect(page.locator('.ec-event').filter({hasText:lessonTitle})).toBeVisible();
        // Observe the genuine browser event; do not replace window.print or fabricate a print result.
        await page.evaluate(()=>{window.__manualCalendarPrint={count:0,landscape:false};window.addEventListener('beforeprint',()=>{window.__manualCalendarPrint.count++;window.__manualCalendarPrint.landscape=[...document.head.querySelectorAll('style')].some(style=>style.textContent.includes('@page { size: A3 landscape; }'));});});
        await take('general-calendar-current-view-and-print-action');await page.getByRole('button',{name:'Stampa',exact:true}).click();
        const print=await page.evaluate(()=>window.__manualCalendarPrint);proof('actual_calendar_native_print_initiated_with_a3_landscape',print.count>0&&print.landscape);report.native_print={beforeprint_events:print.count,a3_landscape_css_observed:print.landscape,destination_selected:false,physical_output_verified:false};
        const beforeRemovalMembers=await members();
        await page.goto(card());await row().locator('button:is([title="Elimina"],[data-original-title="Elimina"]),button[data-original-title="Elimina"]').click();const popup=page.locator('.swal2-popup');await expect(popup).toContainText("Eliminare l'iscrizione?");await take('course-athlete-removal-confirmation',popup);
        await popup.getByRole('button',{name:'Annulla',exact:true}).click();proof('course_removal_cancel_preserves_enrollment',(await enrollments()).length===1);
        await row().locator('button:is([title="Elimina"],[data-original-title="Elimina"]),button[data-original-title="Elimina"]').click();const removed=responseFor(`course-subscriptions/${owned.enrollment}/delete`,'DELETE');await popup.getByRole('button',{name:'Elimina',exact:true}).click();expect((await removed).status()).toBe(204);owned.enrollment=null;await page.reload();
        expect(await enrollments()).toHaveLength(0);
        const remainingQuota=(await payments()).filter(item=>owned.payments.has(item.payment_id));
        // The serializer retains the stored course choice after the active enrollment is removed.
        const storedPayment=({course,subscription_id,...fields})=>fields;
        expect(remainingQuota.map(storedPayment)).toEqual(addedPayments.map(storedPayment));
        expect(remainingQuota[0].course).toEqual({label:title,value:owned.course});expect(remainingQuota[0].subscription_id).toBeNull();
        expect(records(await members())).toEqual(records(beforeRemovalMembers));
        proof('course_removal_preserves_single_fee_and_association',true);
        expect((await calendar()).events).toHaveLength(1);await take('course-athlete-removed-association-and-calendar-preserved');
        const readerRemoval=(await reader.api(`course-subscriptions/${registrations[0].course_subscription_id}/delete`,{method:'DELETE'})).status();proof('reader_course_enrollment_write_denied',readerRemoval===403);
    }catch(caught){error=caught;throw caught;}finally{
        const failures=[],clean=async(route,method,codes)=>{try{const res=await api(route,{method});if(!codes.includes(res.status()))failures.push(route+': '+res.status());}catch{failures.push(route+': unavailable');}};
        if(owned.enrollment)await clean(`course-subscriptions/${owned.enrollment}/delete`,'DELETE',[200,204,404]);
        if(owned.course)await clean(`course/${owned.course}/calendar/update`,'DELETE',[200,404]);
        for(const uid of owned.payments)await clean(`payment/${uid}/delete`,'DELETE',[200,404]);
        if(owned.course)await clean(`course/${owned.course}/delete`,'POST',[200,404]);
        if(failures.length){report.cleanup_failures=failures;if(!error)throw new Error('Owned course cleanup failed');}
    }
    const ordered=(rows,key)=>Object.values(rows).sort((left,right)=>left[key].localeCompare(right[key]));
    expect(ordered(await courses(),'course_id')).toEqual(ordered(baseline.courses,'course_id'));
    expect(ordered(await payments(),'payment_id')).toEqual(ordered(baseline.payments,'payment_id'));
    expect(ordered(await members(),'subscription_id')).toEqual(ordered(baseline.members,'subscription_id'));
    expect(await read(`course/${input.course_id}/calendar`)).toEqual(baseline.calendar);
    proof('owned_course_cleanup_preserves_original_records',true);
    expect(facts).toEqual(spec.outcome.expected);report[spec.outcome.field]=facts;
    report.external_gaps=[{operation:'native-calendar-paper-or-save-as-pdf',status:'needs_external_verification',reason:'The genuine beforeprint event and application A3 landscape CSS are observed; native destination, pagination, paper and selected save-as-PDF output require external verification.'}];
}});
