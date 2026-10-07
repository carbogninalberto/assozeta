// Real settings and new-registration payment, with association-scoped cleanup.
import {scenario, expect} from './scenario.mjs';
import {createMember} from './member-create-flow.mjs';
import {organizationBasicsAuthoredWorkflows} from '../../../../docs/manuale/organization-basics-authored-workflows.mjs';
const id = 'registration-default-category', spec = organizationBasicsAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources, actions: async flow => {
    const {page, api, open, actor, input, capture, report} = flow;
    expect(input.fixture_version).toBe(8); expect(input.fixture_profile || 'baseline').toBe('baseline');
    const read = async route => {const res = await api(route); expect(res.status()).toBe(200); return res.json();};
    const settings = async () => (await read('profile/settings')).settings;
    const members = async () => Object.values((await read('subscription/list?pagination[perpage]=100')).data).sort((a,b)=>a.subscription_id.localeCompare(b.subscription_id));
    const payments = async () => Object.values((await read('payment/list?pagination[perpage]=100')).data).sort((a,b)=>a.payment_id.localeCompare(b.payment_id));
    const original = await settings(), beforeMembers = await members(), beforePayments = await payments();
    const categories = (await read('payment/category/list')).data;
    const category = categories.find(item => item.payment_category_id === input.payment_category_id);
    expect(category).toBeTruthy(); expect(category.expense).toBe(false);
    const facts = {}, proof = (key, value) => {expect(value, key).toEqual(spec.outcome.expected[key]); facts[key] = value;};
    const take = async checkpoint => {await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
        await capture(page, spec.checkpoints.findIndex(point => point.id === checkpoint) + 1, checkpoint);};
    let owned, changed = false;
    try {
        await open('Impostazioni', '/#/profile'); await page.getByText('Generali', {exact: true}).click();
        const select = page.locator('.svelte-select').filter({has: page.locator('input[type="hidden"][name="default_payment_category"]')});
        await select.scrollIntoViewIfNeeded(); await take('default-registration-category-before-edit');
        await select.locator('input:not([type="hidden"])').click();
        await page.locator('.list-item').filter({hasText: category.name}).click();
        await take('default-registration-category-before-save');
        const savedResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/profile/settings' && r.request().method() === 'POST');
        changed = true; await page.locator('#bkn_form_password_update_submit').click(); expect((await savedResponse).status()).toBe(200);
        await page.reload(); const saved = await settings(); proof('default_category_saved_and_reopened', saved.default_payment_category === category.payment_category_id);
        proof('other_settings_preserved', Object.keys(original).filter(key => key !== 'default_payment_category').every(key => JSON.stringify(saved[key]) === JSON.stringify(original[key])));
        proof('existing_payments_preserved_before_signup', JSON.stringify(await payments()) === JSON.stringify(beforePayments));
        await select.scrollIntoViewIfNeeded(); await take('default-registration-category-persists-after-reload');
        await createMember({...flow, capture: async (_page, number) => {if (number === 7) await take('registration-using-default-category-summary');}});
        owned = (await members()).find(row => !beforeMembers.some(old => old.subscription_id === row.subscription_id)
            && row.associate.first_name === 'Marta' && row.associate.last_name === 'Neri' && row.associate.email === 'marta@example.test'
            && row.user.user_id === input.user_id);
        expect(owned).toBeTruthy(); proof('new_registration_has_unpaid_quota', Number(owned.payment.amount) === 25 && owned.payment.paid === false);
        const payment = (await payments()).find(row => row.payment_id === owned.payment.payment_id);
        proof('new_quota_uses_saved_default_category', payment.payment_category === category.payment_category_id
            && payment.payment_category_name === category.name);
        await open('Pagamenti', '/#/payment/list'); await expect(page.locator('[data-row]').filter({hasText: 'Marta Neri'})).toContainText('25,00');
        await take('registration-payment-with-saved-default-category');
        const reader = await actor('reader'); const denied = await reader.api('profile/settings', {method: 'POST', data: {...saved, default_payment_category: null}});
        proof('reader_settings_write_denied', denied.status() === 403); expect(await settings()).toEqual(saved);
        proof('baseline_other_records_preserved', JSON.stringify((await members()).filter(row => row.subscription_id !== owned.subscription_id)) === JSON.stringify(beforeMembers)
            && JSON.stringify((await payments()).filter(row => row.payment_id !== owned.payment.payment_id)) === JSON.stringify(beforePayments));
    } finally {
        if (!owned) owned = (await members()).find(row => !beforeMembers.some(old => old.subscription_id === row.subscription_id)
            && row.associate.first_name === 'Marta' && row.associate.last_name === 'Neri' && row.associate.email === 'marta@example.test'
            && row.user.user_id === input.user_id);
        if (owned) {
            expect(owned.payment.paid).toBe(false);
            expect((await api(`subscription/${owned.subscription_id}/delete`, {method: 'POST'})).status()).toBe(200);
            report.private_cleanup = {operation: 'remaining-owned-person-and-legacy-signature-fixture-reset', status: 'awaiting_runner_fixture_reset',
                persona: owned.associate.associate_id};
        }
        if (changed) expect((await api('profile/settings', {method: 'POST', data: original})).status()).toBe(200);
        expect(await settings()).toEqual(original); expect(await members()).toEqual(beforeMembers); expect(await payments()).toEqual(beforePayments);
        proof('settings_and_created_registration_payment_restored', true);
    }
    expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
    report.external_gaps = [{operation: 'course-default-category-and-provider-payment', status: 'needs_external_verification',
        reason: 'This path applies the registration default only. Course-default application, collection, tax advice and provider checkout require their own procedures.'}];
    report.checks = ['actual default-category UI save and readback', 'real wizard quota uses the saved default; old payments remain unchanged',
        'reader settings mutation denied; only created subscription/unpaid payment removed and original settings restored'];
}});
