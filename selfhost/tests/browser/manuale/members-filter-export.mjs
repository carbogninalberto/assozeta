// Real UI and backend. Tag assignments are declared fixture preparation, not a captured tag-edit guide.
import {scenario, expect} from './scenario.mjs';
import {memberAuthoredWorkflows} from '../../../../docs/manuale/member-authored-workflows.mjs';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {inflateRawSync} from 'node:zlib';

const id = 'members-filter-export';
const spec = memberAuthoredWorkflows[id];
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const names = ['Giulia Bianchi', 'Luca Verdi', 'Sara Conti'];
const values = data => Object.values(data || {});
const memberName = row => `${row.associate.first_name} ${row.associate.last_name}`;
async function setChecked(input, checked = true) {
    if (await input.isChecked() !== checked) await input.locator('..').click();
    if (checked) await expect(input).toBeChecked(); else await expect(input).not.toBeChecked();
}

// Read the ZIP central directory and real worksheet XML rather than trusting an extension or response.
function xlsxParts(bytes) {
    let end = bytes.length - 22;
    while (end >= Math.max(0, bytes.length - 65557) && bytes.readUInt32LE(end) !== 0x06054b50) end--;
    if (end < 0 || bytes.readUInt32LE(end) !== 0x06054b50) throw new Error('Excel download has no ZIP directory');
    const parts = {};
    let offset = bytes.readUInt32LE(end + 16);
    for (let n = 0; n < bytes.readUInt16LE(end + 10); n++) {
        expect(bytes.readUInt32LE(offset)).toBe(0x02014b50);
        const method = bytes.readUInt16LE(offset + 10), size = bytes.readUInt32LE(offset + 20);
        const length = bytes.readUInt16LE(offset + 28), extra = bytes.readUInt16LE(offset + 30);
        const comment = bytes.readUInt16LE(offset + 32), local = bytes.readUInt32LE(offset + 42);
        const filename = bytes.subarray(offset + 46, offset + 46 + length).toString();
        expect(bytes.readUInt32LE(local)).toBe(0x04034b50);
        const start = local + 30 + bytes.readUInt16LE(local + 26) + bytes.readUInt16LE(local + 28);
        const compressed = bytes.subarray(start, start + size);
        if (method !== 0 && method !== 8) throw new Error('Unsupported XLSX ZIP compression');
        parts[filename] = (method === 8 ? inflateRawSync(compressed) : compressed).toString('utf8');
        offset += 46 + length + extra + comment;
    }
    return parts;
}
async function xlsxRows(page, bytes) {
    const parts = xlsxParts(bytes);
    expect(parts['xl/worksheets/sheet1.xml']).toBeTruthy();
    return page.evaluate(parts => {
        const parse = xml => new DOMParser().parseFromString(xml, 'application/xml');
        const shared = parts['xl/sharedStrings.xml']
            ? [...parse(parts['xl/sharedStrings.xml']).querySelectorAll('si')].map(si =>
                [...si.querySelectorAll('t')].map(t => t.textContent).join('')) : [];
        const sheet = parse(parts['xl/worksheets/sheet1.xml']);
        if (sheet.querySelector('parsererror')) throw new Error('Invalid worksheet XML');
        return [...sheet.querySelectorAll('sheetData row')].map(row => {
            const fields = [];
            for (const cell of row.querySelectorAll('c')) {
                const column = cell.getAttribute('r').match(/^[A-Z]+/)[0];
                const index = [...column].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0) - 1;
                const raw = cell.querySelector('v')?.textContent || '';
                fields[index] = cell.getAttribute('t') === 's' ? shared[Number(raw)]
                    : cell.getAttribute('t') === 'inlineStr'
                        ? [...cell.querySelectorAll('is t')].map(t => t.textContent).join('') : raw;
            }
            return fields;
        });
    }, parts);
}
function csvRows(text) {
    const rows = []; let row = [], field = '', quoted = false;
    for (let i = 0; i < text.length; i++) {
        const c = text[i];
        if (c === '"') {
            if (quoted && text[i + 1] === '"') {field += '"'; i++;}
            else quoted = !quoted;
        } else if (c === ',' && !quoted) {row.push(field); field = '';}
        else if ((c === '\n' || c === '\r') && !quoted) {
            if (c === '\r' && text[i + 1] === '\n') i++;
            row.push(field); if (row.some(x => x !== '')) rows.push(row); row = []; field = '';
        } else field += c;
    }
    if (quoted) throw new Error('Unterminated CSV quoted field');
    if (field || row.length) {row.push(field); rows.push(row);}
    return rows;
}
async function settled(page) {
    await expect(page.locator('.datatable-loading')).toHaveCount(0);
    await expect(page.locator('[data-sonner-toast]')).toHaveCount(0, {timeout: 15000});
    await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all([...document.images].map(img => img.decode().catch(() => {})));
        await Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity)
            .map(a => a.finished.catch(() => {})));
    });
}

await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const facts = {};
        const proof = (field, value) => {expect(value, field).toEqual(spec.outcome.expected[field]); facts[field] = value;};
        const snap = async (checkpoint, focus, currentPage = page) => {
            const number = spec.checkpoints.findIndex(c => c.id === checkpoint) + 1;
            if (!number) throw new Error('Unknown checkpoint ' + checkpoint);
            await settled(currentPage);
            await capture(currentPage, number, checkpoint, focus);
        };
        async function list(params = {}, requestApi = api) {
            const query = new URLSearchParams({'pagination[perpage]': '100', type: 'athletes', ...params});
            const res = await requestApi('subscription/list?' + query);
            expect(res.ok()).toBeTruthy(); return res.json();
        }
        const baseline = await list();
        proof('baseline_members', baseline.meta.total);
        expect(values(baseline.data).map(memberName).sort()).toEqual([...names].sort());
        const fixture = Object.fromEntries(values(baseline.data).map(r => [memberName(r), r]));
        const stable = data => values(data).map(row => ({id: row.subscription_id, associate: row.associate,
            status: row.status_flag, type: row.type, role: row.role, start: row.start_date, end: row.end_date,
            number: row.subscription_number, medical: row.medical, payment: row.payment}))
            .sort((a, b) => a.id.localeCompare(b.id));
        const unchanged = stable(baseline.data);

        // Preparation uses real authorized APIs. The scenario does not publish tag-creation procedures.
        const tags = [];
        for (const tag_name of ['Gruppo A', 'Gare']) {
            const res = await api('subscription/tags/add', {method: 'POST', data: {tag_name}});
            expect(res.ok()).toBeTruthy(); const data = await res.json();
            expect(data.tag.tag_id).toBeTruthy(); tags.push(data.tag.tag_id);
        }
        for (const [index, people] of [[0, ['Giulia Bianchi', 'Sara Conti']], [1, ['Luca Verdi', 'Sara Conti']]]) {
            for (const person of people) {
                const res = await api(`subscription/tags/${tags[index]}/assign/${fixture[person].subscription_id}`,
                    {method: 'PATCH'});
                expect(res.ok()).toBeTruthy();
            }
        }
        report.fixture_preparation = {tag_count: tags.length, assignments: 4, backend: 'real'};
        await open('Organizzazione', '/#/members/list');
        const rows = page.locator('[data-row]');
        const search = page.locator('#bkn_datatable_search_query');
        const table = page.locator('.datatable-table').first();
        // MobileFilterSheet's desktop wrapper uses display:contents and has no
        // scroll/crop box. Focus the visible row containing its real controls.
        const filters = page.locator('.datatable-filter-controls').filter({has: page.locator('#show_current')});
        await expect(rows).toHaveCount(3); await snap('filters-baseline', table);
        const select = async (selector, label) => {
            await page.locator(selector).click();
            await page.locator('.svelte-select-list:visible').getByText(label, {exact: true}).click();
        };
        const waitList = predicate => page.waitForResponse(response => {
            const url = new URL(response.url());
            return url.pathname === '/api/subscription/list' && response.request().method() === 'GET' && predicate(url.searchParams);
        });
        async function change(selectAction, predicate, count) {
            const pending = waitList(predicate); await selectAction();
            const res = await pending; expect(res.ok()).toBeTruthy(); const data = await res.json();
            expect(data.meta.total).toBe(count); await expect(rows).toHaveCount(count); return data;
        }
        // The initial store already selects the current year. Exercise a real
        // change away and back rather than waiting for an unchanged selection
        // to invent a new request that the native select does not dispatch.
        for (const [label, value, field, count] of [
            ['Anni precedenti', '0', 'previous_members', 0],
            ['Anno corrente', '1', 'current_members', 3],
            ['Pre-iscrizioni', '2', 'future_members', 0],
        ]) {
            const result = await change(() => select('#show_current', label), q => q.get('query[current_year]') === value, count);
            proof(field, result.meta.total);
            if (value === '1') await snap('period-current', filters);
        }
        await select('#show_current', 'Personalizzato');
        const referenceYear = Number(input.reference_date.slice(0, 4));
        async function custom(preset, year, count) {
            await page.locator('#period_date_range').click();
            const panel = page.locator('.drp-panel:visible');
            // Named presets commit immediately; only custom calendar ranges use Applica.
            const end = preset === 'Anno Corrente' ? input.reference_date.split('-').reverse().join('/') : `31/12/${year}`;
            return change(() => panel.getByRole('button', {name: preset, exact: true}).click(),
                q => q.get('query[period_start]') === `01/01/${year}` && q.get('query[period_end]') === end, count);
        }
        proof('custom_disjoint_members', (await custom('Anno Precedente', referenceYear - 1, 0)).meta.total);
        proof('custom_overlap_members', (await custom('Anno Corrente', referenceYear, 3)).meta.total);
        await snap('period-custom', filters);
        await rows.filter({hasText: 'Giulia Bianchi'}).locator('[data-field="associate"]').click();
        const drawer = page.locator('.drawer:visible').last();
        await expect(drawer.locator('input[name="start_date"]')).toHaveValue(fixture['Giulia Bianchi'].start_date);
        await expect(drawer.locator('input[name="end_date"]')).toHaveValue(fixture['Giulia Bianchi'].end_date);
        proof('detail_dates_match', await drawer.locator('input[name="start_date"]').inputValue() === fixture['Giulia Bianchi'].start_date
            && await drawer.locator('input[name="end_date"]').inputValue() === fixture['Giulia Bianchi'].end_date);
        await drawer.getByText('Informazioni Iscrizione', {exact: true}).scrollIntoViewIfNeeded();
        await snap('period-detail', drawer);
        await drawer.locator('.drawer-header button.close').click();
        await expect(drawer).not.toBeVisible();
        await page.getByRole('button', {name: 'Ripristina filtri', exact: true}).click(); await expect(rows).toHaveCount(3);

        async function tagPanel() {
            await page.getByRole('button', {name: /^Filtra Tag/}).click(); return page.locator('.query-filter-panel:visible');
        }
        let panel = await tagPanel();
        await setChecked(panel.getByLabel('Gruppo A', {exact: true})); await setChecked(panel.getByLabel('Gare', {exact: true}));
        await setChecked(panel.getByLabel('Contiene i tag selezionati', {exact: true}));
        await snap('tags-choices', panel);
        const and = await change(() => panel.getByRole('button', {name: 'Applica', exact: true}).click(),
            q => q.get('query[tags_and]') === '1' && q.get('query[tags]')?.split(',').length === 2, 1);
        expect(values(and.data).map(memberName)).toEqual(['Sara Conti']); proof('tag_and_rows', and.meta.total);
        await snap('tags-and', table);
        panel = await tagPanel(); await setChecked(panel.getByLabel('Contiene i tag selezionati', {exact: true}), false);
        const or = await change(() => panel.getByRole('button', {name: 'Applica', exact: true}).click(),
            q => q.get('query[tags_and]') === '0' && q.get('query[tags]')?.split(',').length === 2, 4);
        // Current backend joins return Sara twice for two matching tags; never claim deduplicated rows.
        proof('tag_or_rows', or.meta.total); proof('tag_or_unique_members', new Set(values(or.data).map(r => r.subscription_id)).size);
        await snap('tags-or', table);
        await page.getByRole('button', {name: /^Rimuovi filtro Filtra Tag/}).click(); await expect(rows).toHaveCount(3);

        const ages = Object.fromEntries(values(baseline.data).map(r => [memberName(r),
            referenceYear - Number(r.associate.born_date.slice(0, 4))
            - (input.reference_date.slice(5) < r.associate.born_date.slice(5) ? 1 : 0)]));
        const agePanel = async () => {await page.getByRole('button', {name: /^Età/}).click(); return page.locator('.query-filter-panel:visible');};
        panel = await agePanel();
        await panel.getByLabel('Da anni', {exact: true}).fill(String(ages['Sara Conti']));
        await panel.getByLabel('A anni', {exact: true}).fill(String(ages['Luca Verdi'])); await snap('age-limits', panel);
        const ageRange = await change(() => panel.getByRole('button', {name: 'Applica', exact: true}).click(),
            q => q.get('query[from_age]') === String(ages['Sara Conti']) && q.get('query[to_age]') === String(ages['Luca Verdi']), 1);
        expect(values(ageRange.data).map(memberName)).toEqual(['Sara Conti']); proof('age_range_members', ageRange.meta.total);
        await snap('age-result', table);
        panel = await agePanel(); await panel.getByLabel('Da anni', {exact: true}).fill('');
        await panel.getByLabel('A anni', {exact: true}).fill(String(ages['Giulia Bianchi']));
        const upperOnly = await change(() => panel.getByRole('button', {name: 'Applica', exact: true}).click(),
            q => !q.get('query[from_age]') && q.get('query[to_age]') === String(ages['Giulia Bianchi']), 2);
        expect(values(upperOnly.data).map(memberName).sort()).toEqual(['Luca Verdi', 'Sara Conti']);
        proof('age_upper_only_members', upperOnly.meta.total);
        await page.getByRole('button', {name: /^Rimuovi filtro Età/}).click(); await expect(rows).toHaveCount(3);

        // Ripristina filtri already restored Anno corrente; selecting it again emits no request.
        await expect(page.locator('#show_current').locator('xpath=ancestor::div[contains(@class,"svelte-select")][1]')).toContainText('Anno corrente');
        await change(() => select('#search_status', 'In attesa'), q => q.get('query[status_flag]') === '2', 0);
        await change(() => select('#search_status', 'Accettata'), q => q.get('query[status_flag]') === '4', 3);
        panel = await tagPanel(); await setChecked(panel.getByLabel('Gruppo A', {exact: true}));
        await setChecked(panel.getByLabel('Gare', {exact: true})); await setChecked(panel.getByLabel('Contiene i tag selezionati', {exact: true}));
        await change(() => panel.getByRole('button', {name: 'Applica', exact: true}).click(), q => q.get('query[tags_and]') === '1', 1);
        await snap('combined-filters', filters);
        const empty = await change(() => search.fill('Giulia'), q => q.get('query[generalSearch]') === 'Giulia', 0);
        proof('combined_empty_members', empty.meta.total); await snap('combined-empty', table);
        await change(() => search.locator('..').getByRole('button').click(), q => !q.get('query[generalSearch]'), 1);
        await change(() => page.getByRole('button', {name: 'Ripristina filtri', exact: true}).click(),
            q => !q.get('query[status_flag]') && !q.get('query[tags]'), 3);
        proof('reset_restored_members', await rows.count()); await snap('filters-reset', table);

        await change(() => search.fill('Sara'), q => q.get('query[generalSearch]') === 'Sara', 1);
        await setChecked(rows.first().locator('input[type="checkbox"]'));
        await expect(page.locator('#bkn_datatable_archive_selected')).toBeEnabled();
        await snap('filtered-selection', table);
        await setChecked(rows.first().locator('input[type="checkbox"]'), false);
        await page.getByRole('button', {name: /Vista corrente/}).click();
        const modal = page.locator('#printing-modal');
        const generated = page.waitForResponse(r => new URL(r.url()).pathname === '/api/subscription/list' && r.request().method() === 'POST');
        await modal.getByRole('button', {name: 'Excel', exact: true}).click(); const response = await generated;
        expect(response.ok()).toBeTruthy(); expect(response.request().postDataJSON().format).toBe('excel');
        expect(new URL(response.url()).searchParams.get('query[generalSearch]')).toBe('Sara');
        await expect(modal.getByRole('button', {name: /Scarica file/})).toBeVisible(); await snap('current-excel', modal);
        const downloadDirectory = path.join(process.env.ASSOZETA_MANUAL_RUN, 'downloads', id);
        fs.mkdirSync(downloadDirectory, {recursive: true});
        const excelEvent = page.waitForEvent('download'); await modal.getByRole('button', {name: /Scarica file/}).click();
        const excel = await excelEvent; expect(await excel.failure()).toBeNull();
        const excelPath = path.join(downloadDirectory, 'vista-corrente.xlsx'); await excel.saveAs(excelPath);
        const excelBytes = fs.readFileSync(excelPath), sheet = await xlsxRows(page, excelBytes);
        const nameColumn = sheet[0].indexOf('Tesserato'); expect(nameColumn).toBeGreaterThanOrEqual(0);
        const exportedNames = sheet.slice(1).filter(r => r[nameColumn]).map(r => r[nameColumn]);
        expect(exportedNames).toEqual(['Sara Conti']); proof('current_excel_members', exportedNames.length);
        await modal.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
        await page.getByRole('button', {name: /Esporta tutto/}).click();
        const exportMenu = page.locator('.dropdown-menu:visible').filter({hasText: 'Csv (semplice)'});
        await snap('complete-export', exportMenu);
        const csvResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/subscription/list/export');
        const csvEvent = page.waitForEvent('download'); await exportMenu.getByText('Csv', {exact: true}).click();
        const fullResponse = await csvResponse; expect(fullResponse.ok()).toBeTruthy();
        proof('complete_export_ignores_filters', !new URL(fullResponse.url()).searchParams.has('query[generalSearch]'));
        const csv = await csvEvent; expect(await csv.failure()).toBeNull();
        const csvPath = path.join(downloadDirectory, 'elenco-completo.csv'); await csv.saveAs(csvPath);
        const csvBytes = fs.readFileSync(csvPath), csvData = csvRows(csvBytes.toString('utf8').replace(/^\uFEFF/, ''));
        const first = csvData[0].indexOf('nome'), last = csvData[0].indexOf('cognome');
        expect(first).toBeGreaterThanOrEqual(0); expect(last).toBeGreaterThanOrEqual(0);
        const fullNames = csvData.slice(1).map(row => `${row[first]} ${row[last]}`).sort();
        expect(fullNames).toEqual([...names].sort()); proof('full_csv_members', fullNames.length);
        report.downloads = [
            {path: `downloads/${id}/vista-corrente.xlsx`, sha256: sha(excelBytes), parsed_format: 'xlsx', members: exportedNames.length},
            {path: `downloads/${id}/elenco-completo.csv`, sha256: sha(csvBytes), parsed_format: 'csv', members: fullNames.length},
        ];

        await change(() => search.locator('..').getByRole('button').click(), q => !q.get('query[generalSearch]'), 3);
        const ageHeader = page.locator('.datatable-head [data-field="age"]'); await expect(ageHeader).toBeVisible();
        await page.locator(':is([title="Mostra / nascondi colonne tabella"],[data-original-title="Mostra / nascondi colonne tabella"]),[data-original-title="Mostra / nascondi colonne tabella"]').click();
        const picker = page.locator('.dropdown-menu:visible').filter({has: page.getByText('Età', {exact: true})});
        await snap('columns-picker', picker);
        const settingsRequest = page.waitForResponse(r => new URL(r.url()).pathname === '/api/profile/settings/tables' && r.request().method() === 'POST');
        await picker.locator('.dropdown-item').filter({hasText: /^\s*Età\s*$/}).click();
        expect((await settingsRequest).ok()).toBeTruthy(); await expect(ageHeader).toHaveCount(0);
        const local = await page.evaluate(() => JSON.parse(localStorage.getItem('tablesSettings')));
        proof('column_hidden_locally', local['members-list'].find(c => c.title === 'Età').checked === false);
        const preferences = await api('profile/settings'); expect(preferences.ok()).toBeTruthy();
        const server = (await preferences.json()).settings.table_settings;
        proof('column_saved_server', server['members-list'].find(c => c.title === 'Età').checked === false);
        await search.click(); await snap('columns-hidden', table);
        await page.reload(); await expect(rows).toHaveCount(3); await expect(ageHeader).toHaveCount(0);
        proof('column_hidden_after_reload', await ageHeader.count() === 0); await snap('columns-reloaded', table);
        await page.getByRole('button', {name: 'Libro Soci', exact: true}).click(); await expect(rows).toHaveCount(3);
        await expect(ageHeader).toBeVisible(); proof('book_column_independent', await ageHeader.isVisible());
        await page.getByRole('button', {name: 'Tesserati', exact: true}).click(); await expect(rows).toHaveCount(3);
        proof('filters_preserve_member_data', JSON.stringify(stable((await list()).data)) === JSON.stringify(unchanged));

        const reader = await actor('reader'); await reader.open('Organizzazione', '/#/members/list');
        const readerRows = reader.page.locator('[data-row]'); await expect(readerRows).toHaveCount(3);
        proof('reader_visible_members', (await list({}, reader.api)).meta.total);
        await reader.page.locator('#bkn_datatable_search_query').fill('Sara'); await expect(readerRows).toHaveCount(1);
        proof('reader_filtered_members', (await list({'query[generalSearch]': 'Sara'}, reader.api)).meta.total);
        await setChecked(readerRows.first().locator('input[type="checkbox"]'));
        await expect(reader.page.locator('#bkn_datatable_archive_selected')).toBeDisabled();
        proof('reader_archive_disabled', await reader.page.locator('#bkn_datatable_archive_selected').isDisabled());
        const denied = await reader.api(`subscription/${fixture['Sara Conti'].subscription_id}/update`,
            {method: 'PATCH', data: {subscription_number: '9999'}});
        proof('reader_update_denied', denied.status() === 403);
        proof('reader_state_preserved', JSON.stringify(stable((await list()).data)) === JSON.stringify(unchanged));
        report.expected_denials.push({identity: 'reader', path: '/api/subscription/[fixture]/update', status: denied.status()});
        await snap('reader-filtered', reader.page.locator('.datatable-table').first(), reader.page);
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.checks.push('real filter API responses match displayed rows', 'downloaded XLSX worksheet and CSV rows parsed',
            'column preferences persisted locally and on server', 'reader modification refused without state mutation');
        report.limits = ['PDF and simplified export variants not exercised',
            'OR tag joins currently show duplicate rows for members matching both tags',
            'tag creation APIs used only for fixture preparation', 'no bulk archive, deletion, invitations or email sent'];
    },
});
