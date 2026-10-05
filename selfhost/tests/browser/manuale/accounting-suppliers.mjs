import {scenario, expect} from './scenario.mjs';
import {accountingDocumentsAuthoredWorkflows} from '../../../../docs/manuale/accounting-documents-authored-workflows.mjs';
const id = 'accounting-suppliers-manage', spec = accountingDocumentsAuthoredWorkflows[id];
await scenario({id, prefix:spec.prefix.replace(/\/$/, ''), sources:spec.sources,
    actions:async ({page, api, actor, capture, report, input}) => {
        expect(input.fixture_version).toBe(8);
        const read = async route => {const response=await api(route); expect(response.status()).toBe(200); return response.json();};
        const list = async () => (await read('supplier/list?all=true')).data;
        const ordered = records => [...records].sort((a,b)=>a.supplier_id.localeCompare(b.supplier_id));
        const baseline=ordered(await list()), payments=(await read('payment/list?pagination[perpage]=100')).data;
        const take = async (checkpoint, focus) => {await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0,{timeout:15000});
            await capture(page,spec.checkpoints.findIndex(p=>p.id===checkpoint)+1,checkpoint,focus);};
        const row = name => page.locator('[data-row]').filter({hasText:name.toUpperCase()});
        const save = () => page.locator('#portal-save-foreground').getByRole('button',{name:'Salva',exact:true}).click();
        const close = async () => {await page.locator('[role="dialog"]:visible .drawer-header button').first().click();
            await expect(page.locator('#supplier_form')).toHaveCount(0);};
        const selectType = async label => {const form=page.locator('#supplier_form');
            await form.getByRole('textbox',{name:'Tipo',exact:true}).click();
            const option=form.locator('.list-item:visible').filter({hasText:new RegExp('^\\s*'+label+'\\s*$')});
            await expect(option).toHaveCount(1); await option.click();};
        let supplier, customer, error;
        try {
            const sidebar=page.locator('#bkn_aside');
            if (!await sidebar.getByText('Fornitori e Clienti',{exact:true}).isVisible()) await sidebar.getByText('Gestione',{exact:true}).first().click();
            await sidebar.getByText('Fornitori e Clienti',{exact:true}).click();
            await expect(page.getByRole('heading',{name:/^Fornitori e Clienti/})).toBeVisible();
            await take('suppliers-list-baseline');
            async function create(name, type, checkpoint, persisted) {
                await page.getByRole('button',{name:'Anagrafica',exact:true}).click();
                const form=page.locator('#supplier_form'); await expect(form).toBeVisible();
                await form.locator('input[name="name"]').fill(name);
                await form.locator('input[name="address"]').fill('Via del Manuale 12');
                await form.locator('input[name="email"]').fill(type==='Fornitore'?'fornitore@example.invalid':'cliente@example.invalid');
                await form.locator('input[name="phone_number"]').fill('0000000000');
                await selectType(type); await take(checkpoint,page.locator('[role="dialog"]:visible'));
                const responsePromise=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/supplier/add'&&r.request().method()==='POST');
                await save(); const response=await responsePromise; const record=await response.json();
                expect(response.status()).toBe(201); expect(record.supplier_id).toBeTruthy();
                // Record ownership immediately, before reload assertions, for cleanup on failure.
                if(type==='Fornitore') supplier=record.supplier_id; else customer=record.supplier_id;
                await expect(form).toHaveCount(0); await page.reload(); await expect(row(name)).toBeVisible();
                expect((await list()).find(r=>r.supplier_id===record.supplier_id)).toMatchObject({name,type:type==='Fornitore'?'supplier':'customer',address:'Via del Manuale 12'});
                await take(persisted,row(name));
            }
            await create('Fornitore manuale locale','Fornitore','supplier-filled-before-save','supplier-created-after-reload');
            await create('Cliente manuale locale','Cliente','customer-filled-before-save','customer-created-after-reload');
            await row('Fornitore manuale locale').getByText('FORNITORE MANUALE LOCALE',{exact:true}).click();
            const form=page.locator('#supplier_form'); await expect(form.locator('input[name="name"]')).toHaveValue('Fornitore manuale locale');
            await form.locator('input[name="address"]').fill('Via del Manuale 24');
            await form.locator('input[name="email"]').fill('recapiti@example.invalid');
            await take('supplier-edit-filled',page.locator('[role="dialog"]:visible'));
            const edit=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/supplier/${supplier}/update`&&r.request().method()==='PATCH');
            await save(); expect((await edit).status()).toBe(200);
            await expect(form.locator('input[name="address"]')).toHaveValue('Via del Manuale 24');
            await close(); await page.reload(); await row('Fornitore manuale locale').getByText('FORNITORE MANUALE LOCALE',{exact:true}).click();
            await expect(form.locator('input[name="address"]')).toHaveValue('Via del Manuale 24');
            await expect(form.locator('input[name="email"]')).toHaveValue('recapiti@example.invalid');
            await take('supplier-edit-reopened',page.locator('[role="dialog"]:visible')); await close();
            const saved=ordered(await list()); const reader=await actor('reader');
            for(const [route,method,data] of [['supplier/add','POST',{name:'Denied',type:'customer'}],
                [`supplier/${supplier}/update`,'PATCH',{name:'Denied'}],[`supplier/${supplier}/delete`,'DELETE',{}],
                ['supplier/bulk-delete','DELETE',{supplier_ids:[supplier,customer]}]]) expect((await reader.api(route,{method,data})).status()).toBe(403);
            expect(ordered(await list())).toEqual(saved);
            await page.locator(`#action-col-${supplier}`).getByRole('button',{name:'Elimina',exact:true}).click();
            const confirmation=page.locator('.swal2-popup'); await expect(confirmation).toContainText("Vuoi eliminare l'anagrafica?");
            await take('supplier-delete-cancel',confirmation); await confirmation.getByRole('button',{name:'Annulla',exact:true}).click();
            expect(ordered(await list())).toEqual(saved);
            await page.locator(`#action-col-${supplier}`).getByRole('button',{name:'Elimina',exact:true}).click();
            await take('supplier-delete-confirm',confirmation);
            const deleted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/supplier/${supplier}/delete`&&r.request().method()==='DELETE');
            await confirmation.getByRole('button',{name:'Elimina',exact:true}).click(); expect((await deleted).status()).toBe(200);
            await page.reload(); await expect(row('Fornitore manuale locale')).toHaveCount(0); await expect(row('Cliente manuale locale')).toBeVisible();
            expect((await list()).find(r=>r.supplier_id===supplier)).toBeUndefined();
            expect(ordered(await list())).toEqual(saved.filter(r=>r.supplier_id!==supplier));
            await take('supplier-deleted-after-reload'); supplier=null;
        } catch(cause) {error=cause; throw cause;} finally {
            for(const owned of [supplier,customer].filter(Boolean)) try {
                if((await list()).some(r=>r.supplier_id===owned)) expect((await api(`supplier/${owned}/delete`,{method:'DELETE'})).status()).toBe(200);
            } catch(cause) {report.cleanup_failures=[...(report.cleanup_failures||[]),'owned supplier/customer']; if(!error)throw cause;}
        }
        expect(ordered(await list())).toEqual(baseline); expect((await read('payment/list?pagination[perpage]=100')).data).toEqual(payments);
        await page.reload(); await expect(row('Cliente manuale locale')).toHaveCount(0); await take('suppliers-baseline-restored');
        report[spec.outcome.field]={supplier_created_via_ui:true,customer_created_via_ui:true,both_types_persist_after_reload:true,
            supplier_edit_reopened:true,delete_cancel_preserved_record:true,supplier_deleted_after_reload:true,control_customer_preserved:true,
            reader_write_denials:4,reader_denials_preserve_state:true,baseline_suppliers_preserved:true,baseline_payments_preserved:true,owned_suppliers_removed:true};
        expect(report[spec.outcome.field]).toEqual(spec.outcome.expected);
        report.checks=['Real UI supplier and customer creation, persisted edit and reopened fields','Delete cancelled then confirmed; control customer preserved','Four reader denials preserve records; only owned records cleaned up'];
    }});
