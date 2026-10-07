// Real downloads and backend writes only. API preparation is not a captured course-creation guide.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {inflateRawSync} from 'node:zlib';
import {scenario, expect} from './scenario.mjs';
import {navigateToDownload} from './download.mjs';
import {paymentMaintenanceAuthoredWorkflows} from '../../../../docs/manuale/payment-maintenance-authored-workflows.mjs';

const id = 'payments-maintenance';
const spec = paymentMaintenanceAuthoredWorkflows[id];
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const sorted = payments => [...payments].sort((a, b) => a.payment_id.localeCompare(b.payment_id));

function csvRows(text) {
    const rows = []; let row = [], field = '', quoted = false;
    for (let index = 0; index < text.length; index++) {
        const char = text[index];
        if (char === '"') {
            if (quoted && text[index + 1] === '"') {field += '"'; index++;}
            else quoted = !quoted;
        } else if (char === ',' && !quoted) {row.push(field); field = '';}
        else if ((char === '\n' || char === '\r') && !quoted) {
            if (char === '\r' && text[index + 1] === '\n') index++;
            row.push(field); if (row.some(value => value !== '')) rows.push(row);
            row = []; field = '';
        } else field += char;
    }
    if (quoted) throw new Error('Unterminated CSV field');
    if (field || row.length) {row.push(field); rows.push(row);}
    return rows;
}

// Read the actual worksheet, including numeric and inline/shared-string cells.
async function excelRows(page, bytes) {
    let end = bytes.length - 22;
    while (end >= Math.max(0, bytes.length - 65557) && bytes.readUInt32LE(end) !== 0x06054b50) end--;
    if (end < 0 || bytes.readUInt32LE(end) !== 0x06054b50) throw new Error('Excel download has no ZIP directory');
    const parts = {}; let offset = bytes.readUInt32LE(end + 16);
    for (let count = 0; count < bytes.readUInt16LE(end + 10); count++) {
        expect(bytes.readUInt32LE(offset)).toBe(0x02014b50);
        const method = bytes.readUInt16LE(offset + 10), size = bytes.readUInt32LE(offset + 20);
        const length = bytes.readUInt16LE(offset + 28), extra = bytes.readUInt16LE(offset + 30);
        const comment = bytes.readUInt16LE(offset + 32), local = bytes.readUInt32LE(offset + 42);
        const filename = bytes.subarray(offset + 46, offset + 46 + length).toString();
        expect(bytes.readUInt32LE(local)).toBe(0x04034b50);
        const start = local + 30 + bytes.readUInt16LE(local + 26) + bytes.readUInt16LE(local + 28);
        const compressed = bytes.subarray(start, start + size);
        if (method !== 0 && method !== 8) throw new Error('Unsupported worksheet ZIP compression');
        if (filename.startsWith('xl/')) parts[filename] = (method === 8 ? inflateRawSync(compressed) : compressed).toString('utf8');
        offset += 46 + length + extra + comment;
    }
    expect(parts['xl/worksheets/sheet1.xml']).toBeTruthy();
    return page.evaluate(parts => {
        const parse = xml => {
            const document = new DOMParser().parseFromString(xml, 'application/xml');
            if (document.querySelector('parsererror')) throw new Error('Invalid worksheet XML');
            return document;
        };
        const shared = parts['xl/sharedStrings.xml'] ? [...parse(parts['xl/sharedStrings.xml']).querySelectorAll('si')]
            .map(item => [...item.querySelectorAll('t')].map(text => text.textContent).join('')) : [];
        return [...parse(parts['xl/worksheets/sheet1.xml']).querySelectorAll('sheetData row')].map(row => {
            const fields = [];
            for (const cell of row.querySelectorAll('c')) {
                const column = cell.getAttribute('r').match(/^[A-Z]+/)[0];
                const index = [...column].reduce((value, char) => value * 26 + char.charCodeAt(0) - 64, 0) - 1;
                const raw = cell.querySelector('v')?.textContent || '';
                fields[index] = cell.getAttribute('t') === 's' ? shared[Number(raw)] : cell.getAttribute('t') === 'inlineStr'
                    ? [...cell.querySelectorAll('is t')].map(text => text.textContent).join('') : raw;
            }
            return fields;
        });
    }, parts);
}

await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8); expect(input.fixture_profile || 'baseline').toBe('baseline');
        const facts = {};
        const proof = (key, value) => {expect(value, key).toEqual(spec.outcome.expected[key]); facts[key] = value;};
        const read = async (route, request = api) => {
            const response = await request(route); expect(response.status()).toBe(200); return response.json();
        };
        const payments = async () => Object.values((await read('payment/list?pagination[perpage]=100')).data);
        const preferences = async (request = api) => (await read('profile/settings', request)).settings.table_settings;
        const take = async (checkpoint, focus, currentPage = page, options) => {
            const index = spec.checkpoints.findIndex(point => point.id === checkpoint);
            expect(index).toBeGreaterThanOrEqual(0);
            await expect(currentPage.locator('.datatable-loading:visible')).toHaveCount(0);
            await expect(currentPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await capture(currentPage, index + 1, checkpoint, focus, options);
        };
        const baseline = sorted(await payments()); proof('baseline_payments', baseline.length);
        expect(baseline.map(payment => payment.payment_id).sort()).toEqual([...input.payment_ids].sort());
        const originalPreferences = await preferences();
        const ownedPayments = new Set(); let ownedCourse = null, ownedReceipt = null;
        let preferencesChanged = false;
        report.downloads = [];
        const persistDownload = async (download, kind, expectedBytes) => {
            expect(await download.failure()).toBeNull();
            const bytes = fs.readFileSync(await download.path());
            if (expectedBytes) expect(sha(bytes)).toBe(sha(expectedBytes));
            const relative = `downloads/${id}/${kind}`;
            const file = path.join(process.env.ASSOZETA_MANUAL_RUN, relative);
            fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, bytes);
            report.downloads.push({path: relative, sha256: sha(bytes), bytes: bytes.length});
            return bytes;
        };
        const validateRows = (rows, expectedPayments, pettyCash = false) => {
            const header = rows[0]; expect(header).toBeTruthy();
            const column = title => {const index = header.indexOf(title); expect(index, title).toBeGreaterThanOrEqual(0); return index;};
            const name = column('Informazioni intestatario'), state = column('Stato'), amount = column('Importo');
            const created = column('Data creazione'), paid = column('Data pagamento'), method = column('Modalità di pagamento');
            const data = rows.slice(1).filter(row => row[name]); expect(data).toHaveLength(expectedPayments.length);
            const actual = data.map(row => ({name: row[name], amount: Number(row[amount]), state: row[state],
                created: row[created], paid: row[paid] || '', method: row[method]})).sort((a, b) => a.name.localeCompare(b.name));
            const expected = expectedPayments.map(payment => ({name: `${payment.associate.first_name} ${payment.associate.last_name}`,
                amount: Number(payment.amount), state: payment.paid ? 'Pagato' : 'In Attesa',
                created: payment.creation_date.slice(0, 10), paid: payment.payment_date?.slice(0, 10) || '',
                method: payment.type === 'default' ? '-' : payment.type})).sort((a, b) => a.name.localeCompare(b.name));
            expect(actual).toEqual(expected);
            if (pettyCash) {
                const balance = column('Saldo progressivo');
                for (const row of data) expect(Number.isFinite(Number(row[balance]))).toBe(true);
            }
            return data.length;
        };
        const exportFile = async (currentPage, action, name, format, expectedPayments, predicate, pettyCash = false) => {
            // Attach all waiters immediately: a missing control must reach the
            // scenario failure report instead of leaving an unhandled waiter.
            const [received, download] = await Promise.all([currentPage.waitForResponse(response => {
                const url = new URL(response.url());
                return url.pathname === '/api/payment/list/export' && response.request().method() === 'GET'
                    && predicate(url.searchParams);
            }), currentPage.waitForEvent('download'), action()]);
            expect(received.status()).toBe(200);
            const payload = (await received.json()).data; expect(payload.type).toBe(format);
            const bytes = await persistDownload(download, `${name}.${format === 'csv' ? 'csv' : 'xlsx'}`, Buffer.from(payload.file, 'base64'));
            const rows = format === 'csv' ? csvRows(bytes.toString('utf8')) : await excelRows(currentPage, bytes);
            return validateRows(rows, expectedPayments, pettyCash);
        };
        // The tooltip shim moves title to data-original-title after mounting.
        const tooltipButton = (currentPage, title) => currentPage.locator(`button:is([title="${title}"],[data-original-title="${title}"]), button[data-original-title="${title}"], button:has(:is([title="${title}"],[data-original-title="${title}"])), button:has([data-original-title="${title}"])`);
        try {
            await open('Pagamenti', '/#/payment/list');
            const rows = page.locator('[data-row]'); await expect(rows).toHaveCount(3);
            await take('export-baseline-payments');
            const search = page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}); await search.fill('Sara');
            await expect(rows).toHaveCount(1); await expect(rows.first()).toContainText('Sara Conti');
            const filtered = baseline.filter(payment => payment.associate.first_name === 'Sara'); expect(filtered).toHaveLength(1);
            const menu = async currentPage => {
                await currentPage.getByRole('button', {name: 'Esporta', exact: true}).click();
                const options = currentPage.locator('.dropdown-menu:visible').filter({hasText: "CSV (dall'inizio)"});
                await expect(options).toBeVisible(); return options;
            };
            let options = await menu(page); await take('general-export-options', options);
            for (const [label, format, key, filename] of [
                ["CSV (dall'inizio)", 'csv', 'general_csv_rows', 'generale'],
                ["Excel (dall'inizio)", 'xlsx', 'general_excel_rows', 'generale'],
            ]) {
                if (!await options.isVisible()) options = await menu(page);
                proof(key, await exportFile(page, () => options.getByText(label, {exact: true}).click(), filename, format, baseline,
                    query => query.get('m') === format && !query.has('query[generalSearch]') && !query.has('export_type')));
            }
            if (await options.isVisible()) await page.getByRole('button', {name: 'Esporta', exact: true}).click();
            await take('filtered-payment-export');
            proof('filtered_excel_rows', await exportFile(page,
                () => tooltipButton(page, 'Esporta ricerca corrente in Excel').click(), 'ricerca-sara', 'xlsx', filtered,
                query => query.get('m') === 'xlsx' && query.get('query[generalSearch]') === 'Sara' && !query.has('export_type')));
            const reference = new Date(input.reference_date + 'T12:00:00Z');
            const period = month => {
                const start = new Date(Date.UTC(reference.getUTCFullYear(), reference.getUTCMonth() + month, 1));
                const end = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0));
                const format = date => date.toISOString().slice(0, 10).replaceAll('-', '/');
                return `${format(start)} al ${format(end)}`;
            };
            const pettyMenu = async () => {
                await page.getByRole('button', {name: 'Prima nota', exact: true}).click();
                const options = page.locator('.dropdown-menu:visible').filter({hasText: 'Periodo selezionato'});
                await expect(options).toBeVisible(); return options;
            };
            const paidBaseline = baseline.filter(payment => payment.paid);
            expect(paidBaseline).toHaveLength(2);
            options = await pettyMenu(); await take('petty-cash-period-options', options);
            for (const [label, key, range, expectedPayments] of [
                ["Dall'inizio", 'petty_cash_all_rows', null, paidBaseline],
                ['Mese corrente', 'petty_cash_current_rows', period(0), paidBaseline],
                ['Mese scorso', 'petty_cash_previous_rows', period(-1), []],
                ['Periodo selezionato', 'petty_cash_selected_rows', period(0), paidBaseline],
            ]) {
                if (!await options.isVisible()) options = await pettyMenu();
                proof(key, await exportFile(page, () => options.getByText(label, {exact: true}).click(), key, 'xlsx', expectedPayments,
                    query => query.get('export_type') === 'petty_cash_book' && query.get('m') === 'xlsx'
                        && (range === null ? !query.has('query[payment_range]') : query.get('query[payment_range]') === range), true));
            }
            if (await options.isVisible()) await page.getByRole('button', {name: 'Prima nota', exact: true}).click();
            proof('general_exports_ignore_search', true); proof('filtered_export_matches_search', true);
            proof('exports_match_backend_values', true); proof('exports_preserve_payments', JSON.stringify(sorted(await payments())) === JSON.stringify(baseline));
            await take('downloaded-exports-state-preserved');
            const reader = await actor('reader'); await reader.open('Pagamenti', '/#/payment/list');
            await expect(reader.page.locator('[data-row]')).toHaveCount(3);
            const readerPreferences = await reader.page.evaluate(() => JSON.parse(localStorage.getItem('tablesSettings') || '{}'));
            await reader.page.getByRole('textbox', {name:'Cerca nella tabella',exact:true}).fill('Sara');
            await expect(reader.page.locator('[data-row]')).toHaveCount(1);
            proof('reader_export_rows', await exportFile(reader.page,
                () => tooltipButton(reader.page, 'Esporta ricerca corrente in Excel').click(), 'lettura-sara', 'xlsx', filtered,
                query => query.get('query[generalSearch]') === 'Sara' && query.get('m') === 'xlsx'));
            await take('reader-filtered-export', undefined, reader.page);
            // Restore each actor through the table's real clear control before
            // exercising preferences against the complete baseline table.
            for (const currentPage of [page, reader.page]) {
                const [cleared] = await Promise.all([currentPage.waitForResponse(response => {
                    const url = new URL(response.url());
                    return url.pathname === '/api/payment/list' && response.request().method() === 'GET'
                        && !url.searchParams.get('query[generalSearch]');
                }), currentPage.getByRole('button', {name: 'Cancella ricerca', exact: true}).click()]);
                expect(cleared.status()).toBe(200);
                await expect(currentPage.getByRole('textbox', {name: 'Cerca nella tabella', exact: true})).toHaveValue('');
                await expect(currentPage.locator('[data-row]')).toHaveCount(3);
            }
            const header = currentPage => currentPage.locator('.datatable-head [data-field="type"]');
            const chooser = async () => {
                await tooltipButton(page, 'Mostra / nascondi colonne tabella').click();
                const picker = page.locator('.dropdown-menu:visible').filter({has: page.getByText('Metodo', {exact: true})});
                await expect(picker).toBeVisible(); return picker;
            };
            const toggleMethod = async picker => {
                const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/profile/settings/tables'
                    && response.request().method() === 'POST');
                preferencesChanged = true;
                await picker.locator('.dropdown-item').filter({hasText: /^\s*Metodo\s*$/}).click();
                expect((await response).status()).toBe(200);
            };
            let picker = await chooser();
            if (!await picker.locator('.dropdown-item').filter({hasText: /^\s*Metodo\s*$/}).locator('input').isChecked()) {
                await toggleMethod(picker); await search.click(); picker = await chooser();
            }
            await expect(header(page)).toBeVisible(); await take('payment-columns-picker', picker);
            await toggleMethod(picker); await expect(header(page)).toHaveCount(0);
            const localPreferences = await page.evaluate(() => JSON.parse(localStorage.getItem('tablesSettings')));
            proof('column_hidden_locally', localPreferences.payments.find(column => column.title === 'Metodo').checked === false);
            proof('column_saved_server', (await preferences()).payments.find(column => column.title === 'Metodo').checked === false);
            await search.click(); await take('payment-method-column-hidden');
            await open('Attività', '/#/course/list'); await open('Pagamenti', '/#/payment/list');
            await page.reload(); await expect(rows).toHaveCount(3); await expect(header(page)).toHaveCount(0);
            proof('column_hidden_after_reopen', await header(page).count() === 0); await take('payment-column-reopened');
            await reader.page.reload(); await expect(reader.page.locator('[data-row]')).toHaveCount(3);
            const readerMethod = readerPreferences?.payments?.find(column => column.title === 'Metodo')?.checked ?? true;
            if (readerMethod) await expect(header(reader.page)).toBeVisible(); else await expect(header(reader.page)).toHaveCount(0);
            const ownerPreferencesBeforeDenial = await preferences();
            proof('reader_preferences_write_status', (await reader.api('profile/settings/tables', {method: 'POST', data: {
                tables_settings: {...readerPreferences, payments: [{title: 'Metodo', checked: !readerMethod}]},
            }})).status());
            proof('reader_preferences_preserved',
                JSON.stringify(await reader.page.evaluate(() => JSON.parse(localStorage.getItem('tablesSettings') || '{}'))) === JSON.stringify(readerPreferences)
                && JSON.stringify(await preferences()) === JSON.stringify(ownerPreferencesBeforeDenial));
            await take('reader-columns-preserved', undefined, reader.page);
            expect((await api('profile/settings/tables', {method: 'POST', data: {tables_settings: originalPreferences}})).status()).toBe(200);
            await page.evaluate(value => localStorage.setItem('tablesSettings', JSON.stringify(value)), originalPreferences);
            proof('column_preferences_restored', JSON.stringify(await preferences()) === JSON.stringify(originalPreferences)); preferencesChanged = false;
            await page.reload(); await expect(rows).toHaveCount(3);

            // Create an owned course and explicit two-rate subscription through the real services.
            // This prepares a linked payment/receipt so the entire deletion guide is exercised.
            const title = 'Laboratorio dimostrativo cancellazione quota';
            const courseBefore = (await read('course/list?all=1')).data;
            expect(courseBefore.filter(course => course.title === title)).toHaveLength(0);
            const next = new Date(reference); next.setUTCDate(next.getUTCDate() + 7);
            const events = [{id: 0, amount: '30.00', payment_date: input.reference_date.split('-').reverse().join('/')},
                {id: 1, amount: '20.00', payment_date: next.toISOString().slice(0, 10).split('-').reverse().join('/')}];
            expect((await api('course/add', {method: 'POST', data: {new_course: {
                title, description: 'Preparazione reversibile della prova di cancellazione.', fee: '50.00',
                course_type: 1, multi_payments_split: true, events,
            }, subscriptions: []}})).status()).toBe(200);
            const createdCourse = (await read('course/list?all=1')).data.find(course => course.title === title);
            expect(createdCourse).toBeTruthy(); ownedCourse = createdCourse.course_id;
            const added = await api('course-subscriptions/add', {method: 'POST', data: [{course: ownedCourse,
                subscription_id: input.subscription_ids[0], events, multi_payments: true}]});
            expect(added.status()).toBe(201);
            const memberships = async () => (await read(`course-subscriptions/list?course_id=${ownedCourse}`)).data;
            const membership = (await memberships())[0]; expect(membership.installments).toHaveLength(2);
            const preparedPayments = (await payments()).filter(payment => !input.payment_ids.includes(payment.payment_id));
            expect(preparedPayments).toHaveLength(2);
            for (const payment of preparedPayments) {
                expect(payment.meta.course_id).toBe(ownedCourse); ownedPayments.add(payment.payment_id);
            }
            const target = preparedPayments.find(payment => Number(payment.amount) === 30);
            const control = preparedPayments.find(payment => Number(payment.amount) === 20);
            expect(target).toBeTruthy(); expect(control).toBeTruthy();
            const targetRow = currentPage => currentPage.locator(`#action-col-${target.payment_id}`).locator('xpath=ancestor::*[@data-row][1]');
            await page.reload(); await expect(targetRow(page)).toBeVisible();
            await targetRow(page).locator('button:is([title="Segna come pagato"],[data-original-title="Segna come pagato"])').click();
            const approval = page.getByRole('dialog', {name: 'Incassare il pagamento?', exact: true});
            await expect(approval.locator('input[id^="generate_invoice_"]')).toBeChecked();
            await expect(approval.locator('input[id^="send_receipt_email_"]')).not.toBeChecked();
            await approval.locator('input[name="payment_date"]').fill(input.reference_date);
            const approved = page.waitForResponse(response => new URL(response.url()).pathname === `/api/payment/${target.payment_id}/approve`
                && response.request().method() === 'POST');
            await approval.getByRole('button', {name: 'Incassa', exact: true}).click();
            const approvalResponse = await approved; expect(approvalResponse.status()).toBe(200);
            const paid = (await approvalResponse.json()).data.payment; expect(paid.paid).toBe(true);
            ownedReceipt = paid.invoice.invoice_id;
            const invoice = async () => (await read('invoice/list?query[invoice_id]=' + ownedReceipt)).data.invoice;
            await expect.poll(async () => Boolean((await invoice()).document_pdf), {timeout: 90000}).toBe(true);
            const beforeReceipt = await invoice(); expect(beforeReceipt.cancelled).toBe(false);
            const details = page.locator('.drawer').filter({hasText: 'Dettagli Pagamento'});
            await expect(details).toBeVisible(); await details.locator('button.close').click(); await expect(details).not.toBeVisible();
            await page.reload(); await expect(targetRow(page).locator(`button:is([title="Scarica Ricevuta n.${beforeReceipt.number}"],[data-original-title="Scarica Ricevuta n.${beforeReceipt.number}"]),button[data-original-title="Scarica Ricevuta n.${beforeReceipt.number}"]`)).toBeVisible();
            await take('owned-linked-payment-before-deletion');
            const beforeDelete = (await payments()).find(payment => payment.payment_id === target.payment_id);
            const beforePlan = (await memberships())[0];
            await targetRow(page).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
            const confirmation = page.getByRole('dialog', {name: 'Vuoi eliminare il pagamento?', exact: true});
            await expect(confirmation).toContainText("Sarà annullata l'eventuale ricevuta associata.");
            await take('payment-deletion-cancel-confirmation', confirmation);
            await confirmation.getByRole('button', {name: 'Annulla', exact: true}).click(); await expect(confirmation).not.toBeVisible();
            proof('cancellation_preserves_payment', JSON.stringify((await payments()).find(payment => payment.payment_id === target.payment_id)) === JSON.stringify(beforeDelete));
            await reader.page.reload(); await expect(targetRow(reader.page)).toBeVisible();
            await expect(targetRow(reader.page).locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
            proof('reader_payment_delete_status', (await reader.api(`payment/${target.payment_id}/delete`, {method: 'DELETE'})).status());
            proof('reader_denial_preserves_payment', JSON.stringify((await payments()).find(payment => payment.payment_id === target.payment_id)) === JSON.stringify(beforeDelete));
            await targetRow(page).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click(); await expect(confirmation).toBeVisible();
            await take('payment-deletion-confirmation', confirmation);
            const deleted = page.waitForResponse(response => new URL(response.url()).pathname === `/api/payment/${target.payment_id}/delete`
                && response.request().method() === 'DELETE');
            await confirmation.getByRole('button', {name: 'Elimina', exact: true}).click(); expect((await deleted).status()).toBe(200);
            await expect(confirmation).not.toBeVisible(); await page.reload(); await expect(targetRow(page)).toHaveCount(0);
            proof('payment_deleted_after_reload', !(await payments()).some(payment => payment.payment_id === target.payment_id)); ownedPayments.delete(target.payment_id);
            proof('remaining_payment_preserved', JSON.stringify((await payments()).find(payment => payment.payment_id === control.payment_id)) === JSON.stringify(control));
            await take('deleted-payment-absent-after-reload');
            const cancelled = await invoice(); proof('receipt_cancelled', cancelled.cancelled === true);
            expect(cancelled.meta.payment_id).toBe(target.payment_id); expect(Number(cancelled.meta.amount)).toBe(30);
            await expect.poll(async () => (await invoice()).document_pdf, {timeout: 90000}).not.toBe(beforeReceipt.document_pdf);
            const newReceipt = await invoice(); expect(newReceipt.document_pdf).toBeTruthy(); proof('receipt_pdf_regenerated', newReceipt.document_pdf !== beforeReceipt.document_pdf);
            const pdf = await api(`document/retrieve/${newReceipt.document_pdf}?download=true&token=${newReceipt.document_token}`);
            expect(pdf.status()).toBe(200); expect(pdf.headers()['content-type']).toContain('application/pdf');
            const pdfBytes = await pdf.body(); expect(pdfBytes.subarray(0, 5).toString()).toBe('%PDF-');
            await page.getByText('Documenti fiscali', {exact: true}).first().click(); await page.getByText('Ricevute', {exact: true}).first().click();
            await expect(page).toHaveURL(/#\/invoice\/list$/);
            await page.getByRole('textbox', {name: 'Anno ricevute', exact: true}).click(); await page.getByText('Filtra Anno', {exact: true}).click();
            const receiptRow = page.locator('[data-row]').filter({has: page.locator(`button:is([title="Ricevuta n.${newReceipt.number}"],[data-original-title="Ricevuta n.${newReceipt.number}"]),button[data-original-title="Ricevuta n.${newReceipt.number}"]`)});
            await expect(receiptRow).toContainText('ricevuta annullata e pagamento eliminato'); await take('linked-receipt-cancelled');
            const fetched = page.waitForResponse(response => new URL(response.url()).pathname === `/api/document/retrieve/${newReceipt.document_pdf}`);
            await receiptRow.locator(`button:is([title="Ricevuta n.${newReceipt.number}"],[data-original-title="Ricevuta n.${newReceipt.number}"]),button[data-original-title="Ricevuta n.${newReceipt.number}"]`).click();
            const preview = page.getByRole('dialog', {name: `Ricevuta n.${newReceipt.number}`, exact: true});
            const frame = preview.locator('iframe'); await expect(frame).toBeVisible();
            const viewed = await fetched; expect(viewed.status()).toBe(200);
            expect(viewed.headers()['content-type']).toContain('application/pdf');
            const previewSource = new URL(await frame.getAttribute('src'), input.origin);
            expect(previewSource.origin).toBe(input.origin);
            expect(previewSource.pathname).toBe('/api/document/retrieve/' + newReceipt.document_pdf);
            expect(previewSource.searchParams.get('download')).toBe('false');
            const actualFile = await page.context().request.get(previewSource.href);
            expect(actualFile.status()).toBe(200);
            expect(sha(await actualFile.body())).toBe(sha(pdfBytes));
            await take('regenerated-cancelled-receipt-pdf', frame);
            const downloadPage = await page.context().newPage();
            // The capability remains only in this local expression and browser memory.
            const downloaded = await navigateToDownload(downloadPage,
                input.origin + `/api/document/retrieve/${newReceipt.document_pdf}?download=true&token=${newReceipt.document_token}`, 'Cancelled receipt');
            await persistDownload(downloaded, 'ricevuta-annullata.pdf', pdfBytes); await downloadPage.close();
            proof('receipt_pdf_downloaded_and_previewed', true);
            await preview.getByRole('button', {name: 'Chiudi', exact: true}).first().click(); await expect(preview).not.toBeVisible();
            const afterPlan = (await memberships())[0];
            const planValues = membership => membership.installments.map(rate => ({id: rate.id,
                uuid: rate.course_subscription_installment_id, amount: Number(rate.amount), date: rate.payment_date})).sort((a, b) => a.id - b.id);
            expect(planValues(afterPlan)).toEqual(planValues(beforePlan).filter(rate => rate.id !== 0));
            expect(afterPlan.installments).toHaveLength(1);expect(afterPlan.installments[0].paid).toBe(false);
            proof('linked_installment_removed_other_rows_preserved', true);
            await open('Attività', '/#/course/list'); await page.locator('[data-row]').filter({hasText: title}).getByText(title, {exact: true}).click();
            await page.reload(); const athlete = page.locator('[data-row]').filter({hasText: 'GIULIA BIANCHI'});
            await expect(athlete).toBeVisible(); await athlete.locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
            const plan = page.locator('#subscription-modal'); await expect(plan).toBeVisible();
            expect(JSON.parse(await plan.locator('input[name="selectedEvents"]').inputValue())).toHaveLength(1);
            await take('installment-plan-after-payment-deletion', plan.locator('.modal-content'));
            await plan.getByRole('button', {name: 'Annulla', exact: true}).click();
            proof('unrelated_payments_preserved', JSON.stringify(sorted((await payments()).filter(payment => input.payment_ids.includes(payment.payment_id)))) === JSON.stringify(baseline));
        } finally {
            // Never infer owned IDs from names during cleanup and never mutate fixture payments.
            for (const paymentId of ownedPayments) expect((await api(`payment/${paymentId}/delete`, {method: 'DELETE'})).status()).toBe(200);
            if (ownedReceipt) expect((await api(`invoice/${ownedReceipt}/delete`, {method: 'POST', data: {}})).status()).toBe(200);
            if (ownedCourse) expect((await api(`course/${ownedCourse}/delete`, {method: 'POST'})).status()).toBe(200);
            if (preferencesChanged) {
                expect((await api('profile/settings/tables', {method: 'POST', data: {tables_settings: originalPreferences}})).status()).toBe(200);
                await page.evaluate(value => localStorage.setItem('tablesSettings', JSON.stringify(value)), originalPreferences);
            }
        }
        proof('owned_runtime_objects_removed', JSON.stringify(sorted(await payments())) === JSON.stringify(baseline)
            && !(await read('course/list?all=1')).data.some(course => course.course_id === ownedCourse));
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.checks.push('CSV and Excel bytes downloaded via real UI match backend payload and parsed values',
            'general, current search and all Prima nota periods retain distinct scopes',
            'read-only collaborator exports but cannot persist table preferences or delete a payment',
            'owner column visibility persists locally, server-side and after reopening; original preferences restored',
            'single payment deletion can be cancelled and then persists with cancelled receipt, regenerated real PDF and separate rate-plan inspection',
            'only owned IDs are removed; fixture records remain unchanged');
        report.limits = ['no collective deletion or online refund exercised', 'email delivery is disabled',
            'course creation/assignment APIs are fixture preparation, not a captured creation procedure'];
    },
});
