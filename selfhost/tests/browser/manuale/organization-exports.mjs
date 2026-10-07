// Real browser downloads and backend records; never retain recovery archives publicly.
import {scenario, expect} from './scenario.mjs';
import {organizationAuthoredWorkflows} from '../../../../docs/manuale/organization-authored-workflows.mjs';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {inflateRawSync} from 'node:zlib';
// Seeded and frozen-clock rows share timestamps (or the list is unordered), so compare
// record sets: identical rows and contents, independent of physical row order.
const recordSet = rows => (Array.isArray(rows) ? rows.map(row => JSON.stringify(row))
    : Object.entries(rows).map(entry => JSON.stringify(entry))).sort();

// Shared with the instructor report scenario. Importing this file launches no scenario.
export function zipParts(bytes) {
    let end = bytes.length - 22;
    while (end >= Math.max(0, bytes.length - 65557) && bytes.readUInt32LE(end) !== 0x06054b50) end--;
    if (end < 0 || bytes.readUInt32LE(end) !== 0x06054b50) throw new Error('Download has no ZIP directory');
    const parts = {};
    let offset = bytes.readUInt32LE(end + 16);
    for (let count = 0; count < bytes.readUInt16LE(end + 10); count++) {
        expect(bytes.readUInt32LE(offset)).toBe(0x02014b50);
        const method = bytes.readUInt16LE(offset + 10), size = bytes.readUInt32LE(offset + 20);
        const length = bytes.readUInt16LE(offset + 28), extra = bytes.readUInt16LE(offset + 30);
        const comment = bytes.readUInt16LE(offset + 32), local = bytes.readUInt32LE(offset + 42);
        const filename = bytes.subarray(offset + 46, offset + 46 + length).toString();
        expect(bytes.readUInt32LE(local)).toBe(0x04034b50);
        const start = local + 30 + bytes.readUInt16LE(local + 26) + bytes.readUInt16LE(local + 28);
        if (method !== 0 && method !== 8) throw new Error('Unsupported ZIP compression');
        const compressed = bytes.subarray(start, start + size);
        const contents = method === 8 ? inflateRawSync(compressed) : compressed;
        expect(contents.length).toBe(bytes.readUInt32LE(offset + 24));
        parts[filename] = contents;
        offset += 46 + length + extra + comment;
    }
    return parts;
}
export async function xlsxRows(page, bytes) {
    const parts = Object.fromEntries(Object.entries(zipParts(bytes)).map(([key, value]) => [key, value.toString('utf8')]));
    expect(parts['xl/worksheets/sheet1.xml']).toBeTruthy();
    return page.evaluate(parts => {
        const parse = xml => new DOMParser().parseFromString(xml, 'application/xml');
        const shared = parts['xl/sharedStrings.xml'] ? [...parse(parts['xl/sharedStrings.xml']).querySelectorAll('si')]
            .map(si => [...si.querySelectorAll('t')].map(t => t.textContent).join('')) : [];
        const sheet = parse(parts['xl/worksheets/sheet1.xml']);
        if (sheet.querySelector('parsererror')) throw new Error('Invalid worksheet XML');
        return [...sheet.querySelectorAll('sheetData row')].map(row => {
            const result = [];
            for (const cell of row.querySelectorAll('c')) {
                const column = cell.getAttribute('r').match(/^[A-Z]+/)[0];
                const index = [...column].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0) - 1;
                const raw = cell.querySelector('v')?.textContent || '';
                result[index] = cell.getAttribute('t') === 's' ? shared[Number(raw)]
                    : cell.getAttribute('t') === 'inlineStr' ? [...cell.querySelectorAll('is t')].map(t => t.textContent).join('') : raw;
            }
            return result;
        });
    }, parts);
}
export function csvRows(text) {
    const rows = []; let row = [], field = '', quoted = false;
    for (let i = 0; i < text.length; i++) {
        const c = text[i];
        if (c === '"') {
            if (quoted && text[i + 1] === '"') {field += '"'; i++;} else quoted = !quoted;
        } else if (c === ',' && !quoted) {row.push(field); field = '';}
        else if ((c === '\n' || c === '\r') && !quoted) {
            if (c === '\r' && text[i + 1] === '\n') i++;
            row.push(field); if (row.some(item => item !== '')) rows.push(row); row = []; field = '';
        } else field += c;
    }
    if (quoted) throw new Error('Unterminated CSV field');
    if (field || row.length) {row.push(field); rows.push(row);}
    return rows;
}
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const id = 'organization-exports';
const spec = organizationAuthoredWorkflows[id];
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await scenario({
    id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        // The export task queues mail itself. Fail before any action unless the server seed
        // attests the owned instance uses the local sink; no endpoint is mocked here.
        expect(input.integration_modes?.email).toEqual({backend: 'django.core.mail.backends.locmem.EmailBackend', external_delivery: false});
        const read = async route => {const res = await api(route); expect(res.status()).toBe(200); return res.json();};
        const take = async (checkpoint, focus) => {
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await capture(page, spec.checkpoints.findIndex(item => item.id === checkpoint) + 1, checkpoint, focus);
        };
        const facts = {};
        const proof = (field, value) => {expect(value, field).toEqual(spec.outcome.expected[field]); facts[field] = value;};
        const downloads = [];
        const downloadBytes = async (action, format) => {
            const [download] = await Promise.all([page.waitForEvent('download'), action()]);
            expect(await download.failure()).toBeNull();
            const bytes = fs.readFileSync(await download.path());
            downloads.push({format, bytes: bytes.length, sha256: digest(bytes)});
            await download.delete(); return bytes;
        };
        const members = () => read('subscription/list?pagination[perpage]=100');
        const payments = () => read('payment/list?pagination[perpage]=100');
        const baselineMembers = (await members()).data;
        const baselinePayments = (await payments()).data;
        const expectedNames = ['Giulia Bianchi', 'Luca Verdi', 'Sara Conti'];
        const ownedPayments = [], ownedDocuments = [];
        let cleanupFailure;
        try {
            await open('Organizzazione', '/#/members/list');
            await page.getByRole('button', {name: 'Libro Soci', exact: true}).click();
            await expect(page.locator('[data-row]')).toHaveCount(3);
            await page.getByRole('button', {name: /Esporta tutto/}).click();
            const menu = page.locator('.dropdown-menu:visible').filter({hasText: 'Csv (semplice)'});
            await take('book-full-export-menu', menu);
            const csv = csvRows((await downloadBytes(() => menu.getByText('Csv', {exact: true}).click(), 'csv')).toString('utf8').replace(/^\uFEFF/, ''));
            const first = csv[0].indexOf('nome'), last = csv[0].indexOf('cognome');
            expect(first).toBeGreaterThanOrEqual(0); expect(last).toBeGreaterThanOrEqual(0);
            expect(csv.slice(1).map(row => `${row[first]} ${row[last]}`).sort()).toEqual(expectedNames);
            proof('book_full_members', csv.length - 1);
            await page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}).fill('Sara');
            await expect(page.locator('[data-row]')).toHaveCount(1);
            await page.getByRole('button', {name: /Vista corrente/}).click();
            const printing = page.locator('#printing-modal');
            await printing.getByRole('button', {name: 'Excel', exact: true}).click();
            await expect(printing.getByRole('button', {name: /Scarica file/})).toBeVisible();
            await take('book-filtered-excel-ready', printing);
            const sheet = await xlsxRows(page, await downloadBytes(() => printing.getByRole('button', {name: /Scarica file/}).click(), 'xlsx'));
            const nameColumn = sheet[0].indexOf('Tesserato'); expect(nameColumn).toBeGreaterThanOrEqual(0);
            expect(sheet.slice(1).filter(row => row[nameColumn]).map(row => row[nameColumn])).toEqual(['Sara Conti']);
            proof('book_filtered_members', 1);
            // The modal has both a labelled header close and a text footer close.
            await printing.locator('button[aria-label="Chiudi"]').click();
            await expect(printing).not.toBeVisible();

            const old = new Date(input.reference_date.slice(0, 8) + '15T12:00:00Z'); old.setUTCMonth(old.getUTCMonth() - 1);
            const priorDate = old.toISOString().slice(0, 10);
            for (const [description, creation_date] of [['Export pagamento del mese', input.reference_date], ['Export pagamento mese precedente', priorDate]]) {
                const res = await api('payment/add', {method: 'POST', data: {description, creation_date,
                    payment_date: '', amount: '37.00', type: 'cash', expense: false,
                    custom_accounts: input.cash_account_id, payment_category: input.payment_category_id, complex_item: null}});
                expect(res.status()).toBe(201); const created = await res.json();
                expect(created.payment_id).toBeTruthy(); ownedPayments.push(created.payment_id);
            }
            report.fixture_preparation = {backend: 'real', payment_count: ownedPayments.length,
                purpose: 'Known included and excluded month records, created through authorized APIs'};
            await open('Pagamenti', '/#/payment/list');
            await expect(page).toHaveURL(/\/#\/payment\/list$/);
            // Confirm the payment component mounted before touching its search:
            // the previous Libro Soci table also exposes the same search label.
            const monthlyPayment = page.locator('[data-row]').filter({hasText: 'Export pagamento del mese'});
            await expect(monthlyPayment).toHaveCount(1);
            await expect(monthlyPayment).toBeVisible();
            const [filteredResponse] = await Promise.all([page.waitForResponse(response => {
                const url = new URL(response.url());
                return url.pathname === '/api/payment/list' && response.request().method() === 'GET'
                    && url.searchParams.get('query[generalSearch]') === 'Export pagamento';
            }), page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}).fill('Export pagamento')]);
            expect(filteredResponse.status()).toBe(200);
            await expect(page.locator('[data-row]')).toHaveCount(1);
            await expect(monthlyPayment).toBeVisible();
            await take('payment-monthly-search', page.locator('.datatable-table').first());
            const filtered = await xlsxRows(page, await downloadBytes(() => page.locator('button:is([title="Esporta ricerca corrente in Excel"],[data-original-title="Esporta ricerca corrente in Excel"]), button[data-original-title="Esporta ricerca corrente in Excel"]').click(), 'xlsx'));
            const filteredText = JSON.stringify(filtered);
            proof('monthly_payment_included', filteredText.includes('Export pagamento del mese'));
            proof('previous_month_payment_excluded', !filteredText.includes('Export pagamento mese precedente'));
            await page.getByRole('button', {name: 'Esporta', exact: true}).click();
            const paymentMenu = page.locator('.dropdown-menu:visible').filter({hasText: '(dall\'inizio)'});
            await take('payment-general-export-menu', paymentMenu);
            const general = (await downloadBytes(() => paymentMenu.getByText('CSV', {exact: false}).click(), 'csv')).toString('utf8');
            const generalRows = csvRows(general.replace(/^\uFEFF/, '')); expect(generalRows.length).toBeGreaterThan(1);
            proof('general_csv_includes_both', general.includes('Export pagamento del mese') && general.includes('Export pagamento mese precedente'));

            const before = (await read('association/export/list')).exports;
            expect(before.length).toBeLessThan(3);
            await open('Impostazioni', '/#/profile');
            await page.getByText('Gestione Dati', {exact: true}).first().click();
            await expect(page.getByRole('button', {name: 'Avvia Export', exact: true})).toBeEnabled();
            await take('export-management-before-start');
            const started = page.waitForResponse(res => new URL(res.url()).pathname.replace(/\/$/, '') === '/api/association/export/start'
                && res.request().method() === 'POST');
            await page.getByRole('button', {name: 'Avvia Export', exact: true}).click();
            const res = await started; expect(res.status()).toBe(202); const task = (await res.json()).task_id;
            expect(task).toBeTruthy();
            await expect.poll(async () => {
                const status = await read('association/export/status?task_id=' + task);
                if (status.status === 'FAILURE') throw new Error('Real association export task failed');
                return status.status;
            }, {timeout: 180000, intervals: [1000, 2000, 5000]}).toBe('SUCCESS');
            const after = (await read('association/export/list')).exports;
            const fresh = after.filter(item => !before.some(old => old.document_id === item.document_id));
            expect(fresh).toHaveLength(1); ownedDocuments.push(fresh[0].document_id);
            await page.reload();
            const row = page.locator('.border.rounded-lg').filter({hasText: fresh[0].filename});
            await expect(row).toBeVisible();
            proof('zip_persists_after_reload', (await read('association/export/list')).exports.some(item => item.document_id === fresh[0].document_id));
            await take('export-completed-after-reload', row);
            const zip = await downloadBytes(() => row.locator('button:is([title="Scarica"],[data-original-title="Scarica"])').click(), 'zip');
            const parts = zipParts(zip); const manifest = JSON.parse(parts['manifest.json'].toString());
            expect(manifest.export_format).toBe('bakney_sport_export_v1');
            const records = model => {
                const entry = manifest.models_exported.find(item => item.name === model); expect(entry).toBeTruthy();
                const content = JSON.parse(parts[entry.file].toString()); expect(Array.isArray(content)).toBe(true); return content;
            };
            proof('downloaded_zip_validated', Object.keys(parts).length > 1);
            proof('zip_association_matches', manifest.association.sport_association_id === input.association_id);
            proof('zip_members_match', input.subscription_ids.every(uid => records('Subscription').some(item => item.subscription_id === uid)));
            proof('zip_payments_match', [...input.payment_ids, ...ownedPayments].every(uid => records('Payment').some(item => item.payment_id === uid)));
            // The ZIP includes recovery credentials; download.delete() above removes the private
            // browser file, and neither its contents nor capability tokens enter the report.
            report.downloads = downloads;
            report.integration_modes = {email: {backend: 'locmem', delivery_verified: false}};
            const reader = await actor('reader');
            const denials = [];
            for (const [route, method, data] of [
                ['association/export/start', 'POST', {}], ['association/export/list', 'GET', undefined],
                ['association/export/status?task_id=' + task, 'GET', undefined],
                ['association/export/delete', 'DELETE', {document_id: fresh[0].document_id}],
            ]) {
                const denied = await reader.api(route, {method, ...(data ? {data} : {})}); expect(denied.status()).toBe(403);
                denials.push(denied.status());
                report.expected_denials.push({identity: 'reader', path: '/api/' + route.split('?')[0], status: denied.status()});
            }
            proof('reader_export_denials', denials.length);
            expect((await read('association/export/list')).exports.some(item => item.document_id === fresh[0].document_id)).toBe(true);
            await row.locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
            const confirmation = page.locator('.swal2-popup');
            await confirmation.getByRole('button', {name: 'Elimina', exact: true}).click();
            await expect(row).toHaveCount(0);
            proof('owned_export_removed', !(await read('association/export/list')).exports.some(item => item.document_id === fresh[0].document_id));
            ownedDocuments.length = 0;
        } finally {
            // Cleanup only IDs created by this invocation, including failed-scenario paths.
            for (const document_id of ownedDocuments) {
                const res = await api('association/export/delete', {method: 'DELETE', data: {document_id}});
                if (![200, 404].includes(res.status())) cleanupFailure = 'Owned export cleanup failed';
            }
            for (const uid of ownedPayments) {
                const res = await api(`payment/${uid}/delete`, {method: 'DELETE'});
                if (![200, 404].includes(res.status())) cleanupFailure = 'Owned payment cleanup failed';
            }
            if (cleanupFailure) throw new Error(cleanupFailure);
        }
        const finalPayments = Object.values((await payments()).data).filter(row => input.payment_ids.includes(row.payment_id));
        proof('baseline_records_preserved', JSON.stringify(recordSet((await members()).data)) === JSON.stringify(recordSet(baselineMembers))
            && JSON.stringify(recordSet(finalPayments)) === JSON.stringify(recordSet(Object.values(baselinePayments))));
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.checks = ['real Book CSV and filtered XLSX parsed', 'monthly and general payment downloads compared',
            'real Celery ZIP completed, reopened, downloaded and inspected', 'reader export denied; only owned artifacts deleted'];
        report.external_gaps = [{operation: 'export-email-delivery', status: 'needs_external_verification'},
            {operation: 'export-backup-restore', status: 'needs_external_verification'}];
    },
});
