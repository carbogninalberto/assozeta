// Prepared real-backend recipe. It neither mocks responses nor asserts email delivery.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import crypto from 'node:crypto';
import {inflateRawSync} from 'node:zlib';
import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {medicalFollowupsAuthoredWorkflows} from '../../../../docs/manuale/medical-followups-authored-workflows.mjs';
const id = 'medical-followups';
const spec = medicalFollowupsAuthoredWorkflows[id];
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
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
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const facts = {};
        const proof = (key, value) => {expect(value, key).toEqual(spec.outcome.expected[key]); facts[key] = value;};
        const read = async (route, request = api) => {
            const response = await request(route); expect(response.status()).toBe(200); return response.json();
        };
        const values = data => Object.values(data);
        const subscriptions = async () => values((await read('subscription/list?pagination[perpage]=100')).data);
        const payments = async () => values((await read('payment/list?pagination[perpage]=100')).data);
        const sort = (rows, key) => [...rows].sort((a, b) => a[key].localeCompare(b[key]));
        const baselineSubscriptions = sort(await subscriptions(), 'subscription_id');
        const baselinePayments = sort(await payments(), 'payment_id');
        expect(baselineSubscriptions.map(row => row.subscription_id).sort()).toEqual([...input.subscription_ids].sort());
        expect(baselinePayments.map(row => row.payment_id).sort()).toEqual([...input.payment_ids].sort());
        const originalSettings = (await read('profile/settings')).settings;
        const originalLayout = (await read('profile/info')).user_data.dashboard_layout;
        const fixture = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'fixtures/documento-medico-demo.pdf');
        const fixtureBytes = fs.readFileSync(fixture);
        const day = offset => {
            const date = new Date(input.reference_date + 'T12:00:00Z'); date.setUTCDate(date.getUTCDate() + offset);
            return date.toISOString().slice(0, 10);
        };
        const local = date => date.split('-').reverse().join('/');
        const owned = {subscription_ids: [], payment_ids: [], associate_ids: [], certificate_ids: [], document_ids: [],
            fixture_only: true, public_cleanup_finished: false};
        // Keep IDs out of the public evidence report. The disposable fixture owner
        // resolves removed wizard upload uids and cleans the standalone Associate.
        const manifest = path.join(process.env.ASSOZETA_MANUAL_RUN, 'private', id + '-owned.json');
        const saveOwned = () => {
            fs.mkdirSync(path.dirname(manifest), {recursive: true, mode: 0o700});
            fs.writeFileSync(manifest, JSON.stringify(owned, null, 2) + '\n', {mode: 0o600});
        };
        const remember = (key, value) => {if (value && !owned[key].includes(value)) {owned[key].push(value); saveOwned();}};
        saveOwned();
        proof('private_cleanup_manifest_recorded', true);
        let memberId;
        const info = async () => (await read(`subscription/${memberId}/info`)).data.info;
        const rememberInfo = value => {
            remember('certificate_ids', value.medical); remember('document_ids', value.medical_document);
            return value;
        };
        const take = async (checkpoint, focus, currentPage = page) => {
            const index = spec.checkpoints.findIndex(item => item.id === checkpoint);
            expect(index).toBeGreaterThanOrEqual(0);
            await expect(currentPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await expect(currentPage.locator('.datatable-loading:visible')).toHaveCount(0);
            await capture(currentPage, index + 1, checkpoint, focus);
        };
        const responseFor = (route, browserPage = page) => browserPage.waitForResponse(response =>
            new URL(response.url()).pathname === '/api/' + route && response.request().method() === 'POST');
        const openMedical = async (browserPage = page, navigate = open) => {
            await navigate('Organizzazione', '/#/members/list');
            const row = browserPage.locator('[data-row]').filter({hasText: 'Marta Neri'});
            await expect(row).toBeVisible(); await row.getByText('Marta Neri', {exact: true}).first().click();
            const drawer = browserPage.getByRole('dialog', {name: 'Marta Neri', exact: true});
            await expect(drawer.locator('#subscription_form')).toBeVisible();
            await drawer.locator('.nav-text').getByText('Certificato medico', {exact: true}).click();
            return drawer;
        };
        const checkDocument = async value => {
            expect(value.medical_document).toBeTruthy(); expect(value.medical_token).toBeTruthy();
            const response = await api(`document/retrieve/${value.medical_document}?download=true&token=${encodeURIComponent(value.medical_token)}`);
            expect(response.status()).toBe(200);
            expect(sha(await response.body())).toBe(sha(fixtureBytes));
            const preview = page.locator('a:is([title="Anteprima certificato medico"],[data-original-title="Anteprima certificato medico"]), a[data-original-title="Anteprima certificato medico"]');
            await expect(preview).toBeVisible();
            await expect(preview).toHaveAttribute('href', new RegExp(value.medical_document));
            await expect(preview).toHaveAttribute('target', '_blank');
            const previewResponse = await page.request.get(new URL(await preview.getAttribute('href'), input.origin).href);
            expect(previewResponse.status()).toBe(200);
            expect(sha(await previewResponse.body())).toBe(sha(fixtureBytes));
            return true;
        };
        const setExpiration = async date => {
            expect((await api(`subscription/${memberId}/medical-certificate/set-certificate-expiration`,
                {method: 'POST', data: {subscription_id: memberId, certificate_expiring_date: date ? local(date) : null}})).status()).toBe(200);
            if (date) expect((await info()).medical_expiration_date).toBe(date);
            else expect((await info()).medical).toBeNull();
        };
        try {
            // A new adult member belongs to this scenario; fixture registrations stay untouched.
            await open('Organizzazione', '/#/members/list');
            await expect(page.locator('[data-row]')).toHaveCount(3);
            await page.getByRole('link', {name: 'Aggiungi', exact: true}).click();
            const active = () => page.locator('[data-wizard-type="step-content"][data-wizard-state="current"]');
            const next = async heading => {
                await page.locator('[data-wizard-type="action-next"]').click(); await expect(active()).toContainText(heading);
            };
            await expect(page.locator('input[name="new_account_radio"][value="false"]')).toBeChecked();
            await next("Inserisci le informazioni dell'Associato");
            for (const [name, value] of Object.entries({firstNameAssociate: 'Marta', lastNameAssociate: 'Neri',
                bornCityAssociate: 'Roma', addressAssociate: 'Via delle Attività 4', addressCityAssociate: 'Roma',
                capAssociate: '00100', emailAssociate: 'marta@example.test'}))
                await active().locator(`[name="${name}"]`).fill(value);
            await page.locator('input[name="bornDateAssociate"]').fill('2000-01-01');
            await expect(page.locator('input[name="bornDateAssociate"]')).toHaveValue('2000-01-01');
            const tax = active().locator('[name="taxCodeAssociate"]');
            const taxResponse = responseFor('subscription/calculate-tax-code');
            await tax.locator('..').getByRole('button').click();
            expect((await taxResponse).status()).toBe(200); await expect(tax).toHaveValue(/^[A-Z0-9]{16}$/);
            await next('Firma del documento');
            const canvas = active().locator('canvas'); await canvas.scrollIntoViewIfNeeded();
            await expect.poll(() => canvas.evaluate(element => element.width > 0 && element.height > 0)).toBe(true);
            const box = await canvas.boundingBox();
            const x = box.x + box.width * .18, y = box.y + box.height * .5;
            await page.mouse.move(x, y); await page.mouse.down();
            for (let n = 1; n <= 32; n++) await page.mouse.move(x + n * box.width * .018, y + Math.sin(n * .6) * box.height * .15);
            await page.mouse.up();
            await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('signature') || '{}').there_is_signature)).toBe(true);
            await next('Hai già un certificato medico?');
            await take('wizard-medical-step-correct-person', active());
            let uploadRequests = 0;
            const countUpload = request => {if (new URL(request.url()).pathname === '/api/document/medical/certificate/'
                && request.method() === 'POST') uploadRequests++;};
            page.on('request', countUpload);
            await active().locator('input[type="file"]').setInputFiles({name: 'documento-non-ammesso.txt', mimeType: 'text/plain', buffer: Buffer.from('Dimostrazione')});
            await expect(active().getByRole('alert')).toContainText('Seleziona un file PDF');
            proof('wizard_invalid_file_did_not_upload', uploadRequests === 0);
            await take('wizard-invalid-file-rejected', active());
            await active().locator('#expiring_certificate input').fill(day(10));
            const uploadWizard = async () => {
                const pending = responseFor('document/medical/certificate/');
                await active().locator('input[type="file"]').setInputFiles(fixture);
                const response = await pending; expect(response.status()).toBe(200);
                const result = await response.json(); expect(result.uid).toBeTruthy(); remember('certificate_ids', result.uid);
                expect(result.expiring_date).toBeNull();
                await expect(active().getByText('Documento caricato: documento-medico-demo.pdf', {exact: true})).toBeVisible();
                await expect(active().locator('#expiring_certificate input')).toHaveValue(day(10));
                return result.uid;
            };
            await uploadWizard(); await take('wizard-real-upload-and-expiration', active());
            await active().getByRole('button', {name: 'Rimuovi certificato selezionato', exact: true}).click();
            await expect(active().getByText('Documento caricato: documento-medico-demo.pdf', {exact: true})).toHaveCount(0);
            await expect(active().locator('#expiring_certificate input')).toHaveValue(day(10));
            expect(await page.evaluate(() => JSON.parse(localStorage.getItem('medicalCertificate') || '{}').medical_id)).toBeNull();
            proof('wizard_removed_attachment_preserved_date', true);
            await take('wizard-removed-file-preserves-expiration', active());
            const wizardCertificate = await uploadWizard(); page.off('request', countUpload);
            await next('Ancora un ultimo passo'); await expect(active()).toContainText('Marta'); await expect(active()).toContainText('Neri');
            await take('wizard-summary-before-submission', active());
            const creation = responseFor('subscription/add'); await page.locator('#subscription_submit').click();
            await page.locator('.swal2-popup').getByRole('button', {name: 'Continua', exact: true}).click();
            const createdResponse = await creation; expect(createdResponse.status()).toBe(200);
            const submitted = createdResponse.request().postDataJSON();
            expect(submitted.medical_certificate.medical_id).toBe(wizardCertificate);
            expect(submitted.medical_certificate.certificate_expring_date).toBe(day(10));
            proof('wizard_submitted_actual_certificate_and_expiration', true);
            const created = await createdResponse.json(); expect(created.status).toBe('success');
            remember('payment_ids', created.payment_id);
            const ownedMember = (await subscriptions()).find(row => row.associate.first_name === 'Marta' && row.associate.last_name === 'Neri');
            expect(ownedMember).toBeTruthy(); memberId = ownedMember.subscription_id;
            remember('subscription_ids', memberId); remember('associate_ids', ownedMember.associate.associate_id);
            expect(ownedMember.type).toBe(2); expect(ownedMember.status_flag).toBe(2);
            expect(ownedMember.user.user_id).toBe(input.user_id);
            expect(ownedMember.payment.paid).toBe(false);
            await page.reload(); let drawer = await openMedical();
            let saved = rememberInfo(await info()); expect(saved.medical).toBe(wizardCertificate);
            expect(saved.medical_expiration_date).toBe(day(10)); await expect(drawer).toContainText(local(day(10)));
            proof('wizard_document_download_matches_fixture', await checkDocument(saved));
            proof('wizard_document_and_date_reopened', true); await take('wizard-certificate-after-reload', drawer);

            // Reset only this owned attachment to exercise Nuovo Certificato as authored.
            await setExpiration(null); await page.reload(); drawer = await openMedical();
            await expect(drawer.getByRole('button', {name: 'Nuovo Certificato', exact: true})).toBeVisible();
            await take('profile-medical-empty-before-upload', drawer);
            const uploadProfile = async (actionName, name, date, checkpoint) => {
                await drawer.getByRole('button', {name: actionName, exact: true}).click();
                const modal = page.locator('#add-medical-certificate-modal'); await expect(modal).toBeVisible();
                await expect(modal.getByRole('button', {name: 'Salva', exact: true})).toBeDisabled();
                const pending = responseFor(`subscription/${memberId}/medical-certificate/upload`);
                await modal.locator('input[type="file"]').setInputFiles({name, mimeType: 'application/pdf', buffer: fixtureBytes});
                const response = await pending; expect(response.status()).toBe(200);
                const result = await response.json(); expect(result.medical).toBeTruthy();
                const attached = rememberInfo(await info()); expect(attached.medical_document).toBe(result.medical);
                expect(attached.medical_expiration_date).toBeNull();
                await expect(modal.getByText('Documento caricato: ' + name, {exact: true})).toBeVisible();
                await modal.locator('input[name="expiring_date"]').fill(date); await take(checkpoint, modal.locator('.modal-content'));
                const saving = responseFor(`subscription/${memberId}/medical-certificate/set-certificate-expiration`);
                await modal.getByRole('button', {name: 'Salva', exact: true}).click();
                const savedResponse = await saving; expect(savedResponse.status()).toBe(200);
                expect(savedResponse.request().postDataJSON().certificate_expiring_date).toBe(local(date));
                await expect(modal).toHaveCount(0); expect((await info()).medical_expiration_date).toBe(date);
                return attached;
            };
            const first = await uploadProfile('Nuovo Certificato', 'documento-medico-demo.pdf', day(10), 'profile-upload-confirmation-before-save');
            proof('profile_upload_attaches_before_date_save', true);
            await page.reload(); drawer = await openMedical(); saved = rememberInfo(await info());
            expect(saved.medical_document).toBe(first.medical_document); await expect(drawer).toContainText(local(day(10)));
            proof('profile_document_download_matches_fixture', await checkDocument(saved));
            proof('profile_document_and_date_reopened', true); await take('profile-saved-certificate-after-reload', drawer);
            const replacement = await uploadProfile('Sostituisci certificato', 'documento-medico-rinnovo.pdf', day(20), 'replacement-real-file-before-save');
            proof('replacement_changes_document_id', replacement.medical_document !== first.medical_document);
            expect(replacement.medical).not.toBe(first.medical);
            await page.reload(); drawer = await openMedical(); saved = rememberInfo(await info());
            expect(saved.medical_document).toBe(replacement.medical_document); await expect(drawer).toContainText(local(day(20)));
            proof('replacement_document_download_matches_fixture', await checkDocument(saved));
            proof('replacement_document_and_date_reopened', true); await take('replacement-saved-after-reload', drawer);
            await drawer.getByRole('button', {name: 'Modifica scadenza', exact: true}).click();
            const dateModal = page.locator('#add-medical-certificate-modal'); await expect(dateModal).toBeVisible();
            await expect(dateModal.locator('input[name="expiring_date"]')).toHaveValue(day(20));
            await expect(dateModal.locator('input[type="file"]')).toHaveCount(0);
            await take('date-only-editor-preserves-existing-date', dateModal.locator('.modal-content'));
            await dateModal.locator('input[name="expiring_date"]').fill(day(10));
            const dateSave = responseFor(`subscription/${memberId}/medical-certificate/set-certificate-expiration`);
            await dateModal.getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await dateSave).status()).toBe(200); await expect(dateModal).toHaveCount(0);
            await page.reload(); drawer = await openMedical(); const dated = await info();
            expect(dated.medical).toBe(replacement.medical); expect(dated.medical_document).toBe(replacement.medical_document);
            expect(dated.medical_expiration_date).toBe(day(10)); await expect(drawer).toContainText(local(day(10)));
            proof('date_only_preserves_document_and_certificate_ids', true); await take('date-only-saved-same-document', drawer);
            const classification = responseFor(`subscription/${memberId}/medical-certificate/edit`);
            await setCheckbox(drawer.locator('input[type="checkbox"]'),true); expect((await classification).status()).toBe(200);
            await page.reload(); drawer = await openMedical(); const classified = await info();
            expect(classified.medical_document).toBe(replacement.medical_document); expect(classified.medical_expiration_date).toBe(day(10));
            expect(classified.competitive_medical_certificate).toBe(true); await expect(drawer).toContainText('AGONISTICO');
            proof('competitive_classification_reopened', true); await take('competitive-classification-after-reload', drawer);
            const reader = await actor('reader'); const readOnly = await openMedical(reader.page, reader.open);
            proof('reader_registration_create_status', (await reader.api('subscription/add', {method: 'POST', data: submitted})).status());
            expect((await subscriptions()).map(row => row.subscription_id).sort()).toEqual([...input.subscription_ids, memberId].sort());
            await expect(readOnly.getByRole('button', {name: 'Sostituisci certificato', exact: true})).toHaveCount(0);
            await expect(readOnly.getByRole('button', {name: 'Modifica scadenza', exact: true})).toHaveCount(0);
            await expect(readOnly.locator('input[type="checkbox"]')).toBeDisabled();
            await take('reader-medical-write-controls-absent', readOnly, reader.page);
            const deniedUpload = await reader.api(`subscription/${memberId}/medical-certificate/upload`,
                {method: 'POST', multipart: {medical_certificate: {name: 'certificato-negato.pdf', mimeType: 'application/pdf', buffer: fixtureBytes}}});
            proof('reader_medical_upload_status', deniedUpload.status());
            proof('reader_medical_expiration_status', (await reader.api(`subscription/${memberId}/medical-certificate/set-certificate-expiration`,
                {method: 'POST', data: {certificate_expiring_date: local(day(1))}})).status());
            proof('reader_medical_classification_status', (await reader.api(`subscription/${memberId}/medical-certificate/edit`,
                {method: 'POST', data: {competitive_medical_certificate: false}})).status());
            expect(await info()).toEqual(classified); proof('reader_medical_denial_preserved_state', true);
            await drawer.locator('button.close').click(); await expect(drawer).not.toBeVisible();
            await readOnly.locator('button.close').click(); await expect(readOnly).not.toBeVisible();

            const notificationBlock = browserPage => browserPage.locator('.form-group').filter({
                has: browserPage.getByText('Invia notifiche certificati medici', {exact: true})});
            const openSettings = async (browserPage = page, navigate = open) => {
                await navigate('Impostazioni', '/#/profile'); await browserPage.getByText('Generali', {exact: true}).click();
                await expect(notificationBlock(browserPage)).toBeVisible();
            };
            await openSettings();
            const notifications = notificationBlock(page).locator('input[type="checkbox"]');
            // Ensure disabling causes a real dirty-state Save even when the initial fixture is off.
            if (!originalSettings.medical_certificate_notifications) {
                await setCheckbox(notifications, true); const enable = responseFor('profile/settings');
                await page.locator('#bkn_form_password_update_submit').click(); expect((await enable).status()).toBe(200);
                await page.reload(); await openSettings();
            }
            await expect(notifications).toBeChecked();
            await take('medical-notifications-before-disable', notificationBlock(page));
            await setCheckbox(notifications, false); const disable = responseFor('profile/settings');
            await page.locator('#bkn_form_password_update_submit').click(); expect((await disable).status()).toBe(200);
            await page.reload(); await openSettings(); await expect(notifications).not.toBeChecked();
            const disabledSettings = (await read('profile/settings')).settings;
            expect(disabledSettings).toEqual({...originalSettings, medical_certificate_notifications: false});
            proof('notifications_disabled_and_reopened', true);
            await take('medical-notifications-disabled-after-reload', notificationBlock(page));
            await openSettings(reader.page, reader.open);
            await expect(notificationBlock(reader.page).locator('input[type="checkbox"]')).toBeDisabled();
            await take('reader-medical-notifications-disabled-control', notificationBlock(reader.page), reader.page);
            proof('reader_notifications_status', (await reader.api('profile/settings', {method: 'POST',
                data: {...disabledSettings, medical_certificate_notifications: true}})).status());
            expect((await read('profile/settings')).settings).toEqual(disabledSettings);
            proof('reader_notifications_denial_preserved_settings', true);

            await open('Organizzazione', '/#/members/list');
            await expect(page.locator('[data-row]')).toHaveCount(4);
            const currentRow = page.locator('[data-row]').filter({hasText: 'Marta Neri'});
            await expect(currentRow.locator('[data-field="medical"]')).toContainText('10');
            for (const member of baselineSubscriptions)
                await expect(page.locator('[data-row]').filter({hasText: member.associate.first_name + ' ' + member.associate.last_name})
                    .locator('[data-field="medical"]')).toContainText(member.plain_medical_label || 'Mancante');
            await take('members-certificate-column-current-state', page.locator('.datatable-table').first());
            const dashboard = async () => {
                const link = page.locator('a[href="/#/"]').first();
                await expect(link).toBeVisible(); await link.click();
                await expect(page.getByRole('heading', {name: 'Bacheca', exact: true})).toBeVisible();
            };
            const widget = label => page.locator('section.min-w-100 > div').filter({
                has: page.locator('.dashboard-widget .card-label').filter({hasText: new RegExp('^' + label + '$')})});
            const expiringLabel = 'Certificati medici in scadenza', expiredLabel = 'Certificati medici scaduti';
            const widgetRows = async (name, field) => (await read(`statistic/dashboard?widget=${name}`)).data[field];
            const assertWidgets = async (expiry, expiresToday = false) => {
                const expiring = await widgetRows('expiringmedicalcertificates', 'expiring_medical_certificates');
                const expired = await widgetRows('expiredmedicalcertificates', 'expired_medical_certificates');
                const expiringIds = expiring.map(item => item.subscription_id), expiredIds = expired.map(item => item.subscription_id);
                expect(expiringIds.includes(memberId)).toBe(expiry >= input.reference_date && expiry <= day(30));
                expect(expiredIds.includes(memberId)).toBe(expiry < input.reference_date);
                for (const group of [expiring, expired]) {
                    expect(group.map(item => item.days_left)).toEqual(group.map(item => item.days_left).sort((a, b) => a - b));
                    const ownedRow = group.find(item => item.subscription_id === memberId);
                    if (ownedRow) expect(ownedRow.medical.expiration_date).toBe(expiry);
                }
                if (expiresToday) {
                    expect(expiring.find(item => item.subscription_id === memberId).days_left).toBe(0);
                    expect(expiredIds).not.toContain(memberId);
                }
                return {expiring, expired};
            };
            await dashboard(); await expect(widget(expiredLabel)).toHaveCount(0);
            await page.getByRole('button', {name: 'Modifica', exact: true}).click();
            await page.getByRole('button', {name: 'Aggiungi widget', exact: true}).click();
            const widgetModal = page.locator('#addWidget'); await expect(widgetModal).toBeVisible();
            await widgetModal.locator('.cursor-pointer').filter({hasText: expiredLabel}).click();
            await take('medical-dashboard-add-expired-widget', widgetModal.locator('.modal-content'));
            await widgetModal.getByRole('button', {name: 'Aggiungi Widget', exact: true, includeHidden: true}).click();
            const saveLayout = responseFor('statistic/dashboard/layout');
            await page.getByRole('button', {name: 'Salva', exact: true}).click(); expect((await saveLayout).status()).toBe(200);
            await page.reload(); await expect(widget(expiredLabel)).toBeVisible(); await expect(widget(expiringLabel)).toBeVisible();
            const layout = (await read('profile/info')).user_data.dashboard_layout;
            expect(layout.rows.flat().some(item => item.id === 'expiredmedicalcertificates')).toBe(true);
            expect(layout.rows.flat().some(item => item.id === 'expiringmedicalcertificates')).toBe(true);
            await assertWidgets(day(10)); await expect(widget(expiringLabel)).toContainText('MARTA');
            proof('both_medical_widgets_saved_and_reopened', true);
            await take('medical-dashboard-both-widgets-saved');
            await widget(expiringLabel).locator(`a[href$="/members/list/detail/${memberId}/medical"]`).click();
            // Widget native links navigate to the full Detail route; list rows use a drawer.
            await expect(page).toHaveURL(input.origin + '/#/members/list/detail/' + memberId + '/medical');
            const linkedDetail = page.locator('#bkn_content .container').filter({has: page.getByText('Informazioni certificato medico', {exact: true})});
            await expect(linkedDetail.locator('.card-title')).toContainText('Marta Neri');
            await expect(linkedDetail).toContainText(local(day(10)));
            const linkedInfo = await info();
            expect(linkedInfo.medical_document).toBe(replacement.medical_document);
            expect(linkedInfo.medical_expiration_date).toBe(day(10));
            proof('widget_opened_exact_person_and_date', true);
            await take('expiring-widget-opened-person-date', linkedDetail);

            const applyCertificateFilter = async (label, key, expectedIds) => {
                await open('Organizzazione', '/#/members/list');
                const reset = page.getByRole('button', {name: 'Ripristina filtri', exact: true});
                if (await reset.isVisible()) await reset.click();
                await expect(page.locator('[data-row]')).toHaveCount(4);
                await page.getByRole('button', {name: 'Altri filtri', exact: true}).click();
                const panel = page.locator('.checkbox-filter-panel:visible');
                const pending = page.waitForResponse(response => {
                    const url = new URL(response.url());
                    return url.pathname === '/api/subscription/list' && response.request().method() === 'GET'
                        && url.searchParams.get(`query[${key}]`) === '1';
                });
                await setCheckbox(panel.getByLabel(label, {exact: true}),true);
                const response = await pending; expect(response.status()).toBe(200);
                const result = await response.json();
                expect(values(result.data).map(row => row.subscription_id).sort()).toEqual([...expectedIds].sort());
                await expect(page.locator('[data-row]')).toHaveCount(expectedIds.length);
                await page.getByRole('button', {name: 'Altri filtri', exact: true}).click();
                return result;
            };
            await applyCertificateFilter('Certificato non presente', 'certificate_missing_flag', input.subscription_ids);
            proof('missing_filter_excludes_document_owner', true);
            await take('missing-certificate-filter-results', page.locator('.datatable-table').first());
            await setExpiration(day(-1));
            await applyCertificateFilter('Certificato scaduto', 'certificate_expired_flag', [memberId]);
            proof('expired_filter_includes_yesterday', true);
            await take('expired-certificate-filter-results', page.locator('.datatable-table').first());
            await dashboard(); await page.reload(); await assertWidgets(day(-1));
            await expect(widget(expiredLabel)).toContainText('MARTA');
            await take('expired-widget-current-owned-person', widget(expiredLabel));
            await setExpiration(input.reference_date);
            await applyCertificateFilter('Certificato scaduto', 'certificate_expired_flag', [memberId]);
            await dashboard(); await page.reload(); await assertWidgets(input.reference_date, true);
            await expect(widget(expiringLabel)).toContainText('MARTA');
            proof('today_list_expired_widget_expiring_distinction', true);
            await take('today-list-versus-widget-boundary', widget(expiringLabel));

            report.downloads = [];
            const storeDownload = async (download, format, label, expectedBytes) => {
                expect(await download.failure()).toBeNull(); const bytes = fs.readFileSync(await download.path());
                expect(sha(bytes)).toBe(sha(expectedBytes));
                const relative = `downloads/${id}/${label}.${format === 'excel' ? 'xlsx' : 'pdf'}`;
                const destination = path.join(process.env.ASSOZETA_MANUAL_RUN, relative);
                fs.mkdirSync(path.dirname(destination), {recursive: true}); fs.writeFileSync(destination, bytes);
                report.downloads.push({path: relative, sha256: sha(bytes), bytes: bytes.length}); return bytes;
            };
            const print = async (label, type, format, checkpoint, expiry) => {
                await widget(label).getByRole('button', {name: 'Stampa', exact: true}).click();
                const modal = page.locator('#printing-modal'); await expect(modal).toBeVisible();
                await expect(modal.getByText('Scegli il formato di stampa', {exact: true})).toBeVisible();
                const response = responseFor('printing/generate');
                await modal.getByRole('button', {name: format === 'pdf' ? 'PDF' : 'Excel', exact: true}).click();
                const result = await response; expect(result.status()).toBe(200);
                expect(result.request().postDataJSON()).toEqual({current_year: 1, format, type, filters: {}});
                const payload = await result.json(); expect(payload.filename).toContain(format === 'pdf' ? '.pdf' : '.xlsx');
                const bytes = Buffer.from(payload.file, 'base64');
                if (format === 'pdf') {
                    expect(bytes.subarray(0, 5).toString()).toBe('%PDF-'); expect(bytes.length).toBeGreaterThan(1000);
                    const frame = modal.locator('iframe'); await expect(frame).toBeVisible();
                    const preview = await frame.getAttribute('src');
                    expect(sha(Buffer.from(preview.split(',')[1], 'base64'))).toBe(sha(bytes));
                }
                const download = page.waitForEvent('download');
                await modal.getByRole('button', {name: 'Scarica file', exact: true}).click();
                const downloaded = await storeDownload(await download, format, type, bytes);
                if (format === 'excel') {
                    const rows = await excelRows(page, downloaded), headers = rows[0];
                    const field = name => {const index = headers.indexOf(name); expect(index, name).toBeGreaterThanOrEqual(0); return index;};
                    const data = rows.slice(1).filter(row => row[field('Nome')]);
                    expect(data).toHaveLength(1);
                    expect(data[0][field('Nome')]).toBe('Marta'); expect(data[0][field('Cognome')]).toBe('Neri');
                    expect(data[0][field('Email')]).toBe('marta@example.test');
                    expect(data[0][field('Scadenza certificato medico')]).toBe(local(expiry));
                    expect(Number(data[0][field('Giorni rimanenti')])).toBe(type === 'expiring_medical_certificates' ? 10 : -1);
                }
                await take(checkpoint, modal.locator('.modal-content'));
                await modal.getByRole('button', {name: 'Chiudi', exact: true}).first().click(); await expect(modal).toHaveCount(0);
                return true;
            };
            await setExpiration(day(10)); await dashboard(); await page.reload(); await assertWidgets(day(10));
            proof('expiring_report_pdf_downloaded_and_previewed', await print(expiringLabel, 'expiring_medical_certificates', 'pdf', 'expiring-report-real-pdf-preview', day(10)));
            proof('expiring_report_excel_content_matches', await print(expiringLabel, 'expiring_medical_certificates', 'excel', 'expiring-report-real-excel-download', day(10)));
            await setExpiration(day(-1)); await page.reload(); await assertWidgets(day(-1));
            proof('expired_report_pdf_downloaded_and_previewed', await print(expiredLabel, 'expired_medical_certificates', 'pdf', 'expired-report-real-pdf-preview', day(-1)));
            proof('expired_report_excel_content_matches', await print(expiredLabel, 'expired_medical_certificates', 'excel', 'expired-report-real-excel-download', day(-1)));
        } finally {
            // API cleanup covers owned current attachments, registration and unpaid payment.
            // Runner fixture reset separately cleans removed upload/orphan Associate records.
            expect((await api('profile/settings', {method: 'POST', data: originalSettings})).status()).toBe(200);
            expect((await api('statistic/dashboard/layout', {method: 'POST', data: {dashboard_layout: originalLayout}})).status()).toBe(200);
            if (!memberId) {
                const created = (await subscriptions()).filter(row => row.associate.first_name === 'Marta' && row.associate.last_name === 'Neri'
                    && !input.subscription_ids.includes(row.subscription_id));
                expect(created.length).toBeLessThanOrEqual(1);
                if (created[0]) {
                    memberId = created[0].subscription_id; remember('subscription_ids', memberId);
                    remember('associate_ids', created[0].associate.associate_id); remember('payment_ids', created[0].payment?.payment_id);
                }
            }
            if (memberId) {
                const current = rememberInfo(await info());
                if (current.medical) await setExpiration(null);
                expect((await api(`subscription/${memberId}/delete`, {method: 'POST'})).status()).toBe(200);
            }
            for (const documentId of owned.document_ids) {
                const removed = await api(`document/${documentId}/delete`, {method: 'DELETE'});
                expect([200, 404]).toContain(removed.status());
                expect((await api(`document/retrieve/${documentId}?download=true`)).status()).toBe(404);
            }
            owned.public_cleanup_finished = true; saveOwned();
        }
        expect((await read('profile/settings')).settings).toEqual(originalSettings);
        expect((await read('profile/info')).user_data.dashboard_layout).toEqual(originalLayout);
        proof('original_settings_and_dashboard_restored', true);
        expect(sort(await subscriptions(), 'subscription_id')).toEqual(baselineSubscriptions);
        expect(sort(await payments(), 'payment_id')).toEqual(baselinePayments);
        proof('baseline_registrations_and_payments_preserved', true);
        proof('owned_registration_and_payment_removed', true); proof('owned_attached_documents_removed', true);
        await page.reload(); await take('medical-configuration-restored');
        report[spec.outcome.field] = facts;
        report.medical_fixture_cleanup = {private_manifest: 'private/' + id + '-owned.json',
            api_cleanup: 'owned current certificate, known attached documents, registration and unpaid membership payment',
            runner_reset_required: 'removed wizard upload and its document; unreferenced Associate and any orphan certificate records'};
        report.checks = ['actual authenticated wizard upload, bad-file validation, remove/reupload and exact submitted certificate/date',
            'real profile upload, saved/reopened attachment, replacement and date-only edit preserve the correct IDs',
            'three reader mutation denials preserve saved certificate; notification setting disabled and reopened without email delivery',
            'both saved widgets, exact person link, missing/expired filters and today boundary verified against real API selections',
            'real PDF previews/downloads and parsed Excel medical report rows; widget printing submits empty filters',
            'original settings/layout and fixture registrations/payments restored; private orphan IDs reserved for runner reset'];
    }});
