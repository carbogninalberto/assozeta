// The run owner executes this actual UI/backend workflow once the whole batch is ready.
import {scenario, expect} from './scenario.mjs';
import {focusRegion, tableFocus} from './focus.mjs';
import {accountingBalanceSources} from './accounting-balance-sources.mjs';

await scenario({id: 'accounting-balance-manage', prefix: 'images/contabilita/bilancio',
    sources: accountingBalanceSources, actions: async ({page, api, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const json = async route => {
            const response = await api(route);
            expect(response.status()).toBe(200);
            return response.json();
        };
        const accounts = async () => (await json('balance-sheet/accounts/list')).data;
        const transfers = async () => (await json('balance-sheet/accounts-transfer/list')).data;
        const balance = async () => (await json('balance-sheet?currentDate=' + input.reference_date)).data;
        const paymentState = async () => Object.values((await json('payment/list?pagination[perpage]=100')).data)
            .filter(row => input.payment_ids.includes(row.payment_id)).map(row =>
                Object.fromEntries(['payment_id', 'amount', 'paid', 'expense', 'payment_date', 'creation_date',
                    'custom_accounts', 'payment_category', 'archived', 'deleted'].map(key => [key, row[key]])))
            .sort((a, b) => a.payment_id.localeCompare(b.payment_id));
        const baselinePayments = await paymentState();
        expect(baselinePayments).toHaveLength(3);
        const take = async (number, checkpoint, locator) => {
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            await capture(page, number, checkpoint, locator);
        };
        const navigate = async (label, route) => {
            const sidebar = page.locator('#bkn_aside');
            const item = sidebar.getByText(label, {exact: true});
            if (!await item.isVisible()) await sidebar.getByText('Gestione', {exact: true}).first().click();
            await expect(item).toBeVisible();
            await item.click();
            await expect(page).toHaveURL(new RegExp('/#' + route + '$'));
        };
        const saveRequest = (pathname, method) => page.waitForResponse(response =>
            new URL(response.url()).pathname === '/api/' + pathname && response.request().method() === method);
        const baseline = await accounts();
        expect(baseline).toHaveLength(1);
        const cash = baseline[0];
        expect(cash.custom_account_id).toBe(input.cash_account_id);
        expect(cash.name).toBe('Cassa Aurora');
        expect(Number(cash.initial_balance)).toBe(0);
        expect(Number(cash.current_balance)).toBe(50);
        expect(cash.deletable).toBe(false);
        expect(await transfers()).toEqual([]);

        await navigate('Conti Finanziari', '/accounting/list');
        await expect(page.getByRole('heading', {name: /^Conti finanziari/})).toBeVisible();
        await expect(page.locator('#action-col-' + cash.custom_account_id).locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
        await take(1, 'baseline-cash-account-and-current-balance', tableFocus(page, ['name', 'current_balance']));
        await page.locator('[data-target="#addAccountModal"]').click();
        const accountModal = page.locator('#addAccountModal');
        await expect(accountModal).toBeVisible();
        await expect(accountModal.locator('[name="account_type"] option')).toHaveText(['Cassa', 'Banca', 'Altro']);
        await accountModal.locator('[name="name"]').fill('Banca Aurora');
        await accountModal.locator('[name="account_type"]').selectOption('2');
        await accountModal.locator('[name="initial_balance"]').fill('100,00');
        await accountModal.locator('[name="account_code"]').fill('BANCA-AURORA');
        await take(2, 'new-bank-account-filled-before-save', accountModal.locator('.modal-content'));
        const created = saveRequest('balance-sheet/accounts/add', 'POST');
        await accountModal.getByRole('button', {name: 'Salva', exact: true}).click();
        const createdResponse = await created;
        expect(createdResponse.status()).toBe(200);
        const bank = await createdResponse.json();
        expect(Number(bank.initial_balance)).toBe(100);
        await expect(accountModal).not.toBeVisible();
        await page.reload();
        await expect(page.getByText('BANCA AURORA', {exact: true})).toBeVisible();
        const persistedBank = (await accounts()).find(row => row.custom_account_id === bank.custom_account_id);
        expect(persistedBank.account_type).toBe(2);
        expect(persistedBank.enabled).toBe(true);
        expect(persistedBank.account_code).toBe('BANCA-AURORA');
        expect(Number(persistedBank.current_balance)).toBe(100);
        await take(3, 'bank-account-created-and-persisted-after-reload', tableFocus(page, ['name', 'current_balance'],
            page.locator('[data-row]').filter({hasText: 'BANCA AURORA'})));
        await page.locator('#action-col-' + bank.custom_account_id).locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        const editModal = page.locator('#editAccountModal-' + bank.custom_account_id);
        await expect(editModal).toBeVisible();
        await expect(editModal.locator('[name="account_code"]')).toHaveValue('BANCA-AURORA');
        await editModal.locator('[name="initial_balance"]').fill('125,00');
        await take(4, 'account-edit-initial-balance-before-save', editModal.locator('.modal-content'));
        const edited = saveRequest('balance-sheet/accounts/' + bank.custom_account_id + '/update', 'PATCH');
        await editModal.getByRole('button', {name: 'Salva', exact: true}).click();
        expect((await edited).status()).toBe(200);
        await expect(editModal).not.toBeVisible();
        await page.reload();
        await expect(page.getByText('BANCA AURORA', {exact: true})).toBeVisible();
        const editedBank = (await accounts()).find(row => row.custom_account_id === bank.custom_account_id);
        expect(Number(editedBank.initial_balance)).toBe(125);
        expect(Number(editedBank.current_balance)).toBe(125);
        await take(5, 'edited-bank-account-persists-after-reload', tableFocus(page, ['current_balance', 'initial_balance'],
            page.locator('[data-row]').filter({hasText: 'BANCA AURORA'})));

        await navigate('Giroconti', '/accounting-transfer/list');
        await page.locator('[data-target="#addAccountModal"]').click();
        const transferModal = page.locator('#addAccountModal');
        await expect(transferModal).toBeVisible();
        await transferModal.locator('[name="date"]').fill(input.reference_date);
        await transferModal.locator('[name="custom_account_from"]').selectOption(cash.custom_account_id);
        await expect(transferModal.locator('[name="custom_account_to"] option[value="' + cash.custom_account_id + '"]')).toHaveCount(0);
        await transferModal.locator('[name="custom_account_to"]').selectOption(bank.custom_account_id);
        await transferModal.locator('[name="amount"]').fill('20,00');
        await take(6, 'transfer-filled-with-distinct-accounts', transferModal.locator('.modal-content'));
        const transferred = saveRequest('balance-sheet/accounts-transfer/add', 'POST');
        await transferModal.getByRole('button', {name: 'Salva', exact: true}).click();
        const transferResponse = await transferred;
        expect(transferResponse.status()).toBe(200);
        const transfer = await transferResponse.json();
        expect(Number(transfer.amount)).toBe(20);
        await expect(transferModal).not.toBeVisible();
        await page.reload();
        await expect(page.locator('#action-col-' + transfer.custom_account_transfer_id)).toBeVisible();
        const persistedTransfers = await transfers();
        expect(persistedTransfers).toHaveLength(1);
        expect(persistedTransfers[0].custom_account_from.custom_account_id).toBe(cash.custom_account_id);
        expect(persistedTransfers[0].custom_account_to.custom_account_id).toBe(bank.custom_account_id);
        await take(7, 'transfer-persists-after-reload', tableFocus(page, ['amount', 'date']));
        await navigate('Conti Finanziari', '/accounting/list');
        const movedAccounts = await accounts();
        const cashAfter = Number(movedAccounts.find(row => row.custom_account_id === cash.custom_account_id).current_balance);
        const bankAfter = Number(movedAccounts.find(row => row.custom_account_id === bank.custom_account_id).current_balance);
        expect(cashAfter).toBe(30);
        expect(bankAfter).toBe(145);
        expect(cashAfter + bankAfter).toBe(175);
        await expect(page.locator('#action-col-' + bank.custom_account_id).locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
        await take(8, 'cash-bank-balances-reflect-internal-transfer', tableFocus(page, ['name', 'current_balance']));

        await navigate('Bilancio', '/balance-sheet/list');
        await expect(page.getByRole('heading', {name: /^Rendiconto economico finanziario/})).toBeVisible();
        await expect(page.getByRole('button', {name: 'Pubblica', exact: true})).toBeEnabled();
        const beforeManual = await balance();
        expect(beforeManual.available_years).toHaveLength(6);
        expect(beforeManual.balance_sheet.draft).toBe(true);
        const automaticIncome = beforeManual.balance_sheet.data.incoming.generalIncome.reduce((sum, row) =>
            sum + Number(row.institutional || 0) + Number(row.commercial || 0), 0);
        expect(automaticIncome).toBe(50);
        expect(beforeManual.balance_sheet.data.cash).toBe(30);
        expect(beforeManual.balance_sheet.data.bank).toBe(145);
        expect(beforeManual.balance_sheet.data.total).toBe(175);
        const years = page.locator('.filter-select-control input[aria-label="Anno sociale"]');
        await years.click();
        await expect(page.locator('.filter-select-control .list-item:visible')).toHaveCount(6);
        await take(9, 'balance-current-year-six-available-years', focusRegion(years,
            page.locator('.filter-select-control .list-item:visible').first(),
            page.locator('.filter-select-control .list-item:visible').last()));
        await years.press('Escape');
        await page.getByRole('button', {name: 'Aggiungi riga', exact: true}).first().click();
        const manualRow = page.locator('.balance-sheet-table').first().locator('tr').filter({
            has: page.getByPlaceholder('Descrizione', {exact: true})});
        await manualRow.getByPlaceholder('Descrizione', {exact: true}).fill('Contributo dimostrativo direttivo');
        const numbers = manualRow.locator('input[inputmode="decimal"]');
        await expect(numbers).toHaveCount(2);
        // Regresses the actual NumericInput handler, including an intermediate value.
        await numbers.nth(0).fill('-');
        await numbers.nth(0).fill('15,00');
        await numbers.nth(1).fill('5,00');
        await expect(manualRow.locator('td').last()).toHaveText('20,00 €');
        await take(10, 'manual-balance-row-description-and-two-amounts', manualRow);
        const saved = saveRequest('balance-sheet', 'POST');
        await page.locator('.card-toolbar').getByRole('button', {name: 'Salva', exact: true}).click();
        const savedResponse = await saved;
        expect(savedResponse.status()).toBe(200);
        const savedData = (await savedResponse.json()).data.balance_sheet.data;
        const savedManual = savedData.incoming.generalIncome.find(row => row.description === 'Contributo dimostrativo direttivo');
        expect(savedManual.institutional).toBe(15);
        expect(savedManual.commercial).toBe(5);
        await page.reload();
        await expect(page.getByText('Contributo dimostrativo direttivo', {exact: true})).toBeVisible();
        const reloaded = await balance();
        const reloadedManual = reloaded.balance_sheet.data.incoming.generalIncome.find(row => row.id === savedManual.id);
        expect(reloadedManual.institutional).toBe(15);
        expect(reloadedManual.commercial).toBe(5);
        const incomeAfter = reloaded.balance_sheet.data.incoming.generalIncome.reduce((sum, row) =>
            sum + Number(row.institutional || 0) + Number(row.commercial || 0), 0);
        expect(incomeAfter).toBe(70);
        expect(reloaded.balance_sheet.data.total).toBe(175);
        const persistedManualRow = () => page.locator('.balance-sheet-table').first().locator('tr')
            .filter({hasText: 'Contributo dimostrativo direttivo'});
        const manualFocus = () => focusRegion(
            page.locator('.balance-sheet-table').first().locator('tr').first(), persistedManualRow());
        await take(11, 'saved-manual-row-persists-after-reload', manualFocus());
        await page.getByRole('button', {name: 'Pubblica', exact: true}).click();
        const confirmation = page.locator('.swal2-popup');
        await expect(confirmation).toContainText('Vuoi pubblicare il bilancio?');
        await expect(confirmation).toContainText('Puoi annullare la pubblicazione in qualsiasi momento.');
        await take(12, 'publication-confirmation-is-reversible', confirmation);
        const publishedRequest = saveRequest('balance-sheet', 'POST');
        await confirmation.getByRole('button', {name: 'Pubblica', exact: true}).click();
        expect((await publishedRequest).status()).toBe(200);
        await expect(confirmation).not.toBeVisible();
        await page.reload();
        await expect(page.getByRole('button', {name: 'Annulla pubblicazione', exact: true})).toBeVisible();
        await expect(page.getByRole('button', {name: 'Aggiungi riga', exact: true})).toHaveCount(0);
        const published = await balance();
        expect(published.balance_sheet.draft).toBe(false);
        expect(published.balance_sheet.data.incoming.generalIncome.find(row => row.id === savedManual.id)).toEqual(reloadedManual);
        await take(13, 'published-balance-persists-after-reload', page.locator('.card-toolbar'));
        await page.getByRole('button', {name: 'Annulla pubblicazione', exact: true}).click();
        await expect(confirmation).toContainText('Vuoi annullare la pubblicazione del bilancio?');
        await take(14, 'unpublish-confirmation', confirmation);
        const unpublishedRequest = saveRequest('balance-sheet', 'POST');
        await confirmation.getByRole('button', {name: 'Annulla pubblicazione', exact: true}).click();
        expect((await unpublishedRequest).status()).toBe(200);
        await expect(confirmation).not.toBeVisible();
        await page.reload();
        await expect(page.getByRole('button', {name: 'Pubblica', exact: true})).toBeVisible();
        await expect(page.getByRole('button', {name: 'Aggiungi riga', exact: true}).first()).toBeVisible();
        const unpublished = await balance();
        expect(unpublished.balance_sheet.draft).toBe(true);
        expect(unpublished.balance_sheet.data.incoming.generalIncome.find(row => row.id === savedManual.id)).toEqual(reloadedManual);
        await take(15, 'draft-and-manual-row-restored-after-unpublish-reload', manualFocus());

        const reader = await actor('reader');
        const deniedRead = await reader.api('balance-sheet?currentDate=' + input.reference_date);
        expect(deniedRead.status()).toBe(403);
        const deniedWrite = await reader.api('balance-sheet', {method: 'POST', data: {
            balance_sheet: {...unpublished.balance_sheet.data, draft: false, year: unpublished.balance_sheet.year}}});
        expect(deniedWrite.status()).toBe(403);
        for (const route of ['balance-sheet/accounts/list', 'balance-sheet/accounts-transfer/list'])
            expect((await reader.api(route)).status()).toBe(403);
        expect((await balance()).balance_sheet.draft).toBe(true);

        await navigate('Giroconti', '/accounting-transfer/list');
        await page.locator('#action-col-' + transfer.custom_account_transfer_id).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
        await expect(confirmation).toContainText('Vuoi eliminare il giroconto?');
        await take(16, 'transfer-delete-confirmation', confirmation);
        await confirmation.getByRole('button', {name: 'Annulla', exact: true}).click();
        await expect(confirmation).not.toBeVisible();
        expect(await transfers()).toEqual(persistedTransfers);
        const afterCancelledTransferDelete = await accounts();
        expect(Number(afterCancelledTransferDelete.find(row => row.custom_account_id === cash.custom_account_id).current_balance)).toBe(30);
        expect(Number(afterCancelledTransferDelete.find(row => row.custom_account_id === bank.custom_account_id).current_balance)).toBe(145);
        await page.locator('#action-col-' + transfer.custom_account_transfer_id).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
        await expect(confirmation).toContainText('Vuoi eliminare il giroconto?');
        const deletedTransfer = saveRequest('balance-sheet/accounts-transfer/' + transfer.custom_account_transfer_id + '/delete', 'DELETE');
        await confirmation.getByRole('button', {name: 'Elimina', exact: true}).click();
        expect((await deletedTransfer).status()).toBe(200);
        await expect(confirmation).not.toBeVisible();
        await page.reload();
        await expect(page.locator('#action-col-' + transfer.custom_account_transfer_id)).toHaveCount(0);
        expect(await transfers()).toEqual([]);
        await navigate('Conti Finanziari', '/accounting/list');
        const restored = await accounts();
        expect(Number(restored.find(row => row.custom_account_id === cash.custom_account_id).current_balance)).toBe(50);
        expect(Number(restored.find(row => row.custom_account_id === bank.custom_account_id).current_balance)).toBe(125);
        await expect(page.locator('[data-row]')).toHaveCount(2);
        await expect(page.locator('[data-row]').filter({hasText:'CASSA AURORA'}).locator('[data-field="current_balance"]')).toContainText('50,00');
        await take(17, 'deleted-transfer-absent-and-balances-restored', tableFocus(page, ['name', 'current_balance']));
        const bankDelete = page.locator('#action-col-' + bank.custom_account_id).locator('button:is([title="Elimina"],[data-original-title="Elimina"])');
        await expect(bankDelete).toBeEnabled();
        await bankDelete.click();
        await expect(confirmation).toContainText('Vuoi eliminare il conto?');
        const deletedBank = saveRequest('balance-sheet/accounts/' + bank.custom_account_id + '/delete', 'DELETE');
        await confirmation.getByRole('button', {name: 'Elimina', exact: true}).click();
        expect((await deletedBank).status()).toBe(200);
        await expect(confirmation).not.toBeVisible();
        await page.reload();
        await expect(page.getByText('BANCA AURORA', {exact: true})).toHaveCount(0);
        const afterCleanup = await accounts();
        expect(afterCleanup).toHaveLength(1);
        expect(Number(afterCleanup[0].current_balance)).toBe(50);
        expect(await paymentState()).toEqual(baselinePayments);
        await expect(page.locator('[data-row]')).toHaveCount(1);
        await expect(page.locator('[data-row]').first()).toContainText('CASSA AURORA');
        await take(18, 'unused-bank-account-deleted-baseline-restored', tableFocus(page, ['name', 'current_balance']));
        report.accounting_balance_manage = {baseline_cash: Number(cash.current_balance),
            bank_created_initial_balance: Number(bank.initial_balance), bank_saved_initial_balance: Number(editedBank.initial_balance),
            transfer_amount: Number(transfer.amount), cash_after_transfer: cashAfter, bank_after_transfer: bankAfter,
            liquid_total_after_transfer: reloaded.balance_sheet.data.total, automatic_income: automaticIncome,
            available_year_count: beforeManual.available_years.length, manual_row_description: reloadedManual.description,
            manual_institutional: reloadedManual.institutional, manual_commercial: reloadedManual.commercial,
            resulting_income: incomeAfter, manual_row_persisted_after_reload: true, published_persisted_after_reload: true,
            unpublish_persisted_after_reload: true, automatic_payments_unchanged: true, transfer_deleted_after_reload: true,
            bank_deleted_after_reload: true, baseline_cash_restored: Number(afterCleanup[0].current_balance),
            reader_read_status: deniedRead.status(), reader_write_status: deniedWrite.status(), export_exercised: false,
            account_type_options_visible: true,
            cancelled_transfer_delete_preserves_balances: true,
            invoices_exercised: false, suppliers_exercised: false, categories_exercised: false,
            fiscal_change_exercised: false, associated_member_visibility_exercised: false};
        report.external_gaps = [{operation: 'active-invoice-xml-and-sdi-delivery', status: 'needs_external_verification'},
            {operation: 'associated-member-published-balance-visibility', status: 'needs_external_verification'}];
        report.checks = ['actual UI creates and edits bank account; list API and reload establish persistence',
            'distinct-account transfer changes each balance but preserves total liquidity; deletion reverses it',
            'actual NumericInput events persist institutional 15 and commercial 5; invalid intermediate input is not guessed',
            'current-year automatic paid income remains 50; saved manual row raises rendered income to 70 without new payments',
            'publish and unpublish each persist after reload; unpermitted collaborator cannot read or publish',
            'bank account deleted after linked transfer removed; baseline cash and payments preserved'];
    }});
