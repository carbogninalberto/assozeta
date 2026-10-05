// All requests reach the disposable real backend. No signup or signature response is substituted.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {scenario,expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import manualConfig from '../playwright.manual.config.mjs';
import {reloadOrganizationProfile} from './organization-access-sources.mjs';
import {settingsPrintAuthoredWorkflows} from '../../../../docs/manuale/settings-print-authored-workflows.mjs';
const id='settings-signup-checkout',spec=settingsPrintAuthoredWorkflows[id];
await scenario({id,prefix:spec.prefix.replace(/\/$/,''),sources:spec.sources,
    actions:async({page,api,open,context,actor,input,capture,report})=>{
        expect(input.fixture_version).toBe(8);expect(input.fixture_profile||'baseline').toBe('baseline');
        expect(input.integration_modes.email.external_delivery).toBe(false);
        const read=async route=>{const response=await api(route);expect(response.status()).toBe(200);return response.json();};
        const profile=async()=>(await read('profile/info')).user_data,original=await profile();
        expect(original.sport_association.denomination).toBe('Associazione Sportiva Aurora');expect((await read('profile/settings')).settings.online_payments).toBe(false);
        expect(original.sport_association.additional_fields).toEqual([]);expect(original.sport_association.additional_sections).toEqual([]);
        // List endpoints do not guarantee row order. Preserve every row/field while
        // comparing business records by their stable identities.
        const ordered=(data,key)=>Object.values(data).sort((left,right)=>left[key].localeCompare(right[key]));
        const records=async()=>({subscriptions:ordered((await read('subscription/list?pagination[perpage]=100')).data,'subscription_id'),payments:ordered((await read('payment/list?pagination[perpage]=100')).data,'payment_id')});
        const initialRecords=await records(),baseline=structuredClone(initialRecords);
        const originalPayload={user_data:{first_name:original.first_name,last_name:original.last_name,username:original.username,email:original.email,avatar_image:null,sport_association:original.sport_association}};
        const fields=[['Telefono atleta obbligatorio','mandatory_phone'],['Email atleta obbligatoria','mandatory_email'],['Email tutore obbligatoria','mandatory_tutor_email'],['Telefono tutore obbligatorio','mandatory_tutor_phone'],['Firma obbligatoria alla compilazione','mandatory_signature']];
        const take=async(checkpoint,focus,browserPage=page,mask=[])=>{
            const index=spec.checkpoints.findIndex(p=>p.id===checkpoint);expect(index).toBeGreaterThanOrEqual(0);
            await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0,{timeout:15000});
            await capture(browserPage,index+1,checkpoint,focus,{mask,redactionReason:mask.length?'Registration capability link and QR are private runtime evidence.':''});};
        const openAccount=async()=>{await open('Impostazioni','/#/profile');await page.getByText('Informazioni Account',{exact:true}).click();await expect(page.locator('#bkn_form_account_update')).toBeVisible();};
        const block=(scope,label)=>scope.getByText(label,{exact:true}).locator('xpath=ancestor::div[contains(@class,"form-group")][1]');
        const editor=()=>page.getByRole('heading',{name:'Checkout',exact:true}).locator('xpath=ancestor::div[contains(@class,"row")][1]/following-sibling::div[1]').locator('[contenteditable="true"]');
        const save=async()=>{const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/profile/update'&&r.request().method()==='PATCH');
            await page.locator('#bkn_form_account_update_submit').click();expect((await pending).status()).toBe(200);await reloadOrganizationProfile({page,api,context,expect});};
        const tab=async name=>page.locator('.nav-link').filter({hasText:new RegExp('^\\s*'+name+'\\s*$')}).click();
        const sectionCard=()=>page.locator('div.my-6').filter({has:page.getByPlaceholder('Nome sezione...')});
        const fieldCard=()=>page.locator('.form-element-preview .form-group').first();
        const property=label=>fieldCard().locator('.form-group').filter({hasText:new RegExp('^\\s*'+label+'\\s*$')}).locator('input');
        const openField=async()=>{await tab('Campi Aggiuntivi');await expect(page.locator('.form-element-preview')).toHaveCount(1);await fieldCard().click();await expect(property('Etichetta')).toBeVisible();};
        const saveTemplate=async()=>{const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/profile/update/subscription/template'&&r.request().method()==='PATCH');
            await page.locator('#bkn_form_account_update_submit').click();expect((await pending).status()).toBe(200);
            await expect(page.getByText("Modulo d'iscrizione aggiornato correttamente",{exact:true})).toBeVisible();};
        const person={first_name:'Elisa',last_name:'Ferri',email:'elisa.ferri@example.test',phone:'3331234567'};
        const owned={fixture_only:true,association_id:input.association_id,subscription_id:null,associate_id:null,payment_id:null,document_ids:[],cleanup_finished:false};
        const privatePath=path.join(process.env.ASSOZETA_MANUAL_RUN,'private',id+'-owned.json');
        const saveOwned=()=>{fs.mkdirSync(path.dirname(privatePath),{recursive:true,mode:0o700});fs.writeFileSync(privatePath,JSON.stringify(owned,null,2)+'\n',{mode:0o600});};
        saveOwned();
        const discoverOwned=async()=>{
            const candidates=Object.values((await records()).subscriptions).filter(row=>!input.subscription_ids.includes(row.subscription_id)
                &&row.sport_association===input.association_id&&row.associate.first_name===person.first_name&&row.associate.last_name===person.last_name
                &&row.associate.email===person.email&&row.associate.tax_code===fiscalCode);
            expect(candidates.length).toBeLessThanOrEqual(1);
            if(candidates[0]){owned.subscription_id=candidates[0].subscription_id;owned.associate_id=candidates[0].associate.associate_id;
                owned.payment_id=candidates[0].payment?.payment_id;saveOwned();}
            return candidates[0];};
        let anonymous,error,fiscalCode,createdRequest,submittedSignature,templateSaved=false,creationRequested=false;
        const browserErrors=[],failures=[],writes=[];
        const observeErrors=browserPage=>{browserPage.on('pageerror',cause=>browserErrors.push(cause.message));browserPage.on('response',r=>{if(r.status()>=400&&r.url().startsWith(input.origin))failures.push({path:new URL(r.url()).pathname,status:r.status()});});};
        context.on('page',observeErrors);
        const rendererPattern=input.origin+'/api/document/subscription/*/view/';
        const authenticateRenderer=route=>route.fallback({headers:{...route.request().headers(),Authorization:'Bearer '+input.token}});
        await page.route(rendererPattern,authenticateRenderer);
        try{
            await openAccount();for(const[label]of fields)await setCheckbox(block(page,label).locator('input[type="checkbox"]'),true);
            await block(page,fields[0][0]).scrollIntoViewIfNeeded();await take('signup-requirements-before-save');await save();
            let saved=await profile();for(const[label,key]of fields){expect(saved.sport_association.configuration[key]).toBe(true);await expect(block(page,label).locator('input[type="checkbox"]')).toBeChecked();}
            // Account saves intentionally canonicalize the existing username.
            // Compare every business field exactly, including the precise
            // expected denormalized owner username from that real saved profile.
            expect(saved.username).toBe(original.username.toUpperCase());
            for(const member of Object.values(baseline.subscriptions)){
                if(member.user?.user_id===input.user_id){
                    expect(member.user.username).toBe(original.username);
                    member.user.username=saved.username;
                }
            }
            expect(await records()).toEqual(baseline);
            report.profile_account_effects={username_case_canonicalized:true,all_other_baseline_business_fields_unchanged:true};
            await block(page,fields[0][0]).scrollIntoViewIfNeeded();await take('signup-requirements-after-reload');
            await open('Organizzazione','/#/members/subscription/template');await expect(page.getByRole('heading',{name:'Modulo Iscrizione',exact:true})).toBeVisible();
            await setCheckbox(page.locator('#associate-checkbox'),false);await setCheckbox(page.locator('#membership-checkbox'),false);await expect(page.locator('#associate-membership-checkbox')).toBeChecked();
            await take('signup-module-type-before-save');
            await tab('Quote');await page.locator('input[name="subscription_fee"]').fill('30,00');await page.locator('input[name="subscription_fee"]').press('Tab');
            await expect(page.locator('input[name="membership_fee"]')).toHaveValue('0,00');await take('signup-simple-fee-before-save');
            await tab('Sezioni Modulo');await page.getByRole('button',{name:'Aggiungi',exact:true}).click();
            await sectionCard().getByPlaceholder('Nome sezione...').fill('Materiale per allenamento');await sectionCard().locator('[contenteditable="true"]').fill('Porta borraccia e abbigliamento comodo.');
            await setCheckbox(sectionCard().locator('input[type="checkbox"]').nth(0),false);await setCheckbox(sectionCard().locator('input[type="checkbox"]').nth(1),true);await setCheckbox(sectionCard().locator('input[type="checkbox"]').nth(2),false);
            await take('signup-added-clause-before-save',sectionCard());
            await tab('Campi Aggiuntivi');await page.getByRole('button',{name:'Testo',exact:true}).click();await openField();
            await property('Etichetta').fill('Taglia maglietta');await property('Placeholder').fill('Esempio: M');await property('Etichetta aiuto').fill('Indica la taglia desiderata.');await setCheckbox(property('Obbligatorio'),false);
            await take('signup-optional-field-before-save',fieldCard());templateSaved=true;await saveTemplate();await page.reload();
            saved=await profile();expect(saved.sport_association.enabled_for).toEqual(['associate-membership']);expect(saved.sport_association.subscription_fee).toBe('30.00');expect(saved.sport_association.membership_fee).toBe('0.00');
            expect(saved.sport_association.additional_sections).toHaveLength(1);expect(saved.sport_association.additional_sections[0].name).toBe('MATERIALE PER ALLENAMENTO');
            expect(saved.sport_association.additional_sections[0].text.replace(/<[^>]*>/g,'').trim()).toBe('Porta borraccia e abbigliamento comodo.');
            // Unchecked optional properties remain absent in the saved JSON;
            // the module renderer interprets them as false.
            const clause = saved.sport_association.additional_sections[0];
            expect([!!clause.show_to_members, !!clause.show_to_both, !!clause.show_to_athletes]).toEqual([false, true, false]);
            expect(saved.sport_association.additional_fields).toHaveLength(1);expect(saved.sport_association.additional_fields[0].props).toMatchObject({label:'Taglia maglietta',placeholder:'Esempio: M',helperLabel:'Indica la taglia desiderata.',required:false});
            await take('signup-module-type-after-reload');await tab('Quote');await expect(page.locator('input[name="subscription_fee"]')).toHaveValue('30,00');await take('signup-simple-fee-after-reload');
            await tab('Sezioni Modulo');await expect(sectionCard().getByPlaceholder('Nome sezione...')).toHaveValue('MATERIALE PER ALLENAMENTO');await take('signup-added-clause-after-reload',sectionCard());
            await openField();await expect(property('Obbligatorio')).not.toBeChecked();await take('signup-optional-field-after-reload',fieldCard());
            // Exercise the field's obligatory variant too, before submitting a real signup.
            await setCheckbox(property('Obbligatorio'),true);await take('signup-required-custom-field-before-save',fieldCard());await saveTemplate();await page.reload();
            await openField();await expect(property('Obbligatorio')).toBeChecked();await take('signup-required-custom-field-after-reload',fieldCard());
            const module=(await profile()).sport_association,customField=module.additional_fields[0];expect(customField.props.required).toBe(true);
            expect(await records()).toEqual(baseline);
            await open('Organizzazione','/#/members/list');
            await page.getByRole('button',{name:'Libro Soci',exact:true}).click();
            await expect(page).toHaveURL(/#\/members\/members-book$/);
            await page.getByText('Condividi link iscrizioni',{exact:true}).click();
            const share=page.locator('#share-link');await expect(share).toBeVisible();const link=await share.locator('input[type="text"]').inputValue(),url=new URL(link);
            expect(url.origin).toBe(input.origin);expect(url.hash).toBe('#/subscribe/'+original.username.toLowerCase());
            await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:input.origin});await share.locator('[data-clipboard="true"]').click();
            expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe(link);
            await take('signup-current-module-share-dialog',share.locator('.modal-content'),page,[share.locator('input'),share.locator('#qr-code-subscriptions')]);
            const opening=page.waitForEvent('popup');await share.getByRole('button',{name:'Apri',exact:true}).click();const preview=await opening;
            await expect(preview.getByText(original.sport_association.denomination,{exact:true})).toBeVisible();await expect(preview.getByText("Stai compilando l'iscrizione come",{exact:false})).toContainText('Socio e Tesserato');
            await take('signup-current-module-real-preview',undefined,preview);await preview.close();await share.getByRole('button',{name:'Chiudi',exact:true}).first().click();
            anonymous=await context.browser().newContext(manualConfig.use);await anonymous.addInitScript(referenceDate=>{const NativeDate=Date;const fixed=new NativeDate(referenceDate+'T12:00:00Z').getTime();
                window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[fixed]));}static now(){return fixed;}};},input.reference_date);
            const publicPage=await anonymous.newPage();observeErrors(publicPage);
            publicPage.on('request',r=>{if(r.url().startsWith(input.origin)&&/^(POST|PATCH|PUT|DELETE)$/.test(r.method()))writes.push(new URL(r.url()).pathname);});
            await publicPage.goto(link);await expect(publicPage.getByText(original.sport_association.denomination,{exact:true})).toBeVisible();
            expect(await publicPage.evaluate(()=>JSON.parse(localStorage.getItem('sessionToken')))).toBeNull();
            const publicResponse=await anonymous.request.get(input.origin+'/api/search/profile/'+original.username.toLowerCase()+'?module_info=1');expect(publicResponse.status()).toBe(200);
            const publicModule=(await publicResponse.json()).data.user.sport_association;for(const[,key]of fields)expect(publicModule.configuration[key]).toBe(true);
            expect(publicModule.additional_fields).toEqual(module.additional_fields);expect(publicModule.additional_sections).toEqual(module.additional_sections);expect(publicModule.enabled_for).toEqual(['associate-membership']);
            await publicPage.getByRole('button',{name:'Continua senza account',exact:true}).click();await expect(publicPage.locator('input[name="phoneAssociate"]')).toBeVisible();
            await take('public-signup-required-fields',publicPage.locator('#subscribe-form'),publicPage);
            const invalidContinue=async()=>{await publicPage.getByRole('button',{name:'Continua',exact:true}).click();await publicPage.getByRole('button',{name:'Ok, capito!',exact:true}).click();};
            await invalidContinue();await expect(publicPage.locator('#subscribe-form')).toContainText('Il numero di telefono è obbligatorio.');await expect(publicPage.locator('#subscribe-form')).toContainText("L'indirizzo email è obbligatorio.");
            await take('public-signup-empty-required-fields',publicPage.locator('#subscribe-form'),publicPage);
            await publicPage.locator('input[name="bornDateAssociate"]').fill('2015-01-15');await expect(publicPage.getByText('Informazioni tutore del minorenne',{exact:true})).toBeVisible();
            await expect(publicPage.locator('input[name="phoneAssociateTutor"]')).toBeVisible();await expect(publicPage.locator('input[name="emailAssociateTutor"]')).toBeVisible();await invalidContinue();
            await expect(publicPage.locator('input[name="phoneAssociateTutor"]').locator('xpath=ancestor::div[contains(@class,"form-group")][1]')).toContainText('Il numero di telefono è obbligatorio.');
            await expect(publicPage.locator('input[name="emailAssociateTutor"]').locator('xpath=ancestor::div[contains(@class,"form-group")][1]')).toContainText("L'indirizzo email è obbligatorio.");
            await take('public-signup-minor-required-fields',publicPage.locator('input[name="phoneAssociateTutor"]').locator('xpath=ancestor::div[contains(@class,"border-top")][1]'),publicPage);
            expect(writes).toEqual([]);
            // Return to an adult profile for the completed example; minor contacts were tested without submitting a child.
            await publicPage.locator('input[name="bornDateAssociate"]').fill('2000-01-01');await expect(publicPage.getByText('Informazioni tutore del minorenne',{exact:true})).not.toBeVisible();
            for(const[name,value]of Object.entries({firstNameAssociate:person.first_name,lastNameAssociate:person.last_name,bornCityAssociate:'Roma',addressAssociate:'Via delle Attività 8',
                addressCityAssociate:'Roma',capAssociate:'00100',emailAssociate:person.email,phoneAssociate:person.phone}))await publicPage.locator(`[name="${name}"]`).fill(value);
            const taxInput=publicPage.locator('input[name="taxCodeAssociate"]');const calculated=publicPage.waitForResponse(r=>new URL(r.url()).pathname==='/api/subscription/calculate-tax-code'&&r.request().method()==='POST');
            await taxInput.locator('..').getByRole('button').click();const calculation=await calculated;expect(calculation.status()).toBe(200);fiscalCode=(await calculation.json()).tax_code;
            expect(fiscalCode).toMatch(/^[A-Z0-9]{16}$/);await expect(taxInput).toHaveValue(fiscalCode);
            const custom=publicPage.locator('input[name="'+customField.props.name+'"]');await expect(custom).toHaveAttribute('placeholder','Esempio: M');await expect(custom).toHaveAttribute('required','');
            await invalidContinue();await expect(publicPage.locator('#subscribe-form')).toContainText('Il campo Taglia maglietta è obbligatorio.');
            await take('public-signup-empty-custom-field-blocks',custom.locator('..'),publicPage);await custom.fill('M');
            await take('public-signup-valid-profile-and-custom-field',publicPage.locator('#subscribe-form'),publicPage);
            await publicPage.getByRole('button',{name:'Continua',exact:true}).click();await expect(publicPage.getByRole('heading',{name:"Firma Modulo d'Iscrizione",exact:true})).toBeVisible();
            await publicPage.getByText('MATERIALE PER ALLENAMENTO',{exact:true}).click();await expect(publicPage.getByText('Porta borraccia e abbigliamento comodo.',{exact:true})).toBeVisible();
            const signatureNext=publicPage.getByRole('button',{name:'Continua',exact:true});await expect(signatureNext).toBeDisabled();await take('public-signup-required-signature-blocks',publicPage.locator('#subscribe-form'),publicPage);
            const draw=async()=>{const canvas=publicPage.locator('canvas');await expect(canvas).toBeVisible();await expect.poll(()=>canvas.evaluate(element=>element.width>0&&element.height>0)).toBe(true);
                await canvas.scrollIntoViewIfNeeded();const box=await canvas.boundingBox();await publicPage.mouse.move(box.x+box.width*.15,box.y+box.height*.5);await publicPage.mouse.down();
                for(let index=1;index<=32;index++)await publicPage.mouse.move(box.x+box.width*(.15+index*.018),box.y+box.height*(.5+Math.sin(index*.6)*.15));await publicPage.mouse.up();
                await expect(signatureNext).toBeEnabled();return canvas.evaluate(element=>element.toDataURL());};
            await draw();await publicPage.getByRole('button',{name:'Cancella firma',exact:true}).click();await expect(signatureNext).toBeDisabled();await take('public-signup-cleared-signature-blocks',publicPage.locator('canvas').locator('..'),publicPage);
            submittedSignature=await draw();await take('public-signup-fictional-signature',publicPage.locator('canvas').locator('..'),publicPage);
            await signatureNext.click();await expect(publicPage.getByRole('heading',{name:'Certificato Medico',exact:true})).toBeVisible();await publicPage.getByRole('button',{name:'Continua',exact:true}).click();
            await expect(publicPage.getByRole('heading',{name:'Riepilogo Iscrizione',exact:true})).toBeVisible();await expect(publicPage.locator('#subscribe-form')).toContainText(person.first_name);
            await expect(publicPage.locator('#subscribe-form')).toContainText('Il modulo di iscrizione è stato firmato.');await expect(publicPage.locator('#subscribe-form img[alt="signature"]')).toHaveAttribute('src',submittedSignature);
            await take('public-signup-summary-before-submit',publicPage.locator('#subscribe-form'),publicPage);
            const creating=publicPage.waitForResponse(r=>new URL(r.url()).pathname==='/api/subscription/add'&&r.request().method()==='POST');creationRequested=true;
            await publicPage.getByRole('button',{name:'Completa iscrizione',exact:true}).click();const created=await creating;createdRequest=created.request().postDataJSON();const creation=await created.json();
            await discoverOwned();expect(created.status()).toBe(200);expect(creation.status).toBe('success');expect(Number(creation.amount)).toBe(30);expect(owned.subscription_id).toBeTruthy();
            expect(owned.payment_id).toBe(creation.payment_id);expect(createdRequest.signature).toEqual({there_is_signature:true,data:submittedSignature});
            expect(createdRequest.new_user_account.new_member).toBe(false);expect(createdRequest.additional_fields[0].props.value).toBe('M');
            await page.goto(input.origin+'/#/members/list');await page.reload();const createdRow=page.locator('[data-row]').filter({hasText:'Elisa Ferri'});await expect(createdRow).toBeVisible();
            const savedSignup=(await read(`subscription/${owned.subscription_id}/info`)).data.info;
            expect(savedSignup.status_flag).toBe(2);expect(savedSignup.signature_present).toBe(true);expect(savedSignup.type).toBe(2);expect(savedSignup.sport_association).toBe(input.association_id);
            expect(savedSignup.user.email).toBe(original.email);
            expect(Object.values((await records()).subscriptions).find(row=>row.subscription_id===owned.subscription_id).user.user_id).toBe(input.user_id);
            expect(savedSignup.associate.email).toBe(person.email);expect(savedSignup.associate.tax_code).toBe(fiscalCode);
            expect(savedSignup.additional_fields[0].props).toMatchObject({label:'Taglia maglietta',value:'M',required:true});expect(savedSignup.payment).toBe(owned.payment_id);
            const signupPayment=Object.values((await records()).payments).find(payment=>payment.payment_id===owned.payment_id);expect(signupPayment).toBeTruthy();
            expect(Number(signupPayment.amount)).toBe(30);expect(signupPayment.paid).toBe(false);expect(savedSignup.medical).toBeNull();
            if(savedSignup.document_pdf){owned.document_ids.push(typeof savedSignup.document_pdf==='string'?savedSignup.document_pdf:savedSignup.document_pdf.document_id);saveOwned();}
            await take('public-signup-pending-request-after-reload',createdRow);
            const rendered=await page.goto(input.origin+`/api/document/subscription/${owned.subscription_id}/view/`);expect(rendered.status()).toBe(200);
            await expect(page.locator('.content-wrapper')).toContainText(/Elisa/i);await expect(page.locator('.content-wrapper')).toContainText(/Ferri/i);await expect(page.locator('.content-wrapper')).toContainText('Taglia maglietta');
            const exactSignature=page.locator(`.content-wrapper img[src="${submittedSignature}"]`);await expect(exactSignature).toBeVisible();await expect.poll(()=>exactSignature.evaluate(image=>image.complete&&image.naturalWidth>0)).toBe(true);
            await take('public-signup-stored-signature-in-renderer',exactSignature.locator('..'));
            const finalRecords=await records();expect(Object.values(finalRecords.subscriptions)).toHaveLength(Object.values(baseline.subscriptions).length+1);
            expect(Object.values(finalRecords.payments)).toHaveLength(Object.values(baseline.payments).length+1);
            expect(browserErrors).toEqual([]);expect(failures).toEqual([]);expect(writes.filter(route=>route==='/api/subscription/add')).toHaveLength(1);
            expect(writes.every(route=>['/api/subscription/calculate-tax-code','/api/subscription/add'].includes(route))).toBe(true);
            report.public_signup={submitted:1,status_flag:2,amount:30,paid:false,account_created:false,signature_present:true,custom_field_value:'M',medical_attached:false,
                signature_sha256:crypto.createHash('sha256').update(Buffer.from(submittedSignature.split(',')[1],'base64')).digest('hex'),signature_renderer_matches_submitted_pixels:true};
            await page.goto(input.origin+'/#/');await expect(page.getByText('Organizzazione',{exact:true})).toBeVisible();await openAccount();
            await editor().fill('Istruzioni dimostrative: contattare la segreteria prima di effettuare un bonifico.');await take('checkout-information-before-save',editor().locator('..'));await save();
            saved=await profile();expect(saved.sport_association.checkout_info).toBe('<p>Istruzioni dimostrative: contattare la segreteria prima di effettuare un bonifico.</p>');
            await expect(editor()).toHaveText('Istruzioni dimostrative: contattare la segreteria prima di effettuare un bonifico.');await take('checkout-information-after-reload',editor().locator('..'));
            const reader=await actor('reader');await reader.open('Impostazioni','/#/profile');await reader.page.getByText('Informazioni Account',{exact:true}).click();
            for(const[label]of fields)await expect(block(reader.page,label).locator('input[type="checkbox"]')).toBeDisabled();
            expect((await reader.api('profile/update',{method:'PATCH',data:{user_data:{...originalPayload.user_data,sport_association:{...saved.sport_association,checkout_info:'Denied'}}}})).status()).toBe(403);
            expect((await reader.api('profile/update/subscription/template',{method:'PATCH',data:{sport_association:{...saved.sport_association,enabled_for:['membership']}}})).status()).toBe(403);
            expect((await reader.api('subscription/add',{method:'POST',data:createdRequest})).status()).toBe(403);
            expect((await profile()).sport_association).toEqual(saved.sport_association);expect(await records()).toEqual(finalRecords);
            await reader.open('Organizzazione','/#/members/subscription/template');await expect(reader.page.locator('#bkn_form_account_update_submit')).toBeDisabled();
            report.external_requirements=[{section:'docs/impostazioni.mdx#checkout',status:'needs_external_verification',reason:'Actual checkout display depends on enabled online payments and real Stripe session creation; no Stripe session or payment requested.'}];
        }catch(cause){error=cause;throw cause;}finally{
            const cleanupFailures=[];
            if(anonymous)await anonymous.close();
            // Recover only the exact owned person if the response or subsequent assertions failed.
            if(creationRequested&&!owned.subscription_id&&fiscalCode){try{await discoverOwned();}catch{cleanupFailures.push('Owned signup discovery unavailable');}}
            if(owned.subscription_id){try{expect((await api(`subscription/${owned.subscription_id}/delete`,{method:'POST'})).status()).toBe(200);}catch{cleanupFailures.push('Owned signup deletion');}}
            for(const documentId of owned.document_ids){try{expect([200,404]).toContain((await api(`document/${documentId}/delete`,{method:'DELETE'})).status());}catch{cleanupFailures.push('Owned signup document deletion');}}
            if(templateSaved){try{expect((await api('profile/update/subscription/template',{method:'PATCH',data:{sport_association:original.sport_association}})).status()).toBe(200);}catch{cleanupFailures.push('Original module configuration restoration');}}
            try{expect((await api('profile/update',{method:'PATCH',data:originalPayload})).status()).toBe(200);}catch{cleanupFailures.push('Original signup/checkout preferences restoration');}
            try{await page.unroute(rendererPattern,authenticateRenderer);}catch{cleanupFailures.push('Renderer authentication route removal');}
            owned.cleanup_finished=cleanupFailures.length===0;saveOwned();
            if(cleanupFailures.length){report.cleanup_failures=cleanupFailures;if(!error)throw new Error('Signup settings cleanup failed');}
        }
        const normalized=association=>({...association,regulation:association.regulation?.trim(),demand:association.demand?.trim()});
        expect(normalized((await profile()).sport_association)).toEqual(normalized(original.sport_association));expect(await records()).toEqual(baseline);
        report.fixture_cleanup={private_manifest:'private/'+id+'-owned.json',api_cleanup:'Owned signed registration, its unpaid quota and known attached documents',
            fixture_reset_required:'The seeded runner separately clears the orphan fictional Associate/Signature objects scoped to the disposable association.'};
        report[spec.outcome.field]={five_requirements_saved_reopened:true,public_configuration_matches:true,adult_contact_validation:true,minor_contact_validation:true,
            module_type_saved_reopened:true,simple_fee_saved_reopened:true,added_clause_saved_reopened:true,optional_custom_field_saved_reopened:true,required_custom_field_saved_reopened:true,
            public_module_matches_saved_configuration:true,current_link_copied_and_opened:true,required_custom_field_blocks:true,required_signature_blocks:true,cleared_signature_blocks_again:true,
            drawn_signature_enables_continue:true,public_signup_persisted:true,pending_signed_status:2,unpaid_signup_amount:30,custom_field_value_persisted:true,stored_signature_pixels_match:true,
            no_new_account_created:true,baseline_records_preserved:true,owned_registration_and_unpaid_quota_removed:true,checkout_text_saved_reopened:true,reader_write_status:403,
            reader_template_write_status:403,reader_signup_write_status:403,reader_denial_preserves_configuration:true,original_configuration_restored:true,business_records_preserved:true};
        expect(report[spec.outcome.field]).toEqual(spec.outcome.expected);
        report.checks=['Real template UI saves a single signup type, simple quota, visible clause and optional/required custom field',
            'Anonymous adult/minor contact validation, custom-field block, blank/cleared signature block and canvas drawing through the real wizard',
            'Exactly one real public signup creates a pending signed request with unpaid quota and saved custom value; renderer contains the submitted signature pixels',
            'Reader profile/template/signup writes refused; original configuration and baseline business records restored',
            'Checkout saved locally only; no external payment, delivery, cryptographic signature certification or account creation claimed'];
    }});
