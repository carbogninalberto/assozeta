import {setCheckbox} from './controls.mjs';
import {scenario, expect} from './scenario.mjs';
import {paymentClassificationAuthoredWorkflows} from '../../../../docs/manuale/payment-classification-authored-workflows.mjs';

const id = 'payment-classification-local', spec = paymentClassificationAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, actor, open, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        const read = async route => {const response = await api(route); expect(response.status()).toBe(200); return response.json();};
        const categories = async () => (await read('payment/category/list')).data;
        const payments = async () => (await read('payment/list?pagination[perpage]=100')).data;
        const baselineCategories = await categories(), baselinePayments = await payments();
        const number = value => Number(String(value).replace(',', '.'));
        const select = async (modal, label, value, index = 0) => {
            await modal.getByRole('textbox', {name: label, exact: true}).nth(index).click();
            await modal.locator('.list-item').getByText(value, {exact: true}).click();
            await expect(modal.locator('.svelte-select-list')).toHaveCount(0);
        };
        const take = async (checkpoint, focus) => {
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            const position = spec.checkpoints.findIndex(point => point.id === checkpoint);
            expect(position).toBeGreaterThanOrEqual(0); await capture(page, position + 1, checkpoint, focus);
        };
        const savedCategory = async () => (await categories()).find(item => item.payment_category_id === categoryId);
        const savedPayment = async () => (await payments()).find(item => item.payment_id === paymentId);
        const closeDrawer = async () => {
            const drawer = page.locator('.drawer').filter({hasText: 'Dettagli Pagamento'});
            await expect(drawer).toBeVisible(); await drawer.locator('button.close').click(); await expect(drawer).not.toBeVisible();
        };
        const editPayment = async () => {
            const row = page.locator('[data-row]').filter({hasText: 'Ripartizione laboratorio manuale'});
            await expect(row).toBeVisible(); await row.locator('button:is([title="Modifica"],[data-original-title="Modifica"]),button[data-original-title="Modifica"]').click();
            const modal = page.getByRole('dialog', {name: 'Modifica Pagamento', exact: true});
            await expect(modal.locator('input[name="amount"]')).toBeVisible(); return modal;
        };
        const savePayment = async modal => {
            const pending = page.waitForResponse(response => new URL(response.url()).pathname === `/api/payment/${paymentId}/update` && response.request().method() === 'PATCH');
            await modal.getByRole('button', {name: 'Modifica', exact: true}).click(); expect((await pending).status()).toBe(200);
            await closeDrawer(); await page.reload();
        };
        const removeExtra = async (modal, index) => {
            const row = modal.locator(`input[name="amount_${index}"]`).locator('xpath=ancestor::div[contains(@class,"justify-content-between")][1]');
            await row.getByRole('button').click();
        };
        let categoryId, paymentId, error;
        try {
            await open('Pagamenti', '/#/payment/list');
            await page.locator('a[href="/#/payment/category/list"]').first().click();
            await expect(page.getByText('Causali dei pagamenti', {exact: true})).toBeVisible();
            await take('classification-categories-baseline');
            await page.getByText('Crea causale', {exact: true}).click();
            const create = page.locator('#addAccountModal'); await expect(create).toBeVisible();
            await create.getByPlaceholder('Nome', {exact: true}).fill('Laboratorio detraibile dimostrativo');
            await create.locator('select[name="expense"]').selectOption('false');
            await select(create, 'Tipologia', 'Istituzionale'); await select(create, 'Tipo IVA', 'IVA Esente'); await select(create, 'Tipo detrazione', 'No');
            await setCheckbox(create.locator('input[name="tax_deductible"]'),true);
            await take('tax-flag-enabled-before-save', create.locator('.modal-content'));
            const pendingCategory = page.waitForResponse(response => new URL(response.url()).pathname === '/api/payment/category/add' && response.request().method() === 'POST');
            await create.getByRole('button', {name: 'Salva', exact: true}).click();
            const categoryResponse = await pendingCategory; expect(categoryResponse.status()).toBe(200); categoryId = (await categoryResponse.json()).data.payment_category_id;
            expect(categoryId).toBeTruthy(); await page.reload();
            expect(await savedCategory()).toMatchObject({expense: false, tax_deductible: true, type: 1});
            await page.getByRole('textbox', {name: 'Cerca nella tabella', exact: true}).fill('Laboratorio detraibile');
            let categoryRow = page.locator('[data-row]').filter({has: page.locator('#action-col-' + categoryId)});
            await expect(categoryRow).toBeVisible(); await take('tax-category-persisted', categoryRow);
            await categoryRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"]),button[data-original-title="Modifica"]').click();
            let edit = page.locator('#editAccountModal-' + categoryId); await expect(edit).toBeVisible();
            await expect(edit.locator('input[name="tax_deductible"]')).toBeChecked();
            await edit.getByPlaceholder('Nome', {exact: true}).fill('Laboratorio detraibile serale');
            await take('tax-flag-preserved-during-rename', edit.locator('.modal-content'));
            let changed = page.waitForResponse(response => new URL(response.url()).pathname === `/api/payment/category/${categoryId}/update` && response.request().method() === 'PATCH');
            await edit.getByRole('button', {name: 'Salva', exact: true}).click(); expect((await changed).status()).toBe(200); await page.reload();
            expect(await savedCategory()).toMatchObject({name: 'Laboratorio detraibile serale', tax_deductible: true});
            await page.getByRole('textbox', {name: 'Cerca nella tabella', exact: true}).fill('Laboratorio detraibile');
            categoryRow = page.locator('[data-row]').filter({has: page.locator('#action-col-' + categoryId)});
            await categoryRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"]),button[data-original-title="Modifica"]').click(); edit = page.locator('#editAccountModal-' + categoryId);
            await expect(edit.locator('input[name="tax_deductible"]')).toBeChecked(); await take('tax-flag-checked-after-reopen', edit.locator('.modal-content'));
            await setCheckbox(edit.locator('input[name="tax_deductible"]'),false); await take('tax-flag-disabled-before-save', edit.locator('.modal-content'));
            changed = page.waitForResponse(response => new URL(response.url()).pathname === `/api/payment/category/${categoryId}/update` && response.request().method() === 'PATCH');
            await edit.getByRole('button', {name: 'Salva', exact: true}).click(); expect((await changed).status()).toBe(200); await page.reload();
            expect((await savedCategory()).tax_deductible).toBe(false);
            await page.getByRole('textbox', {name: 'Cerca nella tabella', exact: true}).fill('Laboratorio detraibile');
            await categoryRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"]),button[data-original-title="Modifica"]').click(); edit = page.locator('#editAccountModal-' + categoryId);
            await expect(edit.locator('input[name="tax_deductible"]')).not.toBeChecked(); await take('tax-flag-disabled-after-reopen', edit.locator('.modal-content'));
            await edit.getByRole('button', {name: 'Chiudi', exact: true}).first().click();

            await page.goto(input.origin + '/#/payment/list');
            await page.getByRole('button', {name: 'Pagamento', exact: true}).click();
            let modal = page.getByRole('dialog', {name: 'Crea Nuovo Pagamento', exact: true}); await expect(modal.locator('#payment_form')).toBeVisible();
            await modal.getByPlaceholder('Inserisci una descrizione').fill('Ripartizione laboratorio manuale');
            await modal.locator('input[name="amount"]').fill('35,00');
            await modal.getByRole('textbox', {name: 'Intestato a', exact: true}).fill('Giulia'); await modal.getByText('Giulia Bianchi', {exact: true}).click();
            await select(modal, 'Causale', 'Quote e attività associative');
            await modal.getByRole('button', {name: 'Aggiungi causale', exact: true}).click();
            await select(modal, 'Causale', 'Laboratorio detraibile serale', 1); await modal.locator('input[name="amount_0"]').fill('10,00'); await modal.locator('input[name="amount_0"]').press('Tab');
            await modal.getByRole('button', {name: 'Aggiungi causale', exact: true}).click();
            await select(modal, 'Causale', 'Quote e attività associative', 2); await modal.locator('input[name="amount_1"]').fill('5,00'); await modal.locator('input[name="amount_1"]').press('Tab');
            await expect(modal.locator('input[name="payment_date"]')).toHaveValue('');
            await take('split-base-and-two-extras-before-save', modal.locator('.modal-content'));
            const pendingPayment = page.waitForResponse(response => new URL(response.url()).pathname === '/api/payment/add' && response.request().method() === 'POST');
            await modal.getByRole('button', {name: 'Crea', exact: true}).click();
            const paymentResponse = await pendingPayment; expect(paymentResponse.status()).toBe(201); const created = await paymentResponse.json(); paymentId = created.payment_id;
            expect(paymentId).toBeTruthy(); expect(number(created.amount)).toBe(50); expect(created.paid).toBe(false); expect(created.invoice).toBeNull();
            expect(created.meta_payment_categories).toEqual([{payment_category_id: categoryId, amount: 10, subject: 0}, {payment_category_id: input.payment_category_id, amount: 5, subject: 0}]);
            await closeDrawer(); await page.reload(); expect(number((await savedPayment()).amount)).toBe(50); await take('split-total-fifty-after-reload');
            modal = await editPayment(); expect(number(await modal.locator('input[name="amount"]').inputValue())).toBe(35);
            expect(number(await modal.locator('input[name="amount_0"]').inputValue())).toBe(10); expect(number(await modal.locator('input[name="amount_1"]').inputValue())).toBe(5);
            await take('split-reopened-base-thirty-five', modal.locator('.modal-content'));
            await removeExtra(modal, 1); await expect(modal.locator('input[name="amount_1"]')).toHaveCount(0);
            await take('split-second-extra-removed-before-save', modal.locator('.modal-content')); await savePayment(modal);
            const reduced = await savedPayment(); expect(number(reduced.amount)).toBe(45); expect(reduced.meta_payment_categories).toEqual([{payment_category_id: categoryId, amount: 10, subject: 0}]);
            await take('split-total-forty-five-after-reload');
            modal = await editPayment(); await modal.locator('input[name="amount"]').fill('99,00');
            await modal.getByRole('button', {name: 'Annulla', exact: true}).click(); await page.reload();
            expect(await savedPayment()).toEqual(reduced); await take('split-cancel-preserves-saved-payment');
            modal = await editPayment(); await removeExtra(modal, 0); await expect(modal.locator('input[name="amount_0"]')).toHaveCount(0);
            await take('split-all-extras-removed-before-save', modal.locator('.modal-content')); await savePayment(modal);
            const single = await savedPayment(); expect(number(single.amount)).toBe(35); expect(single.meta_payment_categories).toEqual([]); expect(single.paid).toBe(false); expect(single.invoice).toBeNull();
            await take('split-single-base-after-reload');

            const reader = await actor('reader'); await reader.open('Pagamenti', '/#/payment/list');
            const readerRow = reader.page.locator('[data-row]').filter({hasText: 'Ripartizione laboratorio manuale'}); await expect(readerRow).toBeVisible();
            await expect(readerRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"]),button[data-original-title="Modifica"]')).toBeDisabled(); await expect(reader.page.getByRole('button', {name: 'Pagamento', exact: true})).toHaveCount(0);
            expect((await reader.api('payment/add', {method: 'POST', data: {amount: 1}})).status()).toBe(403);
            expect((await reader.api(`payment/${paymentId}/update`, {method: 'PATCH', data: {amount: 1, meta_payment_categories: []}})).status()).toBe(403);
            expect((await reader.api(`payment/category/${categoryId}/update`, {method: 'PATCH', data: {tax_deductible: true}})).status()).toBe(403);
            expect(await savedPayment()).toEqual(single); expect((await savedCategory()).tax_deductible).toBe(false);
            await reader.page.goto(input.origin + '/#/payment/category/list');
            await reader.page.getByRole('textbox', {name: 'Cerca nella tabella', exact: true}).fill('Laboratorio detraibile');
            const readCategory = reader.page.locator('[data-row]').filter({has: reader.page.locator('#action-col-' + categoryId)});
            await expect(readCategory).toBeVisible(); await expect(readCategory.locator('button:is([title="Modifica"],[data-original-title="Modifica"]),button[data-original-title="Modifica"]')).toBeDisabled();
            const position = spec.checkpoints.findIndex(point => point.id === 'classification-reader-readonly');
            await capture(reader.page, position + 1, 'classification-reader-readonly', readCategory);
        } catch (cause) {error = cause; throw cause;} finally {
            const cleanupFailures = [];
            if (paymentId) try {expect((await api(`payment/${paymentId}/delete`, {method: 'DELETE'})).status()).toBe(200);} catch (cause) {cleanupFailures.push('owned payment');}
            if (categoryId) try {expect((await api(`payment/category/${categoryId}/delete`, {method: 'DELETE'})).status()).toBe(200);} catch (cause) {cleanupFailures.push('owned category soft deletion');}
            if (cleanupFailures.length) {report.cleanup_failures = cleanupFailures; if (!error) throw new Error('Classification cleanup failed');}
        }
        // Seeded rows share one frozen timestamp, so compare record sets, not row order.
        const records = rows => (Array.isArray(rows) ? rows.map(row => JSON.stringify(row)) : Object.entries(rows).map(entry => JSON.stringify(entry))).sort();
        expect(records(await payments())).toEqual(records(baselinePayments));
        expect(records((await categories()).filter(item => item.payment_category_id !== categoryId))).toEqual(records(baselineCategories));
        expect((await savedCategory()).deleted).toBe(true);
        report[spec.outcome.field] = {tax_enabled_after_create: true, tax_checked_after_reopen: true, unrelated_rename_preserves_flag: true,
            tax_disabled_after_reopen: true, total_with_two_extras: 50, base_after_reopen: 35, total_after_one_removed: 45,
            cancelled_edit_preserves_payment: true, total_after_all_removed: 35, reader_write_denials: 3,
            no_payment_collected: true, no_receipt_generated: true, baseline_payments_preserved: true,
            baseline_categories_preserved: true, owned_payment_removed: true, owned_category_soft_deleted: true};
        expect(report[spec.outcome.field]).toEqual(spec.outcome.expected);
        report.checks = ['Actual UI tax flag enabled, reopened, renamed without loss, disabled and reopened',
            'Actual UI base amount plus additional categories: 35+10+5=50; removal yields 45 then 35; cancel preserves persisted state',
            'Reader write denials preserve state; exact cleanup of owned runtime rows; no fiscal eligibility, 730 output or receipt rendering proof'];
    }});
