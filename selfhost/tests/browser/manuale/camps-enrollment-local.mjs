import {initializeActorSession} from './actor-session.mjs';
// Actual owned camp enrollment, payment and participant form; no provider mocks.
import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import manualConfig from '../playwright.manual.config.mjs';
import {campsPersonasAuthoredWorkflows} from '../../../../docs/manuale/camps-personas-authored-workflows.mjs';
const id='camps-enrollment-local', spec=campsPersonasAuthoredWorkflows[id];
await scenario({id,prefix:spec.prefix.replace(/\/$/,''),sources:spec.sources,actions:async ({page,api,open,actor,input,capture,report})=>{
    expect(input.fixture_version).toBe(8); expect(input.fixture_profile).toBe('member-transfer');
    const read=async(route,client=api)=>{const res=await client(route);expect(res.status()).toBe(200);return res.json();};
    const write=async(route,method,data,status=200)=>{const res=await api(route,{method,data});expect(res.status()).toBe(status);return res.json();};
    const memberRows=async()=>Object.values((await read('subscription/list?pagination[perpage]=100')).data);
    const paymentRows=async()=>Object.values((await read('payment/list?pagination[perpage]=100')).data);
    const financial=row=>Object.fromEntries(['payment_id','amount','paid','expense','subject','creation_date','payment_date','invoice','course','meta','meta_payment_categories','notes','description','archived','deleted']
        .map(key=>[key,row[key]]));
    const sorted=rows=>rows.slice().sort((a,b)=>a.payment_id.localeCompare(b.payment_id));
    const baseline={camps:(await read('camps-and-retreats/list')).data,members:await memberRows(),payments:sorted((await paymentRows()).map(financial))};
    const owned={camp:null,period:null,service:null,enrollments:new Set(),payments:new Set(),transferred:false};
    const title='Ritiro iscrizioni Aurora',periodTitle='Due giornate di allenamento',manualId=input.subscription_ids[1],publicId=input.subscription_ids[0];
    const recipient=input.identities.recipient;expect(recipient).toBeTruthy();expect(recipient.user_id).not.toBe(input.user_id);
    const facts={},proof=(key,value)=>{expect(value,key).toEqual(spec.outcome.expected[key]);facts[key]=value;};
    // Frozen-clock enrollments share created_at, the list's only ordering key.
    const sameEnrollments=(a,b)=>JSON.stringify(a.map(r=>JSON.stringify(r)).sort())===JSON.stringify(b.map(r=>JSON.stringify(r)).sort());
    const enrollments=async()=>owned.camp?(await read(`camps-and-retreats/${owned.camp}/subscriptions/list`)).data:[];
    const take=async(checkpoint,focus,browserPage=page)=>{await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0,{timeout:15000});
        const mask=[browserPage.locator('img.qrcode'),browserPage.locator('a[href*="/camps-and-retreats/forms/"]')];
        await capture(browserPage,spec.checkpoints.findIndex(cp=>cp.id===checkpoint)+1,checkpoint,focus,{mask,redactionReason:'Run-owned camp link and QR identifiers are private evidence.'});};
    const onSave=(route,method,browserPage=page)=>browserPage.waitForResponse(res=>new URL(res.url()).pathname==='/api/'+route&&res.request().method()===method);
    const overview=async()=>{await open('Attività','/#/course/camps-and-retreats/list');await page.getByRole('link',{name:title,exact:true}).click();};
    const row=name=>page.locator('[data-row]').filter({hasText:name});
    const dialog=()=>page.locator('#camps-and-retreats-add-subscription-modal');
    const selection=scope=>scope.locator('#periodsAccordion .checkbox').filter({hasText:periodTitle}).locator('input[type="checkbox"]');
    const serviceSelection=scope=>scope.locator('#periodsAccordion .checkbox').filter({hasText:'Pranzo'}).locator('input[type="checkbox"]');
    const remember=rows=>rows.forEach(item=>{owned.enrollments.add(item.camps_and_retreats_subscription_id);item.periods.forEach(p=>{if(p.payment)owned.payments.add(p.payment.payment_id);});});
    let anonymous,participantContext,error;const participantErrors=[],participantFailures=[];
    try{
        // Creation is fixture preparation; the guide starts from a configured camp.
        const camp=await write('camps-and-retreats/add','POST',{title,description:'Camp dedicato alle iscrizioni locali, senza invii esterni.'},201);owned.camp=camp.camp_and_retreat.camps_and_retreats_id;
        const period=await write('camps-and-retreats/periods/add','POST',{camps_and_retreats:owned.camp,title:periodTitle,
            description:'Allenamento con servizio facoltativo.',fee:'90.00',max_participants:null,start_date:input.reference_date+'T00:00:00Z',end_date:input.reference_date+'T23:59:00Z'},201);
        owned.period=period.period.camps_and_retreats_period_id;
        const service=await write('camps-and-retreats/periods/services/add','POST',{camps_and_retreats_period:owned.period,title:'Pranzo',description:'Servizio aggiuntivo facoltativo.',fee:'15.00',payment_category:input.payment_category_id},201);
        owned.service=service.service.camps_and_retreats_period_service_id;
        report.fixture_preparation={backend:'real',camp:1,period_fee:90,optional_service_fee:15,capacity_limit:null,creation_not_a_guide_step:true,provider_delivery_requested:false};
        await overview();await expect(page.getByRole('heading',{name:title,exact:true})).toBeVisible();await take('camp-existing-period-and-empty-enrollments');
        await page.getByRole('button',{name:'Atleta',exact:true}).click();await expect(dialog()).toBeVisible();
        await dialog().getByPlaceholder("Seleziona l'associato",{exact:true}).fill('Luca');await dialog().getByPlaceholder("Seleziona l'associato",{exact:true}).press('ArrowDown');const lucaOption=dialog().locator('.list-item:visible').filter({hasText:/LUCA VERDI/i});
        await expect(lucaOption).toHaveCount(1);await lucaOption.click();
        await setCheckbox(selection(dialog()),true);await setCheckbox(serviceSelection(dialog()),true);await take('camp-manual-adult-period-and-service-before-submit',dialog().locator('.modal-content'));
        const manualSave=onSave(`camps-and-retreats/${owned.camp}/subscriptions/add`,'POST');
        await dialog().getByRole('button',{name:'Iscrivi',exact:true}).click();expect((await manualSave).status()).toBe(201);await page.reload();
        let list=await enrollments();remember(list);expect(list).toHaveLength(1);const manual=list.find(item=>item.subscription.subscription_id===manualId);expect(manual).toBeTruthy();
        expect(manual.selected_periods[owned.period]).toBe(true);expect(manual.selected_services[owned.period][owned.service]).toBe(true);
        const firstPayment=manual.periods[0].payment;proof('manual_adult_enrollment_and_service_saved',manual.subscription.associate.first_name==='Luca');
        proof('manual_combined_fee_unpaid',Number(firstPayment.amount)===105&&firstPayment.paid===false);
        await expect(row('Luca Verdi')).toBeVisible();await take('camp-manual-enrollment-persists-with-unpaid-fee',row('Luca Verdi'));
        await row('Luca Verdi').locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();await expect(dialog()).toBeVisible();
        await expect(selection(dialog())).toBeChecked();await setCheckbox(serviceSelection(dialog()),false);await take('camp-unpaid-service-removal-before-save',dialog().locator('.modal-content'));
        const edit=onSave(`camps-and-retreats/${manual.camps_and_retreats_subscription_id}/subscriptions/update`,'PATCH');
        await dialog().getByRole('button',{name:'Salva',exact:true}).click();expect((await edit).status()).toBe(200);await page.reload();
        list=await enrollments();remember(list);const edited=list[0],secondPayment=edited.periods[0].payment;
        proof('unpaid_edit_recreates_correct_payment',Number(secondPayment.amount)===90&&secondPayment.paid===false&&secondPayment.payment_id!==firstPayment.payment_id
            &&!(await paymentRows()).some(p=>p.payment_id===firstPayment.payment_id));
        expect(edited.selected_services[owned.period]?.[owned.service]||false).toBe(false);
        await row('Luca Verdi').locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();await expect(selection(dialog())).toBeChecked();await expect(serviceSelection(dialog())).not.toBeChecked();
        await take('camp-edited-unpaid-enrollment-after-reload',dialog().locator('.modal-content'));await setCheckbox(serviceSelection(dialog()),true);
        const editAgain=onSave(`camps-and-retreats/${manual.camps_and_retreats_subscription_id}/subscriptions/update`,'PATCH');
        await dialog().getByRole('button',{name:'Salva',exact:true}).click();expect((await editAgain).status()).toBe(200);
        list=await enrollments();remember(list);const payable=list[0].periods[0].payment;expect(Number(payable.amount)).toBe(105);expect(payable.paid).toBe(false);
        await open('Pagamenti','/#/payment/list');const paymentRow=page.locator(`#action-col-${payable.payment_id}`).locator('xpath=ancestor::*[@data-row][1]');await expect(paymentRow).toHaveCount(1);
        await expect(paymentRow).toContainText('105,00');await take('camp-combined-quota-in-payment-list',paymentRow);
        await paymentRow.locator('button:is([title="Segna come pagato"],[data-original-title="Segna come pagato"])').click();const approval=page.getByRole('dialog',{name:'Incassare il pagamento?',exact:true});
        await setCheckbox(approval.locator('input[id^="generate_invoice_"]'),false);await setCheckbox(approval.locator('input[id^="send_receipt_email_"]'),false);
        await approval.locator('input[name="payment_date"]').fill(input.reference_date);await take('camp-collection-confirmation-no-receipt-or-email',approval);
        const paidResponse=onSave(`payment/${payable.payment_id}/approve`,'POST');await approval.getByRole('button',{name:'Incassa',exact:true}).click();
        const approved=await paidResponse;expect(approved.status()).toBe(200);
        expect(approved.request().postDataJSON()).toMatchObject({generate_invoice:false,send_receipt_email:false});
        const paid=(await paymentRows()).find(p=>p.payment_id===payable.payment_id);
        // Approval creates the local accounting receipt record. The unchecked
        // option controls PDF generation and delivery, not that persisted row.
        expect(paid.invoice).toMatchObject({sport_association:input.association_id,activity_fee:'105.00',membership_fee:'0.00',document_pdf:null});
        expect(paid.invoice.invoice_id).toBeTruthy();
        proof('camp_fee_collected_without_external_delivery',paid.paid===true&&Number(paid.amount)===105&&paid.invoice.document_pdf===null);
        const paymentDrawer = page.locator('.drawer').filter({hasText: 'Dettagli Pagamento'});
        await expect(paymentDrawer).toBeVisible();
        await paymentDrawer.locator('button.close').click();
        await expect(paymentDrawer).toBeHidden();
        await overview();await row('Luca Verdi').locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();await expect(dialog()).toContainText('è già stato pagato');
        await expect(selection(dialog())).toBeDisabled();proof('paid_period_edit_controls_locked',true);await take('camp-paid-period-locked-in-edit',dialog().locator('.modal-content'));
        await dialog().getByRole('button',{name:'Annulla',exact:true}).click();
        const link=page.locator(`a[href="${input.origin}/#/camps-and-retreats/forms/${owned.camp}"]`);await expect(link).toBeVisible();
        anonymous=await page.context().browser().newContext(manualConfig.use);const anonymousPage=await anonymous.newPage();await anonymousPage.goto(await link.getAttribute('href'));
        await expect(anonymousPage.getByRole('heading',{name:'Accedi per iscriverti',exact:true})).toBeVisible();await expect(anonymousPage.getByRole('button',{name:"CREA o ACCEDI con l'account",exact:true})).toBeVisible();
        proof('public_camp_requires_authenticated_account',true);await take('camp-public-link-anonymous-login-gate',undefined,anonymousPage);
        expect((await api(`subscription/${publicId}/transfer`,{method:'POST',data:{recipient:recipient.user_id}})).status()).toBe(201);owned.transferred=true;
        participantContext=await page.context().browser().newContext(manualConfig.use);
        const participantApi=(route,options={})=>participantContext.request.fetch(input.origin+'/api/'+route,{...options,headers:{Authorization:`Bearer ${recipient.token}`}});
        const profileResponse=await participantApi('profile/info');expect(profileResponse.status()).toBe(200);const participantProfile=await profileResponse.json();
        expect(participantProfile.info.role).toBe('athlete');expect(participantProfile.user_data.user_id).toBe(recipient.user_id);
        await participantContext.addInitScript(initializeActorSession, {identity: recipient, input});
        const participant={page:await participantContext.newPage(),api:participantApi};
        participant.page.on('pageerror',error=>participantErrors.push(error.message));participant.page.on('response',res=>{if(res.status()>=400&&res.url().startsWith(input.origin))participantFailures.push({path:new URL(res.url()).pathname,status:res.status()});});
        await participant.page.goto(await link.getAttribute('href'));
        await expect(participant.page.getByRole('heading',{name:'Iscriviti al Camp',exact:true})).toBeVisible();
        const publicForm=participant.page.locator('#camps_and_retreats_subscriptions_form');
        await publicForm.locator('input[name="subscription"]').locator('..').locator('input[type="text"]').click();const giuliaOption=publicForm.locator('.list-item:visible').filter({hasText:/GIULIA BIANCHI/i});
        await expect(giuliaOption).toHaveCount(1);await giuliaOption.click();
        await setCheckbox(selection(publicForm),true);await setCheckbox(serviceSelection(publicForm),true);await take('camp-public-authenticated-person-period-service',publicForm,participant.page);
        const beforeForeignPerson=await enrollments();
        const foreignPerson=await participant.api(`camps-and-retreats/${owned.camp}/subscriptions/add`,{method:'POST',data:{subscription:manualId,periods:[{camps_and_retreats_period:owned.period,services:[]}]}});
        proof('participant_cannot_enroll_another_unrelated_person',foreignPerson.status()===403&&sameEnrollments(await enrollments(),beforeForeignPerson));
        const publicSave=onSave(`camps-and-retreats/${owned.camp}/subscriptions/add`,'POST',participant.page);
        await publicForm.getByRole('button',{name:'Iscrivi',exact:true}).click();const publicResponse=await publicSave;expect(publicResponse.status()).toBe(201);
        await expect(participant.page).toHaveURL(/#\/payment\/list$/);await participant.page.reload();
        list=await enrollments();remember(list);expect(list).toHaveLength(2);const publicEnrollment=list.find(item=>item.subscription.subscription_id===publicId);expect(publicEnrollment).toBeTruthy();
        proof('participant_real_public_form_saved',publicEnrollment.selected_services[owned.period][owned.service]===true&&Number(publicEnrollment.periods[0].payment.amount)===105);
        proof('participant_public_payment_unpaid',publicEnrollment.periods[0].payment.paid===false);
        await expect(participant.page.locator('.card.card-custom').filter({hasText:'Giulia Bianchi'}).filter({hasText:'105,00'})).toContainText('105,00');await take('camp-public-unpaid-quota-after-reload',undefined,participant.page);
        const reader=await actor('reader');await reader.open('Attività','/#/course/camps-and-retreats/list');await reader.page.getByRole('link',{name:title,exact:true}).click();
        await expect(reader.page.getByRole('button',{name:'Atleta',exact:true})).toHaveCount(0);
        await expect(reader.page.locator('[data-row]').filter({hasText:'Luca Verdi'}).locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled();
        const beforeDenial=await enrollments(),denials=[];
        for(const[route,method,data]of[[`camps-and-retreats/${owned.camp}/subscriptions/add`,'POST',{subscription:manualId,periods:[]}],
            [`camps-and-retreats/${manual.camps_and_retreats_subscription_id}/subscriptions/update`,'PATCH',{periods:[]}],
            [`camps-and-retreats/${manual.camps_and_retreats_subscription_id}/subscriptions/delete`,'DELETE',{}]])denials.push((await reader.api(route,{method,data})).status());
        proof('reader_camp_enrollment_writes_denied',denials.every(code=>code===403));expect(await enrollments()).toEqual(beforeDenial);await take('camp-reader-enrollments-write-controls-disabled',undefined,reader.page);
        await overview();const deleteRow=async(name,record)=>{await row(name).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();const popup=page.locator('.swal2-popup');
            await expect(popup).toContainText('Eliminare il camp?');const response=onSave(`camps-and-retreats/${record.camps_and_retreats_subscription_id}/subscriptions/delete`,'DELETE');
            await popup.getByRole('button',{name:'Elimina',exact:true}).click();expect((await response).status()).toBe(200);owned.enrollments.delete(record.camps_and_retreats_subscription_id);};
        await row('Giulia Bianchi').locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();const popup=page.locator('.swal2-popup');await take('camp-enrollment-removal-confirmation',popup);
        const beforeCancel=await enrollments();await popup.getByRole('button',{name:'Annulla',exact:true}).click();proof('removal_cancel_preserves_enrollments',sameEnrollments(await enrollments(),beforeCancel));
        await deleteRow('Giulia Bianchi',publicEnrollment);await page.reload();expect(await enrollments()).toHaveLength(1);
        proof('unpaid_enrollment_removal_removes_its_payment',!(await paymentRows()).some(p=>p.payment_id===publicEnrollment.periods[0].payment.payment_id));
        await take('camp-unpaid-enrollment-removed-after-reload');
        await deleteRow('Luca Verdi',manual);await page.reload();expect(await enrollments()).toEqual([]);
        proof('paid_enrollment_removal_preserves_collected_payment',(await paymentRows()).some(p=>p.payment_id===paid.payment_id&&p.paid===true&&Number(p.amount)===105));
        expect((await read(`camps-and-retreats/${owned.camp}/info`)).data.periods).toHaveLength(1);await take('camp-enrollments-removed-camp-and-paid-quota-preserved');
        const activePayments=await paymentRows();
        const expectedMembers=baseline.members.filter(row=>row.subscription_id!==publicId).map(member=>{
            const associated=activePayments.filter(payment=>payment.associate?.associate_id===member.associate.associate_id);
            return {...member,all_payments_paid:associated.every(payment=>payment.paid),payments_info:{
                total:associated.reduce((sum,payment)=>sum+Number(payment.amount),0),
                to_be_paid:associated.filter(payment=>!payment.paid).reduce((sum,payment)=>sum+Number(payment.amount),0)}};
        });
        const sortMembers=rows=>[...rows].sort((a,b)=>a.subscription_id.localeCompare(b.subscription_id));
        proof('other_registrations_and_baseline_payments_preserved',JSON.stringify(sortMembers((await memberRows()).filter(row=>row.subscription_id!==publicId)))===JSON.stringify(sortMembers(expectedMembers))
            &&JSON.stringify(sorted((await paymentRows()).filter(row=>baseline.payments.some(old=>old.payment_id===row.payment_id)).map(financial)))===JSON.stringify(baseline.payments));
    }catch(caught){error=caught;throw caught;}finally{
        if(anonymous)await anonymous.close();if(participantContext)await participantContext.close();report.participant_browser_errors=participantErrors;report.participant_failed_responses=participantFailures;const failures=[];
        const clean=async(route,method,statuses)=>{try{const res=await api(route,{method});if(!statuses.includes(res.status()))failures.push(route+': '+res.status());}catch{failures.push(route+': unavailable');}};
        if(owned.camp){try{remember(await enrollments());}catch{failures.push('Owned enrollment lookup');}}
        for(const uid of owned.enrollments)await clean(`camps-and-retreats/${uid}/subscriptions/delete`,'DELETE',[200,404]);
        for(const uid of owned.payments)await clean(`payment/${uid}/delete`,'DELETE',[200,404]);
        if(owned.camp)await clean(`camps-and-retreats/${owned.camp}/delete`,'DELETE',[200,404]);
        if(owned.transferred)report.private_cleanup={operation:'member-transfer-owned-fixture-reset',status:'awaiting_runner_fixture_reset',fixture_profile:'member-transfer',
            subscription:publicId,persona:baseline.members.find(row=>row.subscription_id===publicId).associate.associate_id,recipient:recipient.user_id};
        if(failures.length){report.cleanup_failures=failures;if(!error)throw new Error('Owned camp cleanup failed');}
    }
    proof('owned_camp_and_its_generated_payments_removed',JSON.stringify((await read('camps-and-retreats/list')).data)===JSON.stringify(baseline.camps)
        &&JSON.stringify(sorted((await paymentRows()).map(financial)))===JSON.stringify(baseline.payments));
    proof('participant_transfer_reset_delegated',report.private_cleanup?.status==='awaiting_runner_fixture_reset');
    expect(participantErrors).toEqual([]);expect(participantFailures).toEqual([]);expect(facts).toEqual(spec.outcome.expected);report[spec.outcome.field]=facts;
    report.external_gaps=[{operation:'camp-account-registration-login-email-and-provider-payment',status:'needs_external_verification',reason:'An existing real fixture recipient session is used; account creation/login delivery, external checkout, receipt/email delivery and paid enrollment with an issued receipt are separate.'}];
    report.checks=['actual adult manual enrollment with optional service creates unpaid105 quota','unpaid edit recreates90 quota; actual collection105 locks period editing with local receipt record and without PDF/email delivery',
        'anonymous gate and authenticated participant form create a persisted unpaid quota','reader add/update/delete denied; cancellation preserved state',
        'unpaid removal deletes its quota, paid removal preserves105 collection; owned camp/payments cleaned, recipient ownership reset delegated'];
}});
