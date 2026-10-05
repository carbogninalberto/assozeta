// Configure real fee plans, select one in the reused wizard, inspect its actual payment.
import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {memberAuthoredWorkflows} from '../../../../docs/manuale/member-authored-workflows.mjs';
import {createMember} from './member-create-flow.mjs';
import {reloadOrganizationProfile} from './organization-access-sources.mjs';
const id = 'members-fee-plan-selection', spec = memberAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async scenarioContext => {
        const {page, api, open, actor, context, input, capture, report} = scenarioContext;
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const json = async route => {const response = await api(route); expect(response.status()).toBe(200); return response.json();};
        const profile = async () => (await json('profile/info')).user_data.sport_association;
        const original = structuredClone(await profile());
        expect(original.multiple_subscription_fee).toBe(false); expect(original.enable_quotes_management).toBe(true);
        const originalMembers = Object.values((await json('subscription/list?pagination[perpage]=100')).data);
        const originalPayments = Object.values((await json('payment/list?pagination[perpage]=100')).data);
        const take = async checkpoint => {
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            const n = spec.checkpoints.findIndex(item => item.id === checkpoint) + 1; await capture(page, n, checkpoint);
        };
        const facts = {}, proof = (key, value) => {expect(value, key).toEqual(spec.outcome.expected[key]); facts[key] = value;};
        let configChanged = false, created;
        try {
            await open('Organizzazione', '/#/members/subscription/template');
            await page.locator('.nav-link').filter({hasText: /^\s*Quote\s*$/}).click();
            const quotes = page.locator('#quotes_subscription_module');
            const multi = quotes.locator('.col-md-12').filter({has: page.getByText('Quote multiple', {exact: true})})
                .locator('input[type="checkbox"]').first();
            await setCheckbox(multi, true);
            // Require the empty fixture configuration; never remove existing plans implicitly.
            const names = quotes.getByPlaceholder('Nome quota');
            expect(await names.count()).toBe(0);
            for (const [name, amount] of [['Ordinaria', '25,00'], ['Sostenitore', '45,00']]) {
                await quotes.getByRole('button', {name: 'Aggiungi quota', exact: true}).click();
                const index = await names.count() - 1; await names.nth(index).fill(name);
                await quotes.locator('input[name^="subscription_fee_"]').nth(index).fill(amount);
                await quotes.locator('input[name^="subscription_fee_"]').nth(index).press('Tab');
            }
            await take('multiple-fees-module-before-save');
            const saving = page.waitForResponse(response => new URL(response.url()).pathname === '/api/profile/update/subscription/template'
                && response.request().method() === 'PATCH');
            configChanged = true;
            await page.locator('#bkn_form_account_update_submit').click();
            proof('module_save_real', (await saving).status() === 200);
            const saved = await profile(); expect(saved.multiple_subscription_fee).toBe(true);
            proof('configured_plans', saved.subscription_fee_plans.length);
            const ordinary = saved.subscription_fee_plans.find(plan => plan.name === 'Ordinaria');
            const selected = saved.subscription_fee_plans.find(plan => plan.name === 'Sostenitore');
            expect(Number(ordinary.subscription_fee)).toBe(25); expect(Number(selected.subscription_fee)).toBe(45);
            await reloadOrganizationProfile({page, api, context, expect});
            await page.locator('.nav-link').filter({hasText: /^\s*Quote\s*$/}).click();
            await quotes.getByRole('button', {name: 'Mostra quote', exact: true}).click();
            await expect(names).toHaveCount(2); await expect(names.nth(1)).toHaveValue('Sostenitore');
            proof('plans_persist_after_reload', JSON.stringify((await profile()).subscription_fee_plans) === JSON.stringify(saved.subscription_fee_plans));
            await take('multiple-fees-module-persisted');
            await createMember({...scenarioContext, capture: async (_page, number) => {
                const checkpoint = {2: 'multiple-fees-selected-in-wizard', 7: 'multiple-fees-registration-summary',
                    8: 'multiple-fees-created-registration'}[number];
                if (checkpoint) await take(checkpoint);
            }}, {expectedAmount: 45, beforeProfileContinue: async ({active}) => {
                const field = active().locator('.svelte-select').filter({has: page.locator('input[type="hidden"][name="plan_id"]')});
                await expect(field).toHaveCount(1); await field.locator('input:not([type="hidden"])').click();
                await field.locator('.list-item').filter({hasText: /^\s*Sostenitore \(€\s*45,00\)\s*$/}).click();
                const actual = JSON.parse(await field.locator('input[type="hidden"][name="plan_id"]').inputValue());
                expect(actual.value).toBe(selected.id);
            }});
            created = Object.values((await json('subscription/list?query[generalSearch]=Marta')).data)[0];
            proof('registration_created', created.associate.first_name === 'Marta' && created.user.user_id === input.user_id);
            const persisted = (await json(`subscription/${created.subscription_id}/info`)).data.info;
            proof('selected_plan_persisted_in_subscription', persisted.meta?.plan_id === selected.id);
            proof('payment_amount', Number(created.payment.amount)); proof('payment_unpaid', created.payment.paid === false);
            await open('Pagamenti', '/#/payment/list');
            const row = page.locator('[data-row]').filter({hasText: 'Marta Neri'}); await expect(row).toHaveCount(1);
            await expect(row).toContainText('45,00'); await expect(row).toContainText('In attesa');
            await take('multiple-fees-unpaid-payment');
            const reader = await actor('reader');
            const deniedCreate = await reader.api('subscription/add', {method: 'POST', data: {}});
            const deniedModule = await reader.api('profile/update/subscription/template', {method: 'PATCH', data: {sport_association: saved}});
            proof('reader_create_denied', deniedCreate.status() === 403);
            proof('reader_module_update_denied', deniedModule.status() === 403);
            expect(await profile()).toEqual(saved);
            const currentMembers = Object.values((await json('subscription/list?pagination[perpage]=100')).data)
                .filter(row => row.subscription_id !== created.subscription_id);
            expect(currentMembers).toEqual(originalMembers);
            const currentPayments = Object.values((await json('payment/list?pagination[perpage]=100')).data)
                .filter(row => row.payment_id !== created.payment.payment_id);
            expect(currentPayments).toEqual(originalPayments);
        } finally {
            if (configChanged) {
                const restored = await api('profile/update/subscription/template', {method: 'PATCH', data: {sport_association: original}});
                expect(restored.status()).toBe(200);
                const current = await profile();
                for (const key of Object.keys(original).filter(key => !['regulation', 'demand'].includes(key)))
                    expect(current[key], key).toEqual(original[key]);
                for (const key of ['regulation', 'demand']) expect(current[key]?.trim(), key).toBe(original[key]?.trim());
                proof('original_configuration_restored', true);
            }
            if (created) report.private_cleanup = {operation: 'created-member-owned-fixture-reset',
                status: 'awaiting_runner_fixture_reset', subscription: created.subscription_id,
                persona: created.associate.associate_id, payment: created.payment.payment_id};
        }
        proof('fixture_created_records_cleanup_delegated', Boolean(created && report.private_cleanup.status === 'awaiting_runner_fixture_reset'));
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.checks = ['actual module UI Save and plan readback', 'real creation wizard selects a nondefault plan',
            'subscription records selected plan ID and its payment has the configured amount and remains unpaid', 'reader writes denied',
            'original module restored; runner resets only created fixture records'];
    }});
