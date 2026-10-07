import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {stripeLocalAuthoredWorkflows} from '../../../../docs/manuale/stripe-local-authored-workflows.mjs';

const id = 'stripe-local-settings-filters', spec = stripeLocalAuthoredWorkflows[id];
const sorted = rows => [...rows].sort((a, b) => a.payment_id.localeCompare(b.payment_id));
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8); expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async (route, request = api) => {
            const res = await request(route); expect(res.status()).toBe(200); return res.json();
        };
        const settings = async () => (await read('profile/settings')).settings;
        const payments = async () => sorted(Object.values((await read('payment/list?pagination[perpage]=100')).data));
        const integration = await read('instance/admin/integrations/stripe');
        expect(integration.enabled).toBe(false);
        const original = await settings(), baseline = await payments();
        expect(original.online_payments).toBe(false); expect(baseline).toHaveLength(3);
        expect(baseline.every(row => row.type !== 'stripe')).toBe(true);
        let providerActions = 0, sdkAssetRequests = 0;
        const watch = current => current.on('request', request => {
            const url = new URL(request.url());
            // The application shell loads the public SDK on every navigation.
            // Record that asset separately from credentials/probes/transactions.
            if (url.hostname === 'js.stripe.com') sdkAssetRequests++;
            if (['api.stripe.com', 'checkout.stripe.com'].includes(url.hostname)
                || /\/api\/stripe\//.test(url.pathname)
                || /\/integrations\/stripe\/test$/.test(url.pathname)) providerActions++;
        });
        watch(page);
        const take = async (checkpoint, current = page) => {
            const index = spec.checkpoints.findIndex(item => item.id === checkpoint);
            expect(index).toBeGreaterThanOrEqual(0);
            await expect(current.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await capture(current, index + 1, checkpoint);
        };
        const control = current => current.locator('.form-group').filter({has:
            current.locator('label.col-form-label').filter({hasText: /^Pagamenti Online$/})}).locator('input[type="checkbox"]');
        const save = async () => {
            const response = page.waitForResponse(res => new URL(res.url()).pathname === '/api/profile/settings'
                && res.request().method() === 'POST');
            await page.locator('#bkn_form_password_update_submit').click();
            expect((await response).status()).toBe(200);
            await expect(page.locator('#bkn_form_password_update_submit')).toBeDisabled();
        };
        const facts = {baseline_payment_count: baseline.length};
        try {
            await open('Impostazioni', '/#/profile'); await page.getByText('Generali', {exact: true}).click();
            await expect(control(page)).not.toBeChecked(); await control(page).scrollIntoViewIfNeeded();
            await take('online-setting-disabled');
            await setCheckbox(control(page), true); await take('online-setting-enabled-before-save');
            await save(); await page.reload(); await expect(control(page)).toBeChecked();
            expect((await settings()).online_payments).toBe(true);
            await control(page).scrollIntoViewIfNeeded(); await take('online-setting-enabled-after-reload');
            facts.enabled_setting_persisted = true;
            await setCheckbox(control(page), false); await take('online-setting-disabled-before-save');
            await save(); await page.reload(); await expect(control(page)).not.toBeChecked();
            expect((await settings()).online_payments).toBe(false);
            await control(page).scrollIntoViewIfNeeded(); await take('online-setting-disabled-after-reload');
            facts.disabled_setting_persisted = true;
            const reader = await actor('reader'); watch(reader.page);
            await reader.open('Impostazioni', '/#/profile'); await reader.page.getByText('Generali', {exact: true}).click();
            await expect(control(reader.page)).toBeDisabled();
            await expect(reader.page.locator('#bkn_form_password_update_submit')).toBeDisabled();
            await control(reader.page).scrollIntoViewIfNeeded(); await take('online-setting-reader-disabled', reader.page);
            facts.reader_toggle_disabled = true;
            facts.reader_setting_write_status = (await reader.api('profile/settings', {method: 'POST',
                data: {...original, online_payments: true}})).status();
            expect(facts.reader_setting_write_status).toBe(403);
            expect((await settings()).online_payments).toBe(false); facts.reader_denial_preserved_setting = true;
            await open('Pagamenti', '/#/payment/list'); await expect(page.locator('[data-row]')).toHaveCount(3);
            await take('stripe-filter-baseline');
            const filter = (current, label) => current.getByRole('textbox', {name: label, exact: true});
            const choose = async (current, label, text, queries) => {
                await filter(current, label).click();
                const response = current.waitForResponse(res => {
                    const url = new URL(res.url());
                    return url.pathname === '/api/payment/list' && queries.every(([key, value]) =>
                        (url.searchParams.get('query[' + key + ']') || '') === value);
                });
                await current.locator('.list-item:visible').getByText(text, {exact: true}).click();
                const result = await response; expect(result.status()).toBe(200); return result.json();
            };
            await filter(page, 'Metodo pagamento').click();
            await expect(page.locator('.list-item:visible').getByText('Stripe', {exact: true})).toBeVisible();
            await take('stripe-filter-options'); await filter(page, 'Metodo pagamento').click();
            let result = await choose(page, 'Metodo pagamento', 'Stripe', [['type', 'stripe']]);
            expect(Object.values(result.data)).toHaveLength(0); await expect(page.locator('[data-row]')).toHaveCount(0);
            await expect(filter(page, 'Metodo pagamento').locator('xpath=ancestor::div[contains(@class,"filter-select-control")]')).toContainText('Stripe'); await take('stripe-filter-empty-result');
            facts.stripe_filter_empty = true;
            result = await choose(page, 'Stato pagamento', 'Pagato', [['type', 'stripe'], ['paid', 'true']]);
            expect(Object.values(result.data)).toHaveLength(0); await expect(page.locator('[data-row]')).toHaveCount(0);
            await take('stripe-filter-paid-empty-result'); facts.stripe_paid_filter_empty = true;
            result = await choose(page, 'Stato pagamento', 'In attesa', [['type', 'stripe'], ['paid', 'false']]);
            expect(Object.values(result.data)).toHaveLength(0); await expect(page.locator('[data-row]')).toHaveCount(0);
            await take('stripe-filter-pending-empty-result'); facts.stripe_pending_filter_empty = true;
            await choose(page, 'Stato pagamento', 'Stato', [['type', 'stripe'], ['paid', '']]);
            result = await choose(page, 'Metodo pagamento', 'Metodo', [['type', ''], ['paid', '']]);
            expect(sorted(Object.values(result.data))).toEqual(baseline);
            await expect(page.locator('[data-row]')).toHaveCount(3); await take('stripe-filter-reset');
            facts.filter_reset_restored_rows = true;
            await reader.open('Pagamenti', '/#/payment/list');
            await expect(reader.page.locator('[data-row]')).toHaveCount(3);
            result = await choose(reader.page, 'Metodo pagamento', 'Stripe', [['type', 'stripe']]);
            expect(Object.values(result.data)).toHaveLength(0); await expect(reader.page.locator('[data-row]')).toHaveCount(0);
            await take('stripe-filter-reader-empty-result', reader.page); facts.reader_stripe_filter_empty = true;
            expect(await payments()).toEqual(baseline); facts.payments_unchanged = true;
            expect(await read('instance/admin/integrations/stripe')).toEqual(integration);
            facts.integration_disabled_before_and_after = true;
        } finally {
            const current = await settings();
            if (current.online_payments !== original.online_payments)
                expect((await api('profile/settings', {method: 'POST', data: {...current,
                    online_payments: original.online_payments}})).status()).toBe(200);
        }
        expect(await settings()).toEqual(original); facts.settings_restored = true;
        expect(providerActions).toBe(0); facts.provider_actions = providerActions;
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.integration_evidence = {provider: 'stripe', external_transaction: 'not-executed',
            public_sdk_asset_requests: sdkAssetRequests,
            scope: 'local option persistence and empty-result filtering', synthetic_stripe_payments: false};
        report.checks.push('real settings on/off persistence, role denial, method/state filters and preserved payments');
    },
});
