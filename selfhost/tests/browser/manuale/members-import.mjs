// Actual selected-file upload and wizard actions; no simulated APIs, emails or external accounts.
import {scenario, expect} from './scenario.mjs';
import {memberAuthoredWorkflows} from '../../../../docs/manuale/member-authored-workflows.mjs';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import crypto from 'node:crypto';

const id = 'members-import';
const spec = memberAuthoredWorkflows[id];
const file = fileURLToPath(new URL('./fixtures/members-import.csv', import.meta.url));
const csv = fs.readFileSync(file);
const defaults = {'indirizzo': 'Via dello Sport 40', 'città di residenza': 'Roma', cap: '00100'};
const values = data => Object.values(data || {});
const fullName = row => `${row.associate.first_name} ${row.associate.last_name}`;

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
async function selectCheckbox(row) {
    const input = row.locator('input[type="checkbox"]').first();
    if (!await input.isChecked()) await input.locator('..').click();
    await expect(input).toBeChecked();
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
            await settled(currentPage); await capture(currentPage, number, checkpoint, focus);
        };
        async function get(route, requestApi = api) {
            const res = await requestApi(route); expect(res.ok()).toBeTruthy(); return res.json();
        }
        const members = requestApi => get('subscription/list?pagination[perpage]=100&type=athletes', requestApi);
        const drafts = async () => values((await get('subscription/associates-draft/list')).data);
        const baseline = await members(); proof('baseline_members', baseline.meta.total);
        proof('baseline_drafts', (await drafts()).length);
        const baselineIds = values(baseline.data).map(r => r.subscription_id).sort();
        const header = csv.toString('utf8').trim().split(/\r?\n/)[0].split(';');
        proof('csv_rows', csv.toString('utf8').trim().split(/\r?\n/).length - 1);
        report.import_fixture = {path: 'selfhost/tests/browser/manuale/fixtures/members-import.csv',
            sha256: crypto.createHash('sha256').update(csv).digest('hex'), delimiter: ';', rows: 2};

        await open('Organizzazione', '/#/members/list');
        await page.getByRole('button', {name: 'Bozze', exact: true}).click();
        const rows = page.locator('[data-row]'); await expect(rows).toHaveCount(0);
        async function importFile(recordCheckpoints = false) {
            await page.locator('a[href="/#/members/import"]').click();
            await expect(page.locator('#import_dropzone')).toBeVisible();
            const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === '/api/subscription/import/upload'
                && r.request().method() === 'POST' && r.request().headers()['content-type']?.startsWith('multipart/form-data'));
            await page.locator('#import_dropzone input[type="file"]').setInputFiles(file);
            const response = await uploaded; expect(response.ok()).toBeTruthy();
            const data = await response.json(); expect(data.document_id).toBeTruthy();
            expect(data.columns).toEqual(header);
            await expect(page.getByText(/File letto: members-import.csv/)).toBeVisible();
            if (recordCheckpoints) {
                proof('actual_file_uploaded', response.request().headers()['content-type'].startsWith('multipart/form-data'));
                proof('columns_from_server', JSON.stringify(data.columns) === JSON.stringify(header));
                await snap('import-file-read', page.locator('#import_dropzone').locator('..'));
            }
            await page.locator('#next-step').click();
            const mapping = page.locator('[data-wizard-type="step-content"][data-wizard-state="current"]');
            await expect(mapping.getByText('Collega le informazioni del tuo file ai campi corretti', {exact: true})).toBeVisible();
            // Match the destination badge, not matching option text inside
            // other selects; then use that badge's actual field row.
            const fieldRow = (section, key) => section.getByText(key, {exact: true}).and(section.locator('b'))
                .locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " row ")][1]');
            const mapper = key => fieldRow(mapping, key).locator('select');
            // Exercise the native mapping control, including clearing and selecting a real uploaded column.
            const email = mapper('email'); await email.selectOption({index: 0}); await email.selectOption('email');
            for (const column of header) await expect(mapper(column)).toHaveValue(column);
            if (recordCheckpoints) {
                await mapping.getByText('Collega le informazioni del tuo file ai campi corretti', {exact: true}).scrollIntoViewIfNeeded();
                await snap('import-columns', mapping);
            }
            await page.locator('#next-step').click();
            const common = page.locator('[data-wizard-type="step-content"][data-wizard-state="current"]');
            await expect(common.getByText('Colonne senza collegamento', {exact: true})).toBeVisible();
            for (const [key, value] of Object.entries(defaults)) {
                await fieldRow(common, key).locator('input').fill(value);
            }
            if (recordCheckpoints) await snap('import-defaults', common);
            await page.locator('#subscription_submit').click();
            const confirmation = page.locator('.swal2-popup');
            await expect(confirmation.getByRole('button', {name: 'Continua', exact: true})).toBeVisible();
            const saved = page.waitForResponse(r => new URL(r.url()).pathname === '/api/subscription/import/upload'
                && r.request().method() === 'POST' && r.request().headers()['content-type']?.startsWith('application/json'));
            await confirmation.getByRole('button', {name: 'Continua', exact: true}).click();
            const committed = await saved; expect(committed.ok()).toBeTruthy();
            const payload = committed.request().postDataJSON();
            expect(payload.document_id).toBe(data.document_id);
            for (const column of header) expect(payload.map[column]).toBe(column);
            expect(payload.default).toEqual(defaults);
            if (recordCheckpoints) {
                proof('mapping_saved', header.every(column => payload.map[column] === column));
                proof('defaults_saved', JSON.stringify(payload.default) === JSON.stringify(defaults));
            }
            await expect(page).toHaveURL(/#\/members\/list-draft$/);
            // A 200 or toast is insufficient: require all actual persisted draft rows.
            await expect.poll(async () => (await drafts()).length).toBe(2);
            await expect(rows).toHaveCount(2);
            return drafts();
        }
        let imported = await importFile(true);
        proof('imported_drafts', imported.length);
        proof('valid_initial_drafts', imported.filter(r => r.valid).length);
        proof('invalid_initial_drafts', imported.filter(r => !r.valid).length);
        const mario = imported.find(r => r.associate.first_name === 'Mario');
        const incomplete = imported.find(r => r.associate.last_name === 'Verdi');
        expect(mario).toBeTruthy(); expect(incomplete).toBeTruthy();
        expect(mario.associate).toMatchObject({first_name: 'Mario', last_name: 'Rossi', sex: 'M',
            tax_code: 'RSSMRA80A01H501U', born_date: '01/01/1980', born_city: 'Roma',
            address: defaults.indirizzo, address_city: defaults['città di residenza'], address_cap: defaults.cap,
            email: 'mario@aurora.example.test', membership_start_date: '01/09/2026',
            membership_end_date: '31/08/2027', subscription_number: 41});
        expect(incomplete.associate).toMatchObject({last_name: 'Verdi', sex: 'F',
            tax_code: 'VRDLRA90C41H501M', born_date: '01/03/1990', email: 'laura@aurora.example.test',
            address: defaults.indirizzo, address_city: defaults['città di residenza'], address_cap: defaults.cap});
        proof('imported_fields_match', mario.associate.email === 'mario@aurora.example.test'
            && incomplete.associate.address_cap === defaults.cap && !incomplete.associate.first_name);
        expect(mario.medical_certificate.medical_id).toBeTruthy();
        expect(mario.medical_certificate.certificate_expring_date).toBe('30/09/2027');
        proof('medical_date_without_document', mario.medical_certificate.filename === null);
        await snap('import-drafts', page.locator('.datatable-table').first());

        await rows.filter({hasText: 'Verdi'}).locator(':is([title="Modifica Bozza"],[data-original-title="Modifica Bozza"])').click();
        const drawer = page.locator('.drawer:visible').last();
        await drawer.locator('input[name="firstNameAssociate"]').fill('Laura');
        // Imported dates must display intact while correcting only the missing name.
        await expect(drawer.locator('input[name="membership_start_date"]')).toHaveValue('2026-09-01');
        await expect(drawer.locator('input[name="membership_end_date"]')).toHaveValue('2027-08-31');
        await expect(drawer.locator('input[name="bornDateAssociate"]')).toHaveValue('1990-03-01');
        const edited = page.waitForResponse(r => new URL(r.url()).pathname ===
            `/api/subscription/associates-draft/${incomplete.associate_import_draft_id}/edit` && r.request().method() === 'POST');
        await drawer.getByRole('button', {name: 'Salva Modifiche', exact: true}).click();
        expect((await edited).ok()).toBeTruthy();
        imported = await drafts(); const corrected = imported.find(r => r.associate_import_draft_id === incomplete.associate_import_draft_id);
        expect(corrected.associate.first_name).toBe('Laura'); expect(corrected.valid).toBe(true);
        proof('correction_persisted', corrected.valid && corrected.associate.first_name === 'Laura');
        await snap('import-draft-corrected', drawer);
        await drawer.locator('.drawer-header button.close').click(); await expect(drawer).not.toBeVisible();
        await expect(rows.filter({hasText: 'Laura Verdi'})).toHaveCount(1);

        async function approve(people, recordCheckpoint = false) {
            for (const person of people) await selectCheckbox(rows.filter({hasText: person}));
            await page.locator('#bkn_datatable_approve_selected').click();
            const modal = page.locator('#approve-modal-selected'); await expect(modal).toBeVisible();
            await modal.locator('[id="associate_data.type"]').click();
            await page.locator('.svelte-select-list:visible').getByText('Socio e Tesserato', {exact: true}).click();
            await expect(modal.getByText('Quota Associativa', {exact: true})).toBeVisible();
            await expect(modal.getByText('Quota Tesseramento', {exact: true})).toBeVisible();
            if (recordCheckpoint) await snap('import-approval', modal);
            const confirmed = page.waitForResponse(r => new URL(r.url()).pathname === '/api/subscription/associates-draft/list/approve'
                && r.request().method() === 'POST');
            await modal.getByRole('button', {name: 'Approva', exact: true}).click();
            const response = await confirmed; expect(response.ok()).toBeTruthy();
            expect(response.request().postDataJSON().subscription_details).toMatchObject({type: 2, role: 1});
            await expect(modal).not.toBeVisible(); return response.json();
        }
        const approved = await approve(['Mario Rossi', 'Laura Verdi'], true);
        proof('approved_drafts', approved.approved);
        await expect(rows).toHaveCount(0); proof('successful_drafts_removed', (await drafts()).length === 0);
        const after = await members(); proof('resulting_members', after.meta.total);
        const added = values(after.data).filter(r => !baselineIds.includes(r.subscription_id));
        expect(added.map(fullName).sort()).toEqual(['Laura Verdi', 'Mario Rossi']);
        proof('unsigned_imports', added.filter(r => r.status_flag === 1).length);
        proof('imported_number_normalized', String(added.find(r => fullName(r) === 'Mario Rossi').subscription_number) === '41');
        const ownerUser = added.every(r => r.user.user_id === input.user_id);
        proof('no_new_accounts', ownerUser);
        const profile = await get('profile/info');
        const association = profile.user_data.sport_association;
        expect(Number(association.subscription_fee)).toBe(25);
        expect(Number(association.membership_fee || 0)).toBe(0);
        for (const member of added) {
            expect(member.type).toBe(2); expect(member.role).toBe(1);
            expect(member.payment.amount).toBe(25); expect(member.payment.paid).toBe(false);
            expect(member.associate.address_cap).toBe('00100');
            expect(member.start_date).toBeTruthy(); expect(member.end_date).toBeTruthy();
        }
        proof('selected_type_role_and_fees_match', added.every(r => r.type === 2 && r.role === 1 && r.payment.amount === 25 && !r.payment.paid));
        const createdMario = added.find(r => fullName(r) === 'Mario Rossi');
        expect(createdMario.medical_expiration_date).toBe('2027-09-30'); expect(createdMario.medical_document).toBeNull();
        await page.getByRole('button', {name: 'Tesserati', exact: true}).click(); await expect(rows).toHaveCount(5);
        await page.reload(); await expect(rows).toHaveCount(5);
        for (const person of ['Mario Rossi', 'Laura Verdi']) await expect(rows.filter({hasText: person})).toHaveCount(1);
        const reloaded = await members();
        proof('members_persist_after_reload', values(reloaded.data).map(r => r.subscription_id).sort().join(',')
            === values(after.data).map(r => r.subscription_id).sort().join(','));
        await snap('import-persisted', page.locator('.datatable-table').first());

        await page.getByRole('button', {name: 'Bozze', exact: true}).click(); await expect(rows).toHaveCount(0);
        const repeated = await importFile(); proof('repeat_upload_drafts', repeated.length);
        const duplicateId = repeated.find(r => r.associate.first_name === 'Mario').associate_import_draft_id;
        const duplicate = await approve(['Mario Rossi']); proof('duplicate_approved', duplicate.approved);
        await expect(rows).toHaveCount(2);
        proof('duplicate_preserves_member_count', (await members()).meta.total === after.meta.total);
        proof('duplicate_draft_retained', (await drafts()).some(r => r.associate_import_draft_id === duplicateId));
        await snap('import-duplicate-retained', page.locator('.datatable-table').first());

        const reader = await actor('reader'); await reader.open('Organizzazione', '/#/members/list');
        await expect(reader.page.locator('[data-row]')).toHaveCount(5);
        proof('reader_members', (await members(reader.api)).meta.total);
        proof('reader_drafts_hidden', await reader.page.getByRole('button', {name: 'Bozze', exact: true}).count() === 0
            && await reader.page.locator('a[href="/#/members/import"]').count() === 0);
        const deniedUpload = await reader.api('subscription/import/upload', {method: 'POST', multipart: {
            associates_file: {name: 'members-import.csv', mimeType: 'text/csv', buffer: csv},
        }});
        proof('reader_upload_denied', deniedUpload.status() === 403);
        const deniedEdit = await reader.api(`subscription/associates-draft/${duplicateId}/edit`,
            {method: 'POST', data: {edit_data: repeated.find(r => r.associate_import_draft_id === duplicateId)}});
        proof('reader_edit_denied', deniedEdit.status() === 403);
        const deniedApproval = await reader.api('subscription/associates-draft/list/approve', {method: 'POST', data: {
            associate_import_draft_ids: [duplicateId], subscription_details: {type: 2, role: 1},
        }});
        proof('reader_approval_denied', deniedApproval.status() === 403);
        proof('reader_state_preserved', (await members()).meta.total === 5 && (await drafts()).length === 2);
        report.expected_denials.push(...[
            ['subscription/import/upload', deniedUpload], ['subscription/associates-draft/[fixture]/edit', deniedEdit],
            ['subscription/associates-draft/list/approve', deniedApproval],
        ].map(([route, response]) => ({identity: 'reader', path: '/api/' + route, status: response.status()})));
        await snap('import-reader', reader.page.locator('.datatable-table').first(), reader.page);
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.checks.push('multipart file and server columns verified', 'real mapping/default payload and persisted drafts verified',
            'incomplete draft corrected through UI', 'approval creates unsigned subscriptions and removes successful drafts',
            'duplicate approval creates no extra subscription and retains the draft', 'reader writes rejected without state changes');
        report.limits = ['CSV only: Excel, ODS, minors and tutor mapping not exercised',
            'default fee configuration only; alternative quota plans and fee exemptions not exercised',
            'medical expiry is imported without an original certificate document',
            'duplicate draft remains pending in the current backend implementation',
            'imports reference the existing fixture owner account; no external registration or invitation performed'];
    },
});
