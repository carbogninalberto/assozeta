import {scenario, expect} from './scenario.mjs';
import {accountingDocumentsAuthoredWorkflows} from '../../../../docs/manuale/accounting-documents-authored-workflows.mjs';
const id='accounting-supplier-invoices-manage', spec=accountingDocumentsAuthoredWorkflows[id];
await scenario({id,prefix:spec.prefix.replace(/\/$/,''),sources:spec.sources,
    actions:async ({page,api,actor,input,capture,report}) => {
        expect(input.fixture_version).toBe(8);
        const read=async route=>{const response=await api(route);expect(response.status()).toBe(200);return response.json();};
        const list=async()=>Object.values((await read('invoice-suppliers/list?pagination[perpage]=100')).data);
        const ordered=rows=>[...rows].sort((a,b)=>a.invoice_supplier_id.localeCompare(b.invoice_supplier_id));
        const baseline=ordered(await list()),payments=(await read('payment/list?pagination[perpage]=100')).data;
        const accounts=(await read('balance-sheet/accounts/list')).data;
        const categories=(await read('payment/category/list')).data;
        const take=async(checkpoint,focus)=>{await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0,{timeout:15000});
            await capture(page,spec.checkpoints.findIndex(p=>p.id===checkpoint)+1,checkpoint,focus);};
        const choose=async(modal,placeholder,label)=>{await (placeholder==='Seleziona stato' ? modal.locator('input[name="paid"]').locator('..').locator('input:not([type="hidden"])') : modal.getByPlaceholder(placeholder,{exact:true})).click();
            const escaped=label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
            const option=modal.locator('.list-item:visible').filter({hasText:new RegExp('^\\s*'+escaped+'\\s*$','i')});
            await expect(option).toHaveCount(1); await option.click();};
        let supplier,category,invoice,payment,error;
        const current=async()=>{const saved=(await list()).find(r=>r.invoice_supplier_id===invoice);expect(saved).toBeTruthy();return saved;};
        const reopen=async()=>{await page.locator(`#action-col-${invoice}`).locator('button:is([title="Modifica"],[data-original-title="Modifica"]), button[data-original-title="Modifica"]').click();
            const modal=page.locator('#editModal-'+invoice);await expect(modal).toBeVisible();return modal;};
        const dates=async modal=>{await modal.locator('input[name="payment_date"]').fill(input.reference_date);
            await modal.locator('input[name="expire_date"]').fill('2026-10-15');};
        const save=async modal=>{const updated=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/invoice-suppliers/${invoice}/update`&&r.request().method()==='PATCH');
            await modal.getByRole('button',{name:'Salva',exact:true}).click();expect((await updated).status()).toBe(200);await page.reload();};
        try {
            const addedSupplier=await api('supplier/add',{method:'POST',data:{name:'Fornitore fattura manuale',type:'supplier'}});
            expect(addedSupplier.status()).toBe(201);supplier=(await addedSupplier.json()).supplier_id;expect(supplier).toBeTruthy();
            const supplierRecord=(await read('supplier/list?all=true')).data.find(row=>row.supplier_id===supplier);
            expect(supplierRecord).toBeTruthy();expect(supplierRecord.name).toBe('Fornitore fattura manuale');
            const supplierLabel=`${supplierRecord.name} (${supplierRecord.tax_code})`;
            let usable=categories.find(c=>!c.deleted&&c.name.toLowerCase()==='pagamento fornitore'&&c.sport_association===input.association_id);
            if(!usable){const response=await api('payment/category/add',{method:'POST',data:{name:'Pagamento Fornitore',expense:true,type:1,tax_deductible:false,vat_management:{vat:1,deduction:0}}});
                expect(response.status()).toBe(200);category=(await response.json()).data.payment_category_id;expect(category).toBeTruthy();}
            const sidebar=page.locator('#bkn_aside');
            if(!await sidebar.getByText('Fatture Passive',{exact:true}).isVisible())await sidebar.getByText('Documenti fiscali',{exact:true}).first().click();
            await sidebar.getByText('Fatture Passive',{exact:true}).click();await expect(page.getByRole('heading',{name:/^Fatture fornitori/})).toBeVisible();
            await page.locator('[data-target="#addInvoiceModal"]').click();const create=page.locator('#addInvoiceModal');await expect(create).toBeVisible();
            await create.locator('input[name="invoice_identifier"]').fill('MANUALE-PASSIVA-12');await create.locator('input[name="amount"]').fill('12,00');
            await dates(create);await create.locator('select[name="custom_accounts"]').selectOption(input.cash_account_id);
            await choose(create,'Seleziona fornitore',supplierLabel);await choose(create,'Seleziona stato','Non pagata');
            await create.locator('textarea[name="notes"]').fill('Registrazione locale di prova: nessun invio.');
            await take('supplier-invoice-fields-before-save',create.locator('.modal-content'));
            const added=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/invoice-suppliers/add'&&r.request().method()==='POST');
            await create.getByRole('button',{name:'Salva',exact:true}).click();expect((await added).status()).toBe(200);
            const diff=(await list()).filter(r=>!baseline.some(b=>b.invoice_supplier_id===r.invoice_supplier_id));expect(diff).toHaveLength(1);
            invoice=diff[0].invoice_supplier_id;payment=diff[0].payment.payment_id;
            expect(payment).toBeTruthy();await page.reload();await expect(page.locator(`#action-col-${invoice}`)).toBeVisible();
            let saved=await current();expect(saved).toMatchObject({invoice_identifier:'MANUALE-PASSIVA-12',paid:false,supplier:{supplier_id:supplier},
                payment:{payment_id:payment,expense:true,paid:false,custom_accounts:input.cash_account_id}});expect(Number(saved.amount)).toBe(12);expect(Number(saved.payment.amount)).toBe(12);
            await take('supplier-invoice-created-after-reload');
            let modal=await reopen();await expect(modal.locator('input[name="amount"]')).toBeDisabled();
            await expect(modal.locator('input#supplier')).toBeDisabled();
            // The disabled amount plus the unnamed account display are separate real fields.
            await expect(modal.locator('input.form-control:disabled:not([name])')).toHaveValue(saved.payment.custom_account_name.toUpperCase());
            await take('supplier-invoice-reopened-immutable-fields',modal.locator('.modal-content'));
            await modal.locator('input[name="invoice_identifier"]').fill('MANUALE-PASSIVA-12-AGGIORNATA');await dates(modal);
            await modal.locator('textarea[name="notes"]').fill('Recapito documento aggiornato; importo invariato.');
            await take('supplier-invoice-edit-before-save',modal.locator('.modal-content'));await save(modal);
            saved=await current();expect(saved.invoice_identifier).toBe('MANUALE-PASSIVA-12-AGGIORNATA');expect(saved.notes).toBe('Recapito documento aggiornato; importo invariato.');
            expect(saved.payment.payment_id).toBe(payment);expect(Number(saved.payment.amount)).toBe(12);expect(saved.paid).toBe(false);
            modal=await reopen();await expect(modal.locator('input[name="invoice_identifier"]')).toHaveValue(saved.invoice_identifier);
            await expect(modal.locator('textarea[name="notes"]')).toHaveValue(saved.notes);await take('supplier-invoice-edit-after-reload',modal.locator('.modal-content'));
            await choose(modal,'Seleziona stato','Pagata');await dates(modal);await take('supplier-invoice-paid-before-save',modal.locator('.modal-content'));await save(modal);
            saved=await current();expect(saved.paid).toBe(true);expect(saved.payment.paid).toBe(true);expect(saved.payment.payment_id).toBe(payment);
            expect(Number(saved.payment.amount)).toBe(12);expect(saved.payment.payment_date).toBeTruthy();
            await take('supplier-invoice-paid-after-reload');modal=await reopen();
            expect(JSON.parse(await modal.locator('input[name="paid"]').inputValue()).value).toBe(true);
            await take('supplier-invoice-paid-reopened',modal.locator('.modal-content'));await modal.getByRole('button',{name:'Chiudi',exact:true}).first().click();
            const reader=await actor('reader');const before=await current();
            for(const [route,method,data] of [['invoice-suppliers/add','POST',{invoice_identifier:'Denied',amount:12,paid:false,payment_date:input.reference_date,expire_date:'2026-10-15',custom_accounts:input.cash_account_id,supplier_id:supplier}],
                [`invoice-suppliers/${invoice}/update`,'PATCH',{invoice_identifier:'Denied',paid:false}],
                [`invoice-suppliers/${invoice}/delete`,'DELETE',{}]])expect((await reader.api(route,{method,data})).status()).toBe(403);
            expect(await current()).toEqual(before);
            modal=await reopen();await choose(modal,'Seleziona stato','Non pagata');await dates(modal);await save(modal);
            saved=await current();expect(saved.paid).toBe(false);expect(saved.payment.paid).toBe(false);expect(saved.payment.payment_id).toBe(payment);
            await take('supplier-invoice-unpaid-restored');
        } catch(cause){error=cause;throw cause;} finally {
            // Invoice deletion does not remove its linked expense; clean up both owned records explicitly.
            const cleanups=[];
            if(invoice)cleanups.push([`invoice-suppliers/${invoice}/delete`,'owned invoice']);
            if(payment)cleanups.push([`payment/${payment}/delete`,'owned expense']);
            if(supplier)cleanups.push([`supplier/${supplier}/delete`,'owned supplier']);
            if(category)cleanups.push([`payment/category/${category}/delete`,'owned prerequisite category']);
            for(const [route,label]of cleanups)try{expect((await api(route,{method:'DELETE'})).status()).toBe(200);}catch(cause){
                report.cleanup_failures=[...(report.cleanup_failures||[]),label];if(!error)error=cause;}
            if(error&&report.cleanup_failures?.length)throw error;
        }
        expect(ordered(await list())).toEqual(baseline);expect((await read('payment/list?pagination[perpage]=100')).data).toEqual(payments);
        expect((await read('balance-sheet/accounts/list')).data).toEqual(accounts);
        expect((await read('supplier/list?all=true')).data.find(r=>r.supplier_id===supplier)).toBeUndefined();
        expect((await read('payment/category/list')).data.filter(c=>c.payment_category_id!==category)).toEqual(categories);
        if(category)expect((await read('payment/category/list')).data.find(c=>c.payment_category_id===category).deleted).toBe(true);
        report[spec.outcome.field]={invoice_created_via_ui:true,invoice_payment_link_preserved:true,expense_amount:12,created_unpaid:true,
            readonly_amount_account_supplier:true,invoice_edit_reopened:true,expense_amount_preserved_after_edit:true,paid_via_ui_persisted:true,
            paid_expense_persisted:true,paid_state_reopened:true,unpaid_state_restored:true,reader_write_denials:3,reader_denials_preserve_state:true,
            owned_invoice_removed:true,owned_expense_removed:true,owned_supplier_removed:true,baseline_invoices_preserved:true,
            baseline_payments_preserved:true,baseline_accounts_preserved:true,renderer_or_delivery_exercised:false};
        expect(report[spec.outcome.field]).toEqual(spec.outcome.expected);
        report.checks=['Passive invoice UI creation and persisted linked expense','Read-only amount/account/supplier; editable identifier/notes reopened','Paid/unpaid state persisted on invoice and expense; reader writes denied','Owned invoice, expense, supplier and prerequisite category cleaned up; no renderer or fiscal delivery'];
    }});
