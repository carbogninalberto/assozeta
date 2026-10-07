import {setCheckbox} from './controls.mjs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs';
import crypto from 'node:crypto';
import {scenario, expect} from './scenario.mjs';
import {memberMedicalSources, openGiulia} from './member-profile-sources.mjs';

await scenario({id: 'members-medical-manage', prefix: 'images/certificati/profilo', sources: memberMedicalSources,
    actions: async ({page, api, open, actor, capture, report, input}) => {
        const id = input.subscription_ids[0];
        const drawer = await openGiulia({page, open, expect});
        await drawer.locator('.nav-text').getByText('Certificato medico', {exact: true}).click();
        await expect(drawer.getByRole('button', {name: 'Nuovo Certificato', exact: true})).toBeVisible();
        await capture(page, 1, 'medical-tab-with-no-document');
        await drawer.getByRole('button', {name: 'Nuovo Certificato', exact: true}).click();
        const modal = page.locator('#add-medical-certificate-modal');
        await expect(modal).toBeVisible();
        await expect(modal.getByRole('button', {name: 'Salva', exact: true})).toBeDisabled();
        const document = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'fixtures/documento-medico-demo.pdf');
        const uploaded = page.waitForResponse(response =>
            new URL(response.url()).pathname === `/api/subscription/${id}/medical-certificate/upload`
            && response.request().method() === 'POST');
        await modal.locator('input[type="file"]').setInputFiles(document);
        const uploadResponse = await uploaded;
        expect(uploadResponse.status()).toBe(200);
        const attachment = await uploadResponse.json();
        expect(attachment.medical).toBeTruthy();
        expect(attachment.expiring_date).toBeNull();
        await expect(modal.getByText('Documento caricato: documento-medico-demo.pdf', {exact: true})).toBeVisible();
        await modal.locator('input[name="expiring_date"]').fill('2027-09-30');
        await expect(modal.getByRole('button', {name: 'Salva', exact: true})).toBeEnabled();
        await capture(page, 2, 'uploaded-demo-file-and-manual-expiration-before-saving', modal.locator('.modal-content'));
        const saved = page.waitForResponse(response =>
            new URL(response.url()).pathname === `/api/subscription/${id}/medical-certificate/set-certificate-expiration`
            && response.request().method() === 'POST');
        await modal.getByRole('button', {name: 'Salva', exact: true}).click();
        expect((await saved).status()).toBe(200);
        await expect(modal).toHaveCount(0);
        const certificate = async () => (await (await api(`subscription/${id}/info`)).json()).data.info;
        await expect.poll(async () => (await certificate()).medical_expiration_date).toBe('2027-09-30');
        await page.reload();
        const reloaded = await openGiulia({page, open, expect});
        await reloaded.locator('.nav-text').getByText('Certificato medico', {exact: true}).click();
        await expect(reloaded).toContainText('30/09/2027');
        const info = await certificate();
        expect(info.medical_document).toBe(attachment.medical);
        await capture(page, 3, 'document-and-expiration-persist-after-reload');
        const updated = page.waitForResponse(response =>
            new URL(response.url()).pathname === `/api/subscription/${id}/medical-certificate/edit`
            && response.request().method() === 'POST');
        await setCheckbox(reloaded.locator('input[type="checkbox"]'),true);
        expect((await updated).status()).toBe(200);
        expect((await certificate()).competitive_medical_certificate).toBe(true);
        await expect(reloaded).toContainText('AGONISTICO');
        await capture(page, 4, 'competitive-certificate-setting-saved');
        const reader = await actor('reader');
        const readOnly = await openGiulia({...reader, expect});
        await readOnly.locator('.nav-text').getByText('Certificato medico', {exact: true}).click();
        await expect(readOnly.locator('input[type="checkbox"]')).toBeDisabled();
        const forbidden = await reader.api(`subscription/${id}/medical-certificate/edit`,
            {method: 'POST', data: {competitive_medical_certificate: false}});
        expect(forbidden.status()).toBe(403);
        expect((await certificate()).competitive_medical_certificate).toBe(true);
        await reloaded.getByRole('button', {name: 'Rimuovi', exact: true}).click();
        const confirmation = page.locator('.swal2-popup');
        await expect(confirmation).toContainText('Sei sicuro di voler rimuovere il certificato?');
        await capture(page, 5, 'confirm-certificate-removal', confirmation);
        const removed = page.waitForResponse(response =>
            new URL(response.url()).pathname === `/api/subscription/${id}/medical-certificate/set-certificate-expiration`
            && response.request().method() === 'POST');
        await confirmation.getByRole('button', {name: 'Rimuovi', exact: true}).click();
        expect((await removed).status()).toBe(200);
        await expect.poll(async () => (await certificate()).medical).toBeNull();
        await expect(reloaded.getByRole('button', {name: 'Nuovo Certificato', exact: true})).toBeVisible();
        await capture(page, 6, 'medical-tab-empty-after-removal');
        report.medical_certificate = {document_uploaded: true, document_id_matches: info.medical_document === attachment.medical,
            input_fixture_sha256: crypto.createHash('sha256').update(fs.readFileSync(document)).digest('hex'),
            expiration: info.medical_expiration_date, persisted_after_reload: true, competitive_setting_saved: true,
            reader_update_status: forbidden.status(), denied_setting_unchanged: true, removed_medical_is_null: true,
            automatic_expiration_observed: false, email_delivery_exercised: false};
        report.checks = ['native upload modal submits a real file to the backend', 'real object-storage document attached to the registration',
            'manual expiration and attachment persist after reload', 'competitive setting saved and reader denied',
            'removal confirmation clears the registration certificate'];
    }});
