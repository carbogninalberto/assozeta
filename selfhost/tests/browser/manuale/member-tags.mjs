// Real UI applies/removes/deletes tags; authorized API calls inspect state and prepare the example.
import {scenario, expect} from './scenario.mjs';
import {memberTagsAuthoredWorkflows} from '../../../../docs/manuale/member-tags-authored-workflows.mjs';

const id = 'member-tags', spec = memberTagsAuthoredWorkflows[id];
const names = ['Giulia Bianchi', 'Luca Verdi', 'Sara Conti'];
const nameOf = row => `${row.associate.first_name} ${row.associate.last_name}`;
const stableMembers = rows => rows.map(row => ({id: row.subscription_id, associate: row.associate,
    status: row.status_flag, type: row.type, role: row.role, start: row.start_date, end: row.end_date,
    number: row.subscription_number, medical: row.medical, payment: row.payment}))
    // Members share one frozen creation timestamp; compare by identity, not row order.
    .sort((a, b) => String(a.id).localeCompare(String(b.id)))
    .sort((a, b) => a.id.localeCompare(b.id));
const tagState = rows => rows.map(row => ({id: row.subscription_id,
    tags: (row.tags || []).map(tag => tag.tag_id).sort()})).sort((a, b) => a.id.localeCompare(b.id));
async function settled(page) {
    await expect(page.locator('.datatable-loading')).toHaveCount(0);
    await expect(page.locator('[data-sonner-toast]')).toHaveCount(0, {timeout: 15000});
    await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all([...document.images].map(image => image.decode().catch(() => {})));
        await Promise.all(document.getAnimations().filter(animation => animation.effect?.getTiming().iterations !== Infinity)
            .map(animation => animation.finished.catch(() => {})));
    });
}
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, capture, report, input}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const facts = {};
        const proof = (field, value) => {expect(value, field).toEqual(spec.outcome.expected[field]); facts[field] = value;};
        const take = async (checkpoint, focus, currentPage = page) => {
            const index = spec.checkpoints.findIndex(point => point.id === checkpoint);
            if (index < 0) throw new Error('Unknown member-tags checkpoint: ' + checkpoint);
            await settled(currentPage); await capture(currentPage, index + 1, checkpoint, focus);
        };
        const members = async (request = api) => {
            const response = await request('subscription/list?' + new URLSearchParams({type: 'athletes', 'pagination[perpage]': '100'}));
            expect(response.status()).toBe(200);
            return Object.values((await response.json()).data);
        };
        const tags = async (request = api) => {
            const response = await request('subscription/tags/list'); expect(response.status()).toBe(200);
            return (await response.json()).tags;
        };
        const baseline = await members(), initialTags = await tags();
        proof('baseline_members', baseline.length); proof('baseline_tags', initialTags.length);
        expect(baseline.map(nameOf).sort()).toEqual([...names].sort());
        const ids = Object.fromEntries(baseline.map(row => [nameOf(row), row.subscription_id]));
        const originalMembers = stableMembers(baseline);
        const owned = [];
        // Preparation only: definition creation has its separate published tutorial.
        for (const tag_name of ['Gruppo A', 'Laboratorio']) {
            const response = await api('subscription/tags/add', {method: 'POST', data: {tag_name}});
            expect(response.status()).toBe(200);
            const tag = (await response.json()).tag; expect(tag.tag_name).toBe(tag_name); owned.push(tag);
        }
        const [base, lab] = owned;
        for (const person of ['Giulia Bianchi', 'Luca Verdi']) {
            const response = await api(`subscription/tags/${base.tag_id}/assign/${ids[person]}`, {method: 'PATCH'});
            expect(response.status()).toBe(200);
        }
        proof('prepared_tags', (await tags()).length);
        report.fixture_preparation = {backend: 'real', definition_names: owned.map(tag => tag.tag_name),
            initial_assignments: 2, note: 'Definition creation is setup, not evidence of a UI creation procedure.'};
        const row = (name, currentPage = page) => currentPage.locator('[data-row]').filter({hasText: name}).first();
        const select = async (name, currentPage = page) => {
            const current = row(name, currentPage); await expect(current).toBeVisible();
            const check = current.locator('input[type=checkbox]').first();
            if (!await check.isChecked()) await current.locator('label.checkbox').first().click();
            await expect(check).toBeChecked();
        };
        const menu = () => page.locator('.dropdown-menu.show').filter({has: page.getByPlaceholder('Nome tag...')});
        const tagLabel = tag => menu().locator('label').filter({has: page.locator(`input[id="tag-${tag.tag_id}"]`)});
        const setTag = async (tag, checked) => {
            const label = tagLabel(tag), input = label.locator('input[type=checkbox]');
            await expect(label).toBeVisible();
            if (await input.isChecked() !== checked) await label.click();
            if (checked) await expect(input).toBeChecked(); else await expect(input).not.toBeChecked();
        };
        const openTags = async () => {
            await page.locator('#bkn_datatable_assign_tag_to_selected').click(); await expect(menu()).toBeVisible();
        };
        const reload = async () => {await page.reload(); for (const name of names) await expect(row(name)).toBeVisible();};
        const expectTags = async (expected, request = api) => {
            const current = await members(request);
            for (const [name, expectedTags] of Object.entries(expected))
                expect((current.find(item => nameOf(item) === name).tags || []).map(tag => tag.tag_id).sort())
                    .toEqual(expectedTags.map(tag => tag.tag_id).sort());
            expect(stableMembers(current)).toEqual(originalMembers);
            return current;
        };
        const apply = async (operations) => {
            // All expected PATCHes are subscribed before APPLICA, including unassignments.
            const responses = operations.map(([tag, person, action]) => page.waitForResponse(response =>
                new URL(response.url()).pathname === `/api/subscription/tags/${tag.tag_id}/${action}/${ids[person]}`
                && response.request().method() === 'PATCH'));
            await menu().getByRole('button', {name: 'APPLICA', exact: true}).click();
            for (const response of await Promise.all(responses)) expect(response.status()).toBe(200);
        };
        await open('Organizzazione', '/#/members/list');
        await select('Giulia Bianchi'); await select('Sara Conti');
        await expect(row('Luca Verdi').locator('input[type=checkbox]').first()).not.toBeChecked();
        await take('tag-assignment-selected-people');
        await openTags();
        await expect(tagLabel(base).locator('input[type=checkbox]')).not.toBeChecked();
        await expect(tagLabel(lab).locator('input[type=checkbox]')).not.toBeChecked();
        proof('menu_reopen_resets_selection', true);
        await take('tag-menu-initially-unchecked', menu());
        await setTag(base, true); await setTag(lab, true);
        await take('tag-complete-set-before-apply', menu());
        await apply([base, lab].flatMap(tag => ['Giulia Bianchi', 'Sara Conti'].map(person => [tag, person, 'assign'])));
        await reload();
        await expectTags({'Giulia Bianchi': [base, lab], 'Luca Verdi': [base], 'Sara Conti': [base, lab]});
        await expect(row('Giulia Bianchi')).toContainText('Laboratorio');
        await expect(row('Sara Conti')).toContainText('Laboratorio');
        await expect(row('Luca Verdi')).not.toContainText('Laboratorio');
        proof('assigned_people', 2); proof('complete_tag_set_saved_after_reload', true);
        proof('unselected_member_preserved', true);
        await take('tag-batch-assignment-after-reload');

        await select('Giulia Bianchi'); await openTags(); await setTag(base, true); await setTag(lab, false);
        await take('tag-personal-removal-preserves-other-tag', menu());
        await apply([[base, 'Giulia Bianchi', 'assign'], [lab, 'Giulia Bianchi', 'unassign']]);
        await reload();
        const retainedState = await expectTags({'Giulia Bianchi': [base], 'Luca Verdi': [base], 'Sara Conti': [base, lab]});
        expect(await tags()).toEqual(owned);
        await expect(row('Giulia Bianchi')).not.toContainText('Laboratorio');
        await expect(row('Giulia Bianchi')).toContainText('Gruppo A');
        await expect(row('Sara Conti')).toContainText('Laboratorio');
        proof('individual_removal_keeps_definition', true); proof('individual_removal_keeps_other_person', true);
        proof('individual_removal_keeps_selected_other_tag', true);
        await take('tag-personal-removal-after-reload');

        const reader = await actor('reader'); await reader.open('Organizzazione', '/#/members/list');
        expect(await tags(reader.api)).toEqual(owned); proof('reader_tags_readable', true);
        await expectTags({'Giulia Bianchi': [base], 'Luca Verdi': [base], 'Sara Conti': [base, lab]}, reader.api);
        await select('Sara Conti', reader.page);
        await expect(reader.page.locator('#bkn_datatable_assign_tag_to_selected')).toBeDisabled();
        proof('reader_assignment_disabled', true);
        await take('tag-reader-write-action-disabled', undefined, reader.page);
        for (const [field, route, method, data] of [
            ['reader_create_status', 'subscription/tags/add', 'POST', {tag_name: 'Vietato'}],
            ['reader_update_status', `subscription/tags/${lab.tag_id}/update`, 'PATCH', {tag_name: 'Vietato'}],
            ['reader_assign_status', `subscription/tags/${lab.tag_id}/assign/${ids['Giulia Bianchi']}`, 'PATCH'],
            ['reader_unassign_status', `subscription/tags/${lab.tag_id}/unassign/${ids['Sara Conti']}`, 'PATCH'],
            ['reader_delete_status', `subscription/tags/${lab.tag_id}/delete`, 'DELETE'],
        ]) proof(field, (await reader.api(route, {method, ...(data ? {data} : {})})).status());
        expect(tagState(await members())).toEqual(tagState(retainedState)); expect(await tags()).toEqual(owned);
        proof('reader_denials_preserve_state', true);

        // Missing UUIDs check failure preservation. They are not evidence of cross-association identity isolation.
        const missing = 'ffffffff-ffff-4fff-bfff-ffffffffffff';
        for (const [field, route, method] of [
            ['missing_tag_delete_status', `subscription/tags/${missing}/delete`, 'DELETE'],
            ['missing_tag_assignment_status', `subscription/tags/${missing}/assign/${ids['Giulia Bianchi']}`, 'PATCH'],
            ['missing_subscription_assignment_status', `subscription/tags/${lab.tag_id}/assign/${missing}`, 'PATCH'],
            ['missing_subscription_unassignment_status', `subscription/tags/${lab.tag_id}/unassign/${missing}`, 'PATCH'],
        ]) proof(field, (await api(route, {method})).status());
        expect(tagState(await members())).toEqual(tagState(retainedState)); expect(await tags()).toEqual(owned);
        proof('missing_targets_preserve_state', true);

        await select('Giulia Bianchi'); await openTags();
        const trash = () => tagLabel(lab).locator('..').locator('span').last();
        await trash().click();
        const dialog = page.locator('.swal2-popup'); await expect(dialog).toContainText('Vuoi eliminare il tag?');
        await take('tag-global-delete-confirmation', dialog);
        await dialog.getByRole('button', {name: 'Annulla', exact: true}).click();
        await expect(dialog).not.toBeVisible();
        expect(await tags()).toEqual(owned); expect(tagState(await members())).toEqual(tagState(retainedState));
        proof('cancelled_delete_preserves_definition_and_assignments', true);
        // The delegated dropdown shim may close the menu when Annulla is clicked outside it.
        if (!await menu().isVisible()) await openTags();
        await expect(menu()).toBeVisible(); await take('tag-cancelled-delete-preserves-definition', menu());
        await trash().click(); await expect(dialog).toContainText('Vuoi eliminare il tag?');
        const deleting = page.waitForResponse(response => new URL(response.url()).pathname === `/api/subscription/tags/${lab.tag_id}/delete`
            && response.request().method() === 'DELETE');
        await dialog.getByRole('button', {name: 'Elimina', exact: true}).click(); expect((await deleting).status()).toBe(200);
        await reload();
        expect(await tags()).toEqual([base]);
        const deletedState = await expectTags({'Giulia Bianchi': [base], 'Luca Verdi': [base], 'Sara Conti': [base]});
        for (const name of names) {await expect(row(name)).not.toContainText('Laboratorio'); await expect(row(name)).toContainText('Gruppo A');}
        proof('global_delete_removes_definition', true); proof('global_delete_removes_all_its_assignments', true);
        proof('global_delete_preserves_other_tag', true);
        proof('baseline_non_tag_member_data_preserved', JSON.stringify(stableMembers(deletedState)) === JSON.stringify(originalMembers));
        await take('tag-global-delete-clears-remaining-assignment');
        await select('Giulia Bianchi'); await openTags();
        await expect(tagLabel(base)).toBeVisible(); await expect(tagLabel(lab)).toHaveCount(0);
        await take('tag-global-delete-unavailable-after-reopen', menu());

        // Owned setup is removed only after every visible/provenance checkpoint; runner resets baseline on failure.
        expect((await api(`subscription/tags/${base.tag_id}/delete`, {method: 'DELETE'})).status()).toBe(200);
        expect(await tags()).toEqual(initialTags); proof('owned_tags_removed', true);
        const restored = await members(); expect(stableMembers(restored)).toEqual(originalMembers);
        expect(tagState(restored)).toEqual(tagState(baseline)); proof('baseline_members_and_tags_restored', true);
        report.member_tags_authored_workflow = facts;
        report.checks = ['real selected-row UI assignment persists a complete tag set after reload',
            'single-person unassignment preserves the tag definition and another person’s link',
            'read-only collaborator can read labels but all five writes return 403 without changes',
            'missing identifiers return 404 without changes; foreign-association runtime proof is not claimed',
            'cancelled global deletion preserves state; confirmed deletion clears all its links and preserves other tags',
            'owned setup removed and original member data restored'];
    }});
