import {scenario, expect} from './scenario.mjs';
import {memberProfileSources, openGiulia} from './member-profile-sources.mjs';

await scenario({id: 'members-profile-update', prefix: 'images/libro-soci/scheda', sources: memberProfileSources,
    actions: async ({page, api, open, actor, capture, report, input}) => {
        const drawer = await openGiulia({page, open, expect});
        await capture(page, 1, 'registration-profile-and-navigation-tabs', drawer);
        const form = drawer.locator('#subscription_form');
        await form.locator('[name="subscription_number"]').fill('42');
        await form.locator('[name="subscription_type"]').fill('Tessera Aurora');
        await capture(page, 2, 'edited-card-number-and-type-before-saving');
        const saved = page.waitForResponse(response =>
            new URL(response.url()).pathname === `/api/subscription/${input.subscription_ids[0]}/update`
            && response.request().method() === 'PATCH');
        await form.getByRole('button', {name: 'Salva', exact: true}).click();
        const response = await saved;
        expect(response.status()).toBe(200);
        expect(response.request().postDataJSON().subscription_number).toBe('42');
        expect(response.request().postDataJSON().subscription_type).toBe('Tessera Aurora');
        await expect(form.locator('[name="subscription_number"]')).toHaveValue('42');
        await page.reload();
        // Reload preserves this list route. Wait for its rows before opening
        // the profile again; the navigation helper must not toggle the active
        // sidebar section while its mount transition is still settling.
        await expect(page.locator('[data-row]').filter({hasText: 'Giulia Bianchi'})).toBeVisible();
        const reloaded = await openGiulia({page, open, expect});
        await expect(reloaded.locator('[name="subscription_number"]')).toHaveValue('42');
        await expect(reloaded.locator('[name="subscription_type"]')).toHaveValue('Tessera Aurora');
        await capture(page, 3, 'card-details-persist-after-profile-reload');
        const info = (await (await api(`subscription/${input.subscription_ids[0]}/info`)).json()).data.info;
        expect(info.subscription_number).toBe('42');
        expect(info.subscription_type).toBe('Tessera Aurora');
        expect(info.associate.first_name).toBe('Giulia');
        expect(info.status_flag).toBe(4);
        const reader = await actor('reader');
        const readOnly = await openGiulia({...reader, expect});
        await expect(readOnly.locator('#subscription_form').getByRole('button', {name: 'Salva', exact: true})).toHaveCount(0);
        const denied = await reader.api(`subscription/${input.subscription_ids[0]}/update`,
            {method: 'PATCH', data: {subscription_number: '99'}});
        expect(denied.status()).toBe(403);
        const after = (await (await api(`subscription/${input.subscription_ids[0]}/info`)).json()).data.info;
        expect(after.subscription_number).toBe('42');
        report.member_profile = {number: info.subscription_number, card_type: info.subscription_type,
            person_preserved: info.associate.first_name === 'Giulia', status_preserved: info.status_flag === 4,
            persisted_after_reload: true, reader_update_status: denied.status(), denial_left_number_unchanged: after.subscription_number === '42'};
        report.checks = ['native profile drawer opened from the member row', 'real PATCH saves the number and card type',
            'saved card fields persist after reload', 'personal name and accepted status preserved', 'read-only update denied without a data mutation'];
    }});
