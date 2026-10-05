// Reused actual UI wizard; callers may omit captures for scenario preparation.
import {expect} from './scenario.mjs';
import crypto from 'node:crypto';

export async function createMember({page, api, open, actor, capture, report, input}, {beforeProfileContinue, expectedAmount = 25} = {}) {
        await open('Organizzazione', '/#/members/list');
        await expect(page.locator('[data-row]')).toHaveCount(3);
        await capture(page, 1, 'members-list-and-add-action');
        await page.getByRole('link', {name: 'Aggiungi', exact: true}).click();
        await expect(page.getByRole('heading', {name: 'Nuovo Socio o Tesserato', exact: true})).toBeVisible();
        const active = () => page.locator('[data-wizard-type="step-content"][data-wizard-state="current"]');
        const next = async heading => {
            await page.locator('[data-wizard-type="action-next"]').click();
            await expect(active()).toContainText(heading);
        };
        await expect(page.locator('input[name="new_account_radio"][value="false"]')).toBeChecked();
        await expect(active()).toContainText('Socio e Tesserato');
        if (beforeProfileContinue) await beforeProfileContinue({page, active, input});
        await capture(page, 2, 'registration-type-and-no-new-account');
        await next("Inserisci le informazioni dell'Associato");
        const values = {
            firstNameAssociate: 'Marta', lastNameAssociate: 'Neri',
            bornCityAssociate: 'Roma', addressAssociate: 'Via delle Attività 4',
            addressCityAssociate: 'Roma', capAssociate: '00100', emailAssociate: 'marta@example.test',
        };
        for (const [name, value] of Object.entries(values)) await active().locator(`[name="${name}"]`).fill(value);
        await expect(active().locator('input[name="bornDateAssociate"]')).toHaveValue('2000-01-01');
        const taxInput = active().locator('[name="taxCodeAssociate"]');
        const generated = page.waitForResponse(response => new URL(response.url()).pathname === '/api/subscription/calculate-tax-code'
            && response.request().method() === 'POST');
        await taxInput.locator('..').getByRole('button').click();
        const calculation = await generated;
        expect(calculation.status()).toBe(200);
        const fiscalCode = (await calculation.json()).tax_code;
        expect(fiscalCode).toMatch(/^[A-Z0-9]{16}$/);
        await expect(taxInput).toHaveValue(fiscalCode);
        await active().locator('[name="firstNameAssociate"]').scrollIntoViewIfNeeded();
        await capture(page, 3, 'personal-data-and-fiscal-code');
        await active().locator('[name="emailAssociate"]').scrollIntoViewIfNeeded();
        await capture(page, 4, 'residence-and-contact-data');
        await next('Firma del documento');
        const canvas = active().locator('canvas');
        await expect(canvas).toBeVisible();
        await expect.poll(() => canvas.evaluate(element => element.width > 0 && element.height > 0)).toBeTruthy();
        await canvas.scrollIntoViewIfNeeded();
        const box = await canvas.boundingBox();
        const x = box.x + box.width * .18, y = box.y + box.height * .5;
        await page.mouse.move(x, y);
        await page.mouse.down();
        for (let index = 1; index <= 32; index++) {
            await page.mouse.move(x + index * box.width * .018, y + Math.sin(index * .6) * box.height * .15);
        }
        await page.mouse.up();
        await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('signature') || '{}').there_is_signature)).toBe(true);
        expect(await canvas.evaluate(element => {
            const pixels = element.getContext('2d').getImageData(0, 0, element.width, element.height).data;
            return pixels.some((value, index) => index % 4 === 3 && value > 0
                && pixels[index - 3] < 230 && pixels[index - 2] < 230 && pixels[index - 1] < 230);
        })).toBe(true);
        const drawnSignature = await page.evaluate(() => JSON.parse(localStorage.getItem('signature')));
        await capture(page, 5, 'fictional-signature-entered', active());
        await next('Hai già un certificato medico?');
        await capture(page, 6, 'medical-certificate-step-with-no-attachment', active());
        await next('Ancora un ultimo passo');
        await expect(active()).toContainText('Marta');
        await expect(active()).toContainText('Neri');
        await capture(page, 7, 'registration-summary-before-saving', active());
        await page.locator('#subscription_submit').click();
        await expect(page.getByText('Hai inserito i dati corretti? Controlla prima di continuare.', {exact: true})).toBeVisible();
        const created = page.waitForResponse(response => response.url().endsWith('/subscription/add') && response.request().method() === 'POST');
        await page.locator('.swal2-popup').getByRole('button', {name: 'Continua', exact: true}).click();
        const response = await created;
        expect(response.status()).toBe(200);
        const creation = await response.json();
        expect(creation.status).toBe('success');
        expect(Number(creation.amount)).toBe(expectedAmount);
        expect(creation.payment_id).toBeTruthy();
        await expect(page).toHaveURL(/#\/members\/list$/);
        await page.reload();
        const member = page.locator('[data-row]').filter({hasText: 'Marta Neri'});
        await expect(member).toBeVisible();
        await expect(page.locator('[data-row]')).toHaveCount(4);
        const listResponse = await api('subscription/list?query[generalSearch]=Marta');
        expect(listResponse.ok()).toBeTruthy();
        const list = await listResponse.json();
        expect(list.meta.total).toBe(1);
        const subscription = Object.values(list.data)[0];
        expect(subscription.associate.first_name).toBe('Marta');
        expect(subscription.associate.last_name).toBe('Neri');
        expect(subscription.associate.email).toBe('marta@example.test');
        expect(subscription.associate.tax_code).toBe(fiscalCode);
        expect(subscription.type).toBe(2);
        expect(subscription.role).toBe(1);
        expect(subscription.status_flag).toBe(2); // Signed request is pending, not approved.
        expect(subscription.user.user_id).toBe(input.user_id);
        expect(subscription.payment.payment_id).toBe(creation.payment_id);
        expect(subscription.payment.amount).toBe(expectedAmount);
        expect(subscription.payment.paid).toBe(false);
        expect(subscription.signature_present).toBe(true);
        // Private storage deliberately has no public signature URL. Prove the
        // saved signature bytes by reading the real rendered registration.
        const document = await api(`document/subscription/${subscription.subscription_id}/view/`);
        expect(document.status()).toBe(200);
        const embeddedSignature = /<img\b[^>]*\bsrc="(data:image\/png;base64,[^"]+)"/.exec(await document.text())?.[1];
        expect(Boolean(embeddedSignature)).toBe(true);
        const signatureHash = value => crypto.createHash('sha256').update(Buffer.from(value.split(',')[1], 'base64')).digest('hex');
        const signaturePreserved = signatureHash(embeddedSignature) === signatureHash(drawnSignature.data);
        expect(signaturePreserved).toBe(true);
        expect(subscription.medical).toBeNull();
        await capture(page, 8, 'created-pending-registration-persists-after-reload');
        const reader = await actor('reader');
        await reader.open('Organizzazione', '/#/members/list');
        await expect(reader.page.getByRole('link', {name: 'Aggiungi', exact: true})).toHaveCount(0);
        const denial = await reader.api('subscription/add', {method: 'POST', data: {
            new_user_account: {new_member: false}, associate_data: {first_name: 'Marta', last_name: 'Neri'},
            associate_tutor_data: null, signature: {there_is_signature: false, data: ''},
        }});
        expect(denial.status()).toBe(403);
        expect((await (await api('subscription/list')).json()).meta.total).toBe(4);
        report.member_creation = {type: subscription.type, role: subscription.role, status_flag: subscription.status_flag,
            account_created: false, owner_account_preserved: subscription.user.user_id === input.user_id,
            payment_amount: subscription.payment.amount, payment_paid: subscription.payment.paid,
            signature_saved: subscription.signature_present && signaturePreserved, fiscal_code_generated: true,
            medical_attached: false, persisted_after_reload: true, registrations: 4, reader_create_status: denial.status()};
        report.checks = ['real owner navigation and creation wizard', 'existing account selected without user creation',
            'fictional personal data and signature entered through the UI', 'no medical document attached in this example',
            'real POST creates an unpaid registration payment matching the selected fee', 'persisted person and pending signed registration after reload',
            'read-only collaborator cannot see or call the create action; denial leaves four registrations'];

}
