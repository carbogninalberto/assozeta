import {setCheckbox} from './controls.mjs';
import {scenario, expect} from './scenario.mjs';
import manualConfig from '../playwright.manual.config.mjs';
import {courseSettingsAuthoredWorkflows} from '../../../../docs/manuale/course-settings-authored-workflows.mjs';
// Seeded and frozen-clock rows share timestamps (or the list is unordered), so compare
// record sets: identical rows and contents, independent of physical row order.
const records = rows => (Array.isArray(rows) ? rows.map(row => JSON.stringify(row))
    : Object.entries(rows).map(entry => JSON.stringify(entry))).sort();
const id='course-types-sharing',spec=courseSettingsAuthoredWorkflows[id];
await scenario({id,prefix:spec.prefix.replace(/\/$/,''),sources:spec.sources,
    actions:async({page,api,open,actor,context,input,capture,report})=>{
        expect(input.fixture_version).toBe(8);
        const read=async route=>{const response=await api(route);expect(response.status()).toBe(200);return response.json();};
        const list=async()=> (await read('course/list?all=1')).data;
        const payments=async()=>Object.values((await read('payment/list?pagination[perpage]=100')).data);
        const baselineCourses=await list(),baselinePayments=await payments(),owned=new Set();
        const category=(await read('payment/category/list')).data.find(row=>row.name==='Quote e attività associative' && row.type===1 && row.expense===false);
        expect(category).toBeTruthy();
        const take=async(checkpoint,focus,browserPage=page,mask=[])=>{
            await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0,{timeout:15000});
            await capture(browserPage,spec.checkpoints.findIndex(point=>point.id===checkpoint)+1,checkpoint,focus,
                {mask,redactionReason:mask.length?'Calendar sharing link and embedded source are excluded from the public master.':''});};
        const drawer=()=>page.locator('.drawer').filter({has:page.getByPlaceholder('Titolo Corso')});
        const begin=async(title,type)=>{await open('Attività','/#/course/list');await page.getByRole('button',{name:'Corso o Abbonamento',exact:true}).click();
            await expect(drawer()).toBeVisible();await drawer().getByPlaceholder('Titolo Corso').fill(title);
            await drawer().locator('[contenteditable="true"]').fill('Attività dimostrativa per confrontare configurazione e quote assegnate.');
            await setCheckbox(drawer().locator(`input[name="course_type"][value="${type}"]`),true);};
        const finish=async(title)=>{const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/course/add'&&r.request().method()==='POST');
            await drawer().getByRole('button',{name:'Salva',exact:true}).click();const response=await pending;
            const record=(await list()).find(c=>c.title===title);if(record)owned.add(record.course_id);
            expect(response.status()).toBe(200);expect(record).toBeTruthy();await expect(drawer()).not.toBeVisible();
            await page.locator('[data-row]').filter({hasText:title}).getByText(title,{exact:true}).click();await page.reload();
            await expect(page.locator('.card-title').getByText(title,{exact:true})).toBeVisible();return record.course_id;};
        const course=async uid=>(await read(`course/${uid}/overview`)).data.course;
        const members=async uid=>(await read(`course-subscriptions/list?course_id=${uid}`)).data;
        const pick=async(scope,label,value)=>{await scope.getByRole('textbox',{name:label,exact:true}).click();await scope.locator('.list-item').getByText(value,{exact:true}).click();};
        let anonymous,error;
        try{
            await begin('Laboratorio con quote alternative',2);
            for(const [index,title,amount] of [[0,'Ordinaria','90,00'],[1,'Studenti','65,00'],[2,'Da rimuovere','10,00']]){
                await drawer().getByRole('button',{name:'Aggiungi Quota',exact:true}).click();await drawer().locator(`#quoteTitle_${index}`).fill(title);
                await drawer().locator(`#quoteTitle_${index}`).press('Tab');
                const quote=drawer().locator('.quote-item').nth(index);
                await pick(quote,'Causale','Quote e attività associative (Entrata Istituzionale)');
                await expect.poll(()=>page.evaluate(i=>JSON.parse(localStorage.getItem('newCourse')).multiple_quotes[i]?.payment_category?.value,index)).toBe(category.payment_category_id);
                await drawer().locator(`input[name="quoteAmount_${index}"]`).fill(amount);await drawer().locator(`input[name="quoteAmount_${index}"]`).press('Tab');
                await expect.poll(()=>page.evaluate(i=>JSON.parse(localStorage.getItem('newCourse')).multiple_quotes[i]?.amount,index)).toBe(amount);}
            await drawer().locator('.quote-item').nth(2).locator('button.btn-danger').click();await expect(drawer().locator('.quote-item')).toHaveCount(2);
            await expect(drawer().locator('input[name="quoteAmount_0"]')).toHaveValue('90,00');
            await expect(drawer().locator('input[name="quoteAmount_1"]')).toHaveValue('65,00');
            await take('multiple-quotes-before-save',drawer().locator('#bkn_form'));
            const multipleId=await finish('Laboratorio con quote alternative');
            const multiple=await course(multipleId);expect(multiple.course_type).toBe(2);expect(multiple.multiple_quotes.map(q=>q.title)).toEqual(['Ordinaria','Studenti']);
            expect(multiple.multiple_quotes.map(q=>Number(String(q.amount).replace(',','.')))).toEqual([90,65]);await take('multiple-quotes-after-reload');
            await page.getByRole('button',{name:'Aggiungi tesserato',exact:true}).click();const modal=page.locator('#subscription-modal');await expect(modal).toBeVisible();
            const athletePicker=modal.locator('label[for="selectedAthletes"]').locator('xpath=ancestor::div[contains(@class,"form-group")][1]');
            await athletePicker.locator('input:not([type="hidden"])').fill('GIULIA BIANCHI');await page.locator('.list-item').filter({hasText:'GIULIA BIANCHI'}).click();
            await pick(modal,'Quota','Studenti - 65,00€');await take('multiple-quote-assignment-before-save',modal.locator('.modal-content'));
            const assigned=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/course-subscriptions/add'&&r.request().method()==='POST');
            await modal.getByRole('button',{name:'Aggiungi',exact:true}).click();expect((await assigned).status()).toBe(201);await page.reload();
            let rows=await members(multipleId);expect(rows).toHaveLength(1);expect(rows[0].multiple_quote.title).toBe('Studenti');
            const assignedPayment=(await payments()).find(p=>p.meta?.course_id===multipleId && p.meta?.multiple_quote?.title==='Studenti');const paymentId=assignedPayment?.payment_id;
            expect(assignedPayment).toBeTruthy();expect(Number(assignedPayment.amount)).toBe(65);expect(assignedPayment.paid).toBe(false);
            await take('multiple-quote-assignment-after-reload',page.locator('[data-row]').filter({hasText:'GIULIA BIANCHI'}));
            await page.locator('[data-row]').filter({hasText:'GIULIA BIANCHI'}).locator('button:is([title="Modifica"],[data-original-title="Modifica"]), button[data-original-title="Modifica"]').click();await expect(modal).toBeVisible();
            await expect(modal).toContainText('La modifica della quota non apporta modifiche ai pagamenti.');await pick(modal,'Quota','Ordinaria - 90,00€');
            await take('multiple-quote-change-warning',modal.locator('.modal-content'));
            const edited=page.waitForResponse(r=>r.url().includes('/api/course-subscriptions/')&&r.request().method()==='PATCH');
            await modal.getByRole('button',{name:'Modifica',exact:true}).click();expect((await edited).status()).toBe(200);await page.reload();
            rows=await members(multipleId);expect(rows[0].multiple_quote.title).toBe('Ordinaria');
            expect((await payments()).find(p=>p.payment_id===paymentId)).toEqual(assignedPayment);await take('multiple-quote-change-payment-preserved');
            // Prepare a real calendar event solely to demonstrate sharing an existing calendar.
            const event={event_id:'33333333-4444-5555-8888-999999999999',title:'Lezione dimostrativa condivisa',start:input.reference_date+'T17:00:00',end:input.reference_date+'T18:00:00',allDay:false,extendedProps:{description:'Calendario dimostrativo del laboratorio'}};
            expect((await api(`course/${multipleId}/calendar/update`,{method:'POST',data:{events:[event],status:1}})).status()).toBe(200);
            await page.reload();await page.locator('.btn-group').getByText('Calendario', {exact: true}).click();await page.locator('[data-target="#share-link"]').click();const share=page.locator('#share-link');await expect(share).toBeVisible();
            const displayed=await share.locator('input[type="text"]').inputValue(),link=new URL(displayed);
            expect(link.origin).toBe(input.origin);expect(link.hash).toBe('#/shared-calendar/'+multipleId);
            const embed=await share.locator('textarea.text-left').inputValue();expect(embed).toContain(displayed);expect(embed).toContain('<iframe');
            await take('calendar-sharing-link-and-embed',share.locator('.modal-content'),page,[share.locator('input,textarea')]);
            await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:input.origin});await share.locator('[data-clipboard="true"]').click();
            expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe(displayed);
            anonymous=await context.browser().newContext(manualConfig.use);await anonymous.addInitScript(referenceDate=>{const NativeDate=Date;const fixed=new NativeDate(referenceDate+'T12:00:00Z').getTime();window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[fixed]));}static now(){return fixed;}};},input.reference_date);const publicPage=await anonymous.newPage(),publicErrors=[],publicFailures=[];
            publicPage.on('pageerror',cause=>publicErrors.push(cause.message));publicPage.on('response',r=>{if(r.url().startsWith(input.origin)&&r.status()>=400)publicFailures.push({status:r.status(),path:new URL(r.url()).pathname});});
            await publicPage.goto(displayed);await expect(publicPage.locator('#course_attendance_calendar')).toBeVisible();
            await expect(publicPage.locator('#course_attendance_calendar')).toContainText(event.title);
            const publicResponse=await anonymous.request.get(input.origin+`/api/course/${multipleId}/calendar`);expect(publicResponse.status()).toBe(200);
            const publicData=(await publicResponse.json()).data;expect(publicData.course.title).toBe('Laboratorio con quote alternative');expect(publicData.events.some(e=>e.event_id===event.event_id)).toBe(true);
            expect(publicData).not.toHaveProperty('subscriptions');expect(publicData).not.toHaveProperty('payments');
            await take('calendar-shared-without-session',publicPage.locator('#course_attendance_calendar'),publicPage);expect(publicErrors).toEqual([]);expect(publicFailures).toEqual([]);
            expect([401,403]).toContain((await anonymous.request.post(input.origin+`/api/course/${multipleId}/calendar/update`,{data:{events:[],status:1}})).status());
            await share.getByRole('button',{name:'Chiudi',exact:true}).first().click();
            await begin('Abbonamento laboratorio mensile',3);await drawer().locator('input[name="fee"]').fill('40,00');
            await setCheckbox(drawer().locator('input[name="auto_renewal"]'),false);await setCheckbox(drawer().locator('input[name="billed_duration_is_sport_season"]'),false);
            await setCheckbox(drawer().locator('input[name="billed_from_subscription_date"]'),true);
            await pick(drawer(),'Durata abbonamento (mesi)','Mensile');await take('membership-course-defaults-before-save',drawer().locator('#bkn_form'));
            const membershipId=await finish('Abbonamento laboratorio mensile');const membershipCourse=await course(membershipId);
            expect(membershipCourse).toMatchObject({course_type:3,auto_renewal:false,billed_duration_is_sport_season:false,billed_from_subscription_date:true,billed_frequency:1});
            expect(Number(membershipCourse.fee)).toBe(40);await take('membership-course-after-reload');
            await page.getByRole('button',{name:'Aggiungi abbonamento',exact:true}).click();await expect(modal).toBeVisible();
            await modal.getByRole('textbox',{name:'Tesserati',exact:true}).fill('GIULIA BIANCHI');await modal.locator('.list-item').getByText('GIULIA BIANCHI (Anno corrente)',{exact:true}).click();
            await modal.locator('#membership-fee').fill('35,00');await modal.locator('#membership-frequency').selectOption('2');
            await modal.locator('input[name="billed_from"]').fill(input.reference_date);await setCheckbox(modal.getByLabel('Abbonamento attivo',{exact:true}),true);
            await setCheckbox(modal.getByLabel('Rinnovo automatico',{exact:true}),false);const until=await modal.locator('#membership-until').inputValue();expect(until>input.reference_date).toBe(true);
            await take('individual-membership-period-before-save',modal.locator('.modal-content'));
            const subscribed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/course-subscriptions/add'&&r.request().method()==='POST');
            await modal.getByRole('button',{name:'Crea',exact:true}).click();expect((await subscribed).status()).toBe(201);await page.reload();
            const memberships=await members(membershipId);expect(memberships).toHaveLength(1);const member=memberships[0];
            expect(member).toMatchObject({billed_from:input.reference_date+'T00:00:00Z',billed_until:until+'T00:00:00Z',billed_frequency:2,auto_renewal:false,membership_active:true});expect(Number(member.membership_fee)).toBe(35);
            expect(member.membership_payments).toHaveLength(1);expect(Number(member.membership_payments[0].amount)).toBe(35);expect(member.membership_payments[0].paid).toBe(false);
            await take('individual-membership-after-reload',page.locator('[data-row]').filter({hasText:'GIULIA BIANCHI'}));
            const reader=await actor('reader');for(const uid of owned){expect((await reader.api(`course/${uid}/update`,{method:'PATCH',data:{course:{title:'Denied'}}})).status()).toBe(403);
                expect((await reader.api('course-subscriptions/add',{method:'POST',data:[{course:uid,subscription_id:input.subscription_ids[1]}]})).status()).toBe(403);}
            expect((await reader.api(`course/${multipleId}/calendar/update`,{method:'POST',data:{events:[],status:1}})).status()).toBe(403);
            expect(await members(membershipId)).toEqual(memberships);expect((await read(`course/${multipleId}/calendar`)).data.events.some(e=>e.event_id===event.event_id)).toBe(true);
        }catch(cause){error=cause;throw cause;}finally{
            if(anonymous)await anonymous.close();const failures=[];for(const uid of owned){try{expect((await api(`course/${uid}/delete`,{method:'POST'})).status()).toBe(200);}catch{failures.push(uid);}}
            for(const payment of await payments()) {
                if(!owned.has(payment.course?.value || payment.meta?.course_id))continue;
                try {
                    expect(payment.paid).toBe(false);expect(payment.invoice).toBeNull();
                    expect(baselinePayments.some(row=>row.payment_id===payment.payment_id)).toBe(false);
                    expect((await api(`payment/${payment.payment_id}/delete`,{method:'DELETE'})).status()).toBe(200);
                } catch {failures.push('owned-payment');}
            }
            if(failures.length){report.cleanup_failures=failures.map(()=> 'Owned course/payment deletion failed');if(!error)throw new Error('Owned course cleanup failed');}
        }
        expect(records(await list())).toEqual(records(baselineCourses));expect(records(await payments())).toEqual(records(baselinePayments));
        report[spec.outcome.field]={multiple_quotes_saved_reopened:true,selected_quote_creates_unpaid_payment:true,quote_change_preserves_recorded_payment:true,
            membership_defaults_saved_reopened:true,individual_membership_period_saved:true,individual_fee_creates_unpaid_payment:true,
            calendar_link_matches_actual_course:true,clipboard_matches_displayed_link:true,embedded_source_matches_link:true,anonymous_calendar_read:true,
            anonymous_calendar_write_denied:true,reader_write_denials:5,baseline_records_preserved:true,owned_courses_and_unpaid_payments_removed:true};
        expect(report[spec.outcome.field]).toEqual(spec.outcome.expected);
        report.checks=['actual course UI creates alternatives and membership defaults; real enrollment periods and unpaid quotes persist',
            'changing selected alternative does not rewrite the existing payment','anonymous existing-calendar view and denied writes; no WhatsApp/email delivery or Google synchronization requested'];
    }});
