// Real search workflow using the shared authenticated fixture and capture lifecycle.
import {scenario, expect} from './scenario.mjs';
import {focusRegion, textFocus} from './focus.mjs';

await scenario({id: 'members-search', actions: async ({page, open, capture, report}) => {
    const take = async checkpoint => {
        await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
        const rows = page.locator('[data-row]');
        const result = await rows.count()
            ? rows.last().locator('[data-field="associate"]')
            : page.getByText('Nessun dato disponibile', {exact: true});
        await expect(result).toBeVisible();
        const locator = focusRegion(page.locator('#bkn_datatable_search_query'),
            page.locator('.datatable-head [data-field="associate"]'),
            await rows.count() ? result : textFocus(result));
        await capture(page, checkpoint, locator);
    };
    await open('Organizzazione', '/#/members/list');
    const member = page.locator('[data-row]').filter({hasText: /Giulia/i}).first();
    await expect(member).toBeVisible({timeout: 90000});

    const rows = page.locator('[data-row]');
    await expect(rows).toHaveCount(3);
    for (const name of ['Giulia Bianchi', 'Luca Verdi', 'Sara Conti']) await expect(rows).toContainText([name]);
    await take('all-fixture-members');
    const search = page.locator('#bkn_datatable_search_query');
    const filtered = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname.endsWith('/subscription/list') && url.searchParams.get('query[generalSearch]') === 'Giulia';
    });
    await search.fill('Giulia');
    const response = await filtered;
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.meta.total).toBe(1);
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('Giulia Bianchi');
    await expect(rows.first()).not.toContainText('Luca Verdi');
    await take('name-search-result');
    await search.fill('PersonaInesistente');
    await expect(rows).toHaveCount(0);
    await take('no-matching-member');
    await search.locator('..').getByRole('button').click();
    await expect(search).toHaveValue('');
    await expect(rows).toHaveCount(3);
    await take('search-cleared');
    report.member_search = {initial_rows: 3, filtered_rows: data.meta.total, unknown_name_rows: 0,
        reset_rows: await rows.count(), search_cleared: await search.inputValue() === ''};
    report.checks = ['three deterministic fixture members visible', 'real filtered GET returns exactly one member',
        'name search excludes other members', 'unknown name returns no rows', 'clear button restores the full list'];
}});
