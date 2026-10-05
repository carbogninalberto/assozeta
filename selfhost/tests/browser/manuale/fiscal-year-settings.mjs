// Real settings changes and read-back; no publishing, tax advice or migrations.
import {scenario, expect} from './scenario.mjs';
import {fiscalSettingsAuthoredWorkflows} from '../../../../docs/manuale/fiscal-settings-authored-workflows.mjs';
const id = 'fiscal-year-settings', spec = fiscalSettingsAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async route => {const response = await api(route); expect(response.status()).toBe(200); return response.json();};
        const settings = async () => (await read('profile/settings')).settings;
        const records = async () => ({members: (await read('subscription/list?pagination[perpage]=100')).data,
            payments: (await read('payment/list?pagination[perpage]=100')).data});
        const original = await settings(), baseline = await records();
        expect(Number(original.balance_sheet_year)).toBe(1);
        const facts = {}, proof = (key, value) => {expect(value, key).toEqual(spec.outcome.expected[key]); facts[key] = value;};
        const take = async checkpoint => {
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            const position = spec.checkpoints.findIndex(point => point.id === checkpoint);
            expect(position).toBeGreaterThanOrEqual(0); await capture(page, position + 1, checkpoint);
        };
        const goSettings = async () => {
            await open('Impostazioni', '/#/profile'); await page.getByText('Generali', {exact: true}).click();
            await expect(page.locator('#balance-sheet')).toBeVisible();
            await page.locator('#balance-sheet').scrollIntoViewIfNeeded();
        };
        const changedKeys = new Set(['balance_sheet_year', 'balance_sheet_start_month', 'balance_sheet_start_day', 'current_year']);
        let changed = false;
        try {
            await goSettings(); await take('fiscal-settings-original-and-options');
            await expect(page.locator('#balance-sheet option')).toHaveText([
                'Anno solare (Gennaio-Dicembre)', 'Anno Sportivo 1 (Settembre-Agosto)',
                'Anno Sportivo 2 (Giugno-Maggio)', 'Altro Periodo (personalizzato)']);
            // Each selection changes the preceding period, so the real save
            // control is enabled even when the fixture begins with solar year.
            for (const [value, month, day, name] of [['2', 9, 1, 'september'], ['3', 6, 1, 'june'],
                ['1', 1, 1, 'solar'], ['4', 7, 15, 'custom']]) {
                await goSettings();
                await page.locator('#balance-sheet').selectOption(value);
                if (value === '4') {
                    await page.locator('#balance-sheet-start-month').selectOption(String(month));
                    await page.locator('#balance-sheet-start-day').selectOption(String(day));
                }
                await take(name + '-fiscal-settings-before-save');
                const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/profile/settings'
                    && response.request().method() === 'POST');
                changed = true; await page.locator('#bkn_form_password_update_submit').click();
                expect((await saved).status()).toBe(200); await page.reload();
                await page.getByText('Generali', {exact: true}).click();
                await expect(page.locator('#balance-sheet')).toHaveValue(value);
                const reopened = await settings();
                proof(name + '_period_saved_and_reopened', Number(reopened.balance_sheet_year) === Number(value)
                    && reopened.balance_sheet_start_month === month && reopened.balance_sheet_start_day === day);
                proof(name + '_other_settings_preserved', Object.keys(original).filter(key => !changedKeys.has(key))
                    .every(key => JSON.stringify(original[key]) === JSON.stringify(reopened[key])));
                const sidebar = page.locator('#bkn_aside'), balanceLink = sidebar.getByText('Bilancio', {exact: true});
                if (!await balanceLink.isVisible()) await sidebar.getByText('Gestione', {exact: true}).first().click();
                const [balanceLoaded] = await Promise.all([
                    page.waitForResponse(response => new URL(response.url()).pathname === '/api/balance-sheet'
                        && response.request().method() === 'GET', {timeout: 60000}),
                    balanceLink.click(),
                ]);
                expect(balanceLoaded.status()).toBe(200);
                await expect(page).toHaveURL(/\/#\/balance-sheet\/list$/);
                const data = (await read('balance-sheet?currentDate=' + input.reference_date)).data;
                const [year, referenceMonth, referenceDay] = input.reference_date.split('-').map(Number);
                const startYear = referenceMonth < month || (referenceMonth === month && referenceDay < day) ? year - 1 : year;
                proof(name + '_backend_period_matches_settings', data.balance_sheet.draft === true
                    && data.balance_sheet.year === startYear
                    && data.available_years.some(item => item.year === startYear
                        && item.start_date.startsWith(`${startYear}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`)));
                await expect(page.locator('label').filter({hasText: 'Periodo anno sociale dal'})
                    .getByText(`${day}/${month}/${startYear}`, {exact: true})).toBeVisible();
                await take(name + '-balance-period-after-reload');
            }
            const reader = await actor('reader');
            const denied = await reader.api('profile/settings', {method: 'POST', data: original});
            proof('reader_fiscal_write_denied', denied.status() === 403);
            proof('reader_denial_preserved_settings', Number((await settings()).balance_sheet_year) === 4);
            proof('members_and_payments_preserved', JSON.stringify(await records()) === JSON.stringify(baseline));
        } finally {
            if (changed) expect((await api('profile/settings', {method: 'POST', data: original})).status()).toBe(200);
            expect(await settings()).toEqual(original);
            proof('original_settings_restored', true);
        }
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.private_cleanup = {operation: 'owned-disposable-balance-views', status: 'awaiting_runner_fixture_reset',
            reason: 'The real GET prepares fiscal-year drafts; seed_manuale resets only the owned association afterward.'};
        report.external_gaps = [{operation: 'approved-prior-years-receipt-renumbering-and-tax-policy',
            status: 'needs_external_verification', reason: 'This procedure changes preferences and reads draft periods only.'}];
        report.checks = ['actual four fiscal choices including a custom day/month saved through the UI',
            'settings reload and real draft period agree; separate subscription settings unchanged',
            'reader write rejected; member/payment records preserved and original preferences restored'];
    }});
