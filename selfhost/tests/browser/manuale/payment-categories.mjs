import {setCheckbox} from './controls.mjs';
import {scenario, expect} from './scenario.mjs';
import {paymentMaintenanceAuthoredWorkflows} from '../../../../docs/manuale/payment-maintenance-authored-workflows.mjs';
const id = 'payment-categories-manage', spec = paymentMaintenanceAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        const read = async route => {const response = await api(route); expect(response.status()).toBe(200); return response.json();};
        const list = async () => (await read('payment/category/list')).data;
        const baseline = await list(), baselinePayments = (await read('payment/list?pagination[perpage]=100')).data;
        const take = async (checkpoint, focus) => {await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout:15000});
            await capture(page, spec.checkpoints.findIndex(point => point.id === checkpoint) + 1, checkpoint, focus);};
        const select = async (modal, label, option) => {await modal.getByRole('textbox', {name:label, exact:true}).click();
            await modal.locator('.list-item').getByText(option, {exact:true}).click();};
        let owned, error;
        try {
            await open('Pagamenti', '/#/payment/list');
            await page.locator('a[href="/#/payment/category/list"]').first().click();
            await expect(page.getByText('Causali dei pagamenti', {exact:true})).toBeVisible();
            await take('categories-baseline');
            await page.getByText('Crea causale', {exact:true}).click();
            const create = page.locator('#addAccountModal'); await expect(create).toBeVisible();
            await create.getByPlaceholder('Nome', {exact:true}).fill('Quota laboratorio dimostrativa');
            await create.locator('select[name="expense"]').selectOption('false');
            await select(create, 'Tipologia', 'Istituzionale');
            await select(create, 'Tipo IVA', 'IVA Esente');
            await select(create, 'Tipo detrazione', 'No');
            await setCheckbox(create.locator('input[name="tax_deductible"]'),false);
            await take('category-fields-before-save', create.locator('.modal-content'));
            const added = page.waitForResponse(r => new URL(r.url()).pathname === '/api/payment/category/add' && r.request().method() === 'POST');
            await create.getByRole('button', {name:'Salva',exact:true}).click();
            const response = await added, result = await response.json(); owned = result.data?.payment_category_id;
            expect(response.status()).toBe(200); expect(owned).toBeTruthy();
            await page.reload();
            // The category archive is locally paginated (ten rows per page).
            // Find the newly created category through its real search control.
            await page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}).fill('Quota laboratorio dimostrativa');
            let row = page.locator('[data-row]').filter({hasText:'QUOTA LABORATORIO DIMOSTRATIVA'});
            await expect(row).toBeVisible();
            expect((await list()).find(c => c.payment_category_id === owned)).toMatchObject({name:'Quota laboratorio dimostrativa',expense:false,type:1,tax_deductible:false,vat_management:{vat:1,deduction:0}});
            await take('category-created-after-reload', row);
            await row.locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
            const edit = page.locator('#editAccountModal-' + owned); await expect(edit).toBeVisible();
            await edit.getByPlaceholder('Nome', {exact:true}).fill('Quota laboratorio serale');
            await select(edit, 'Tipologia', 'Commerciale');
            await take('category-edit-before-save', edit.locator('.modal-content'));
            const edited = page.waitForResponse(r => new URL(r.url()).pathname === `/api/payment/category/${owned}/update` && r.request().method() === 'PATCH');
            await edit.getByRole('button', {name:'Salva',exact:true}).click(); expect((await edited).status()).toBe(200);
            await page.reload();
            await page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}).fill('Quota laboratorio serale');
            row = page.locator('[data-row]').filter({hasText:'QUOTA LABORATORIO SERALE'});
            await expect(row).toBeVisible(); await take('category-edit-after-reload', row);
            const saved = (await list()).find(c => c.payment_category_id === owned); expect(saved.name).toBe('Quota laboratorio serale'); expect(saved.type).toBe(2);
            const reader = await actor('reader'); await reader.page.goto(input.origin + '/#/payment/category/list');
            await expect(reader.page.getByText('Crea causale',{exact:true})).toHaveCount(0);
            await reader.page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}).fill('Quota laboratorio serale');
            const readerRow = reader.page.locator('[data-row]').filter({hasText:'QUOTA LABORATORIO SERALE'});
            await expect(readerRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled(); await expect(readerRow.locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
            for (const [route, method, data] of [['payment/category/add','POST',{name:'Denied',expense:false,type:1}],
                [`payment/category/${owned}/update`,'PATCH',{name:'Denied'}],[`payment/category/${owned}/delete`,'DELETE',{}]])
                expect((await reader.api(route,{method,data})).status()).toBe(403);
            expect((await list()).find(c => c.payment_category_id === owned)).toEqual(saved);
            const shared = baseline.find(c => !c.sport_association); expect(shared).toBeTruthy();
            expect((await api(`payment/category/${shared.payment_category_id}/update`,{method:'PATCH',data:{name:'Denied shared'}})).status()).toBe(404);
            expect((await api(`payment/category/${shared.payment_category_id}/delete`,{method:'DELETE'})).status()).toBe(404);
            await row.locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
            const confirmation = page.locator('.swal2-popup'); await expect(confirmation).toContainText('Vuoi eliminare la causale di pagamento?');
            await take('category-delete-cancel-confirmation', confirmation); await confirmation.getByRole('button',{name:'Annulla',exact:true}).click();
            expect((await list()).find(c=>c.payment_category_id===owned).deleted).toBe(false);
            await row.locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click(); await take('category-delete-confirmation', confirmation);
            const deleted = page.waitForResponse(r=>new URL(r.url()).pathname===`/api/payment/category/${owned}/delete` && r.request().method()==='DELETE');
            await confirmation.getByRole('button',{name:'Elimina',exact:true}).click(); expect((await deleted).status()).toBe(200);
            await page.reload();
            await page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}).fill('Quota laboratorio serale');
            await expect(row).toContainText('Eliminata');
            expect((await list()).find(c=>c.payment_category_id===owned).deleted).toBe(true); await take('category-deleted-after-reload',row);
        } catch (cause) {error=cause; throw cause;} finally {
            if (owned) {try {expect((await api(`payment/category/${owned}/delete`,{method:'DELETE'})).status()).toBe(200);}
                catch (cause) {report.cleanup_failures=['owned category soft deletion']; if(!error)throw cause;}}
        }
        expect((await list()).filter(c=>c.payment_category_id!==owned)).toEqual(baseline);
        expect((await read('payment/list?pagination[perpage]=100')).data).toEqual(baselinePayments);
        report[spec.outcome.field]={created_reopened:true,edited_reopened:true,delete_cancel_preserved:true,soft_deleted_reopened:true,
            reader_write_denials:3,shared_category_write_denials:2,baseline_categories_preserved:true,payments_preserved:true,owned_category_soft_deleted:true};
        expect(report[spec.outcome.field]).toEqual(spec.outcome.expected);
        report.checks=['UI create/edit/soft delete and real persisted category values; no tax eligibility claim','reader denials and shared category mutation refused','baseline payments and categories unchanged'];
    }});
