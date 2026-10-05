// Real UI and backend: create, persist, remove a link, and verify reader denials.
import {scenario, expect} from './scenario.mjs';
import {focusRegion, tableFocus} from './focus.mjs';
import {tagSources, tagExpectedOutcome} from '../../../../docs/manuale/tag-recipes.mjs';
await scenario({id: 'tags-create-assign', prefix: 'images/tutorials/tags', sources: tagSources,
    actions: async ({page, api, open, actor, capture, report, input}) => {
        await open('Organizzazione', '/#/members/list');
        const member = () => page.locator('[data-row]').filter({hasText: 'Giulia Bianchi'}).first();
        await expect(member()).toBeVisible();
        await capture(page, 1, 'members-list', focusRegion(
            page.getByRole('heading', {name: /^Tesserati/}),
            page.locator('.datatable-head [data-field="associate"]'),
            member().locator('[data-field="associate"]')));
        await member().locator('label.checkbox').first().click();
        await expect(member().locator('input[type=checkbox]')).toBeChecked();
        await page.locator('#bkn_datatable_assign_tag_to_selected').click();
        const search = page.getByPlaceholder('Nome tag...');
        await search.fill('Principianti');
        const dropdown = () => page.locator('.dropdown-menu.show').filter({has: search});
        await capture(page, 2, 'new-tag-name', dropdown());
        const creating = page.waitForResponse(response => response.url().endsWith('/subscription/tags/add') && response.request().method() === 'POST');
        await search.locator('..').locator('.input-group-append button').click();
        const created = await creating;
        expect(created.status()).toBe(200);
        const tagId = (await created.json()).tag.tag_id;
        const tagLabel = () => dropdown().locator('label').filter({hasText: 'Principianti'});
        await expect(tagLabel()).toBeVisible();
        await tagLabel().click();
        await expect(tagLabel().locator('input[type=checkbox]')).toBeChecked();
        await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
        await capture(page, 3, 'tag-selected', dropdown());
        const assigning = page.waitForResponse(response => response.url().endsWith(`/subscription/tags/${tagId}/assign/${input.subscription_ids[0]}`) && response.request().method() === 'PATCH');
        await dropdown().getByRole('button',{name:'APPLICA',exact:true}).click();
        expect((await assigning).status()).toBe(200);
        await page.reload();
        await expect(member()).toContainText('Principianti');
        await capture(page,4,'assignment-persisted',tableFocus(page, ['tags'], member()));
        await member().locator('label.checkbox').first().click();
        await page.locator('#bkn_datatable_assign_tag_to_selected').click();
        await expect(tagLabel()).toBeVisible();
        await tagLabel().locator('input[type=checkbox]').uncheck();
        await capture(page,5,'tag-unchecked-before-apply',dropdown());
        const removing = page.waitForResponse(response => response.url().endsWith(`/subscription/tags/${tagId}/unassign/${input.subscription_ids[0]}`) && response.request().method() === 'PATCH');
        await dropdown().getByRole('button',{name:'APPLICA',exact:true}).click();
        expect((await removing).status()).toBe(200);
        await page.reload();
        await expect(member()).toBeVisible();
        await expect(member()).not.toContainText('Principianti');
        await capture(page,6,'tag-removal-persists-after-reload',tableFocus(page, ['tags'], member()));
        const tags = await api('subscription/tags/list');expect(tags.status()).toBe(200);
        expect((await tags.json()).tags.some(tag=>tag.tag_id===tagId)).toBe(true);
        const registrations=await api('subscription/list');expect(registrations.status()).toBe(200);
        const rows=Object.values((await registrations.json()).data);
        for(const id of input.subscription_ids)expect((rows.find(row=>row.subscription_id===id).tags || []).some(tag=>tag.tag_id===tagId)).toBe(false);
        const reader=await actor('reader');await reader.open('Organizzazione','/#/members/list');
        await reader.page.locator('[data-row]').filter({hasText:'Giulia Bianchi'}).locator('label.checkbox').first().click();
        await expect(reader.page.locator('#bkn_datatable_assign_tag_to_selected')).toBeDisabled();
        expect((await reader.api('subscription/tags/add',{method:'POST',data:{tag_name:'Forbidden'}})).status()).toBe(403);
        expect((await reader.api(`subscription/tags/${tagId}/assign/${input.subscription_ids[0]}`,{method:'PATCH'})).status()).toBe(403);
        expect((await reader.api(`subscription/tags/${tagId}/unassign/${input.subscription_ids[0]}`,{method:'PATCH'})).status()).toBe(403);
        const finalRows=await api('subscription/list');expect(finalRows.status()).toBe(200);
        expect(Object.values((await finalRows.json()).data).every(row=>!(row.tags || []).some(tag=>tag.tag_id===tagId))).toBe(true);
        report.tags_workflow={...tagExpectedOutcome};
        report.checks=['real tag creation and assignment','assignment persisted after reload','unassignment persisted after reload','tag definition retained','unrelated members unchanged','reader UI and three write denials'];
    }});
