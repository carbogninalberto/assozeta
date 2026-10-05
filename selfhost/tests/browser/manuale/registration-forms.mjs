// Real writes belong only to the disposable owner-controlled fixture run.
import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {registrationFormsSources} from './registration-forms-sources.mjs';
import {registrationFormsCaptureSpecs, registrationFormsExpectedOutcome} from '../../../../docs/manuale/registration-forms-recipes.mjs';

const id = 'registration-forms-manage';
await scenario({id, prefix: registrationFormsCaptureSpecs[id][0].replace(/\/$/, ''),
    sources: registrationFormsSources, actions: async ({page, api, open, actor, context, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const json = async (route, request = api) => {
            const response = await request(route);
            expect(response.status()).toBe(200);
            return response.json();
        };
        const profile = async () => (await json('profile/info')).user_data;
        const initialProfile = await profile();
        const original = structuredClone(initialProfile.sport_association);
        expect(original.denomination).toBe('Associazione Sportiva Aurora');
        expect(original.enabled_for).toEqual(['associate', 'associate-membership', 'membership']);
        expect(original.additional_fields).toEqual([]);
        expect(original.additional_sections).toEqual([]);
        expect(original.subscription_fee).toBe('25.00');
        expect(original.membership_fee).toBe('0.00');
        expect(original.enable_quotes_management).toBe(true);
        expect(original.multiple_subscription_fee).toBe(false);
        expect(original.multiple_membership_fee).toBe(false);
        const records = async () => {
            const subscriptions = Object.values((await json('subscription/list?pagination[perpage]=100')).data);
            const payments = Object.values((await json('payment/list')).data);
            const pick = (row, keys) => Object.fromEntries(keys.map(key => [key, row[key]]));
            return {
                subscriptions: subscriptions.map(row => pick(row, ['subscription_id', 'start_date', 'end_date',
                    'status_flag', 'type', 'archived', 'deleted', 'subscription_number'])).sort((a, b) =>
                    a.subscription_id.localeCompare(b.subscription_id)),
                payments: payments.map(row => pick(row, ['payment_id', 'amount', 'paid', 'expense', 'archived',
                    'payment_date', 'subscription', 'associate'])).sort((a, b) => a.payment_id.localeCompare(b.payment_id)),
            };
        };
        const initialRecords = await records();
        expect(initialRecords.subscriptions.map(row => row.subscription_id).sort()).toEqual([...input.subscription_ids].sort());
        const businessWrites = [];
        const publicErrors = [];
        // Observe the real popup immediately, including errors during its load.
        context.on('page', browserPage => browserPage.on('pageerror', error => publicErrors.push(error.message)));
        const observe = browserPage => browserPage.on('request', request => {
            const url = new URL(request.url());
            if (url.origin === input.origin && /^(POST|PUT|PATCH|DELETE)$/.test(request.method()) &&
                /\/(subscription\/add|auth\/(register|signup)|collaborators\/add)(\/|$)/.test(url.pathname))
                businessWrites.push({path: url.pathname, method: request.method()});
        });
        observe(page);
        const checkpoint = (n) => registrationFormsCaptureSpecs[id][1][n - 1];
        const take = async (browserPage, n, locator, options = {}) => {
            await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            await capture(browserPage, n, checkpoint(n), locator, options);
        };
        const save = async () => {
            const saved = page.waitForResponse(response => new URL(response.url()).pathname ===
                '/api/profile/update/subscription/template' && response.request().method() === 'PATCH');
            await page.locator('#bkn_form_account_update_submit').click();
            expect((await saved).status()).toBe(200);
            await expect(page.getByText("Modulo d'iscrizione aggiornato correttamente", {exact: true})).toBeVisible();
        };
        const tab = async name => page.locator('.nav-link').filter({hasText: new RegExp('^\\s*' + name + '\\s*$')}).click();
        const sectionCard = () => page.locator('div.my-6').filter({has: page.getByPlaceholder('Nome sezione...')});
        const fieldCard = () => page.locator('.form-element-preview .form-group').first();
        const property = label => fieldCard().locator('.form-group').filter({hasText: new RegExp('^\\s*' + label + '\\s*$')}).locator('input');
        const openField = async () => {
            await tab('Campi Aggiuntivi');
            await expect(page.locator('.form-element-preview')).toHaveCount(1);
            await fieldCard().click();
            await expect(property('Etichetta')).toBeVisible();
        };
        let savedOnce = false;
        let publicPage;
        try {
            await open('Organizzazione', '/#/members/subscription/template');
            await expect(page.getByRole('heading', {name: 'Modulo Iscrizione', exact: true})).toBeVisible();
            await expect(page.locator('#associate-checkbox')).toBeChecked();
            await expect(page.locator('#associate-membership-checkbox')).toBeChecked();
            await expect(page.locator('#membership-checkbox')).toBeChecked();
            await take(page, 1);
            await setCheckbox(page.locator('#associate-checkbox'), false);
            await setCheckbox(page.locator('#membership-checkbox'), false);
            await take(page, 2, page.locator('.col-md-4').filter({has: page.locator('#associate-checkbox')}));
            await tab('Quote');
            await page.locator('input[name="subscription_fee"]').fill('30,00');
            // NumberInput commits the input on change/keyup, rather than bind:value.
            await page.locator('input[name="subscription_fee"]').press('Tab');
            await expect(page.locator('input[name="membership_fee"]')).toHaveValue('0,00');
            await take(page, 3, page.locator('.row.mt-8'));
            await tab('Sezioni Modulo');
            await page.getByRole('button', {name: 'Aggiungi', exact: true}).click();
            await expect(sectionCard()).toHaveCount(1);
            await page.getByPlaceholder('Nome sezione...').fill('Materiale per allenamento');
            await sectionCard().locator('[contenteditable="true"]').fill('Porta borraccia e abbigliamento comodo.');
            await setCheckbox(sectionCard().locator('input[type="checkbox"]').nth(0), false);
            await setCheckbox(sectionCard().locator('input[type="checkbox"]').nth(1), true);
            await setCheckbox(sectionCard().locator('input[type="checkbox"]').nth(2), false);
            await take(page, 4, sectionCard());
            await tab('Campi Aggiuntivi');
            await page.getByRole('button', {name: 'Testo', exact: true}).click();
            await openField();
            await property('Etichetta').fill('Taglia maglietta');
            await property('Placeholder').fill('Esempio: M');
            await property('Etichetta aiuto').fill('Indica la taglia desiderata.');
            await setCheckbox(property('Obbligatorio'), false);
            await take(page, 5, fieldCard());
            // Editing is still local until the real Save request.
            expect((await profile()).sport_association.enabled_for).toEqual(original.enabled_for);
            expect((await profile()).sport_association.additional_fields).toEqual([]);
            savedOnce = true;
            await save();
            await page.reload();
            await expect(page.locator('#associate-membership-checkbox')).toBeChecked();
            await expect(page.locator('#associate-checkbox')).not.toBeChecked();
            await expect(page.locator('#membership-checkbox')).not.toBeChecked();
            const persisted = (await profile()).sport_association;
            expect(persisted.enabled_for).toEqual(['associate-membership']);
            expect(persisted.subscription_fee).toBe('30.00');
            expect(persisted.additional_sections).toHaveLength(1);
            const addedSection = persisted.additional_sections[0];
            expect(addedSection.name).toBe('MATERIALE PER ALLENAMENTO');
            expect(addedSection.text.replace(/<[^>]*>/g, '').trim()).toBe('Porta borraccia e abbigliamento comodo.');
            expect(Boolean(addedSection.show_to_both)).toBe(true);
            expect(Boolean(addedSection.show_to_members)).toBe(false);
            expect(Boolean(addedSection.show_to_athletes)).toBe(false);
            expect(persisted.additional_fields).toHaveLength(1);
            expect(persisted.additional_fields[0].type).toBe('text');
            expect(persisted.additional_fields[0].props).toMatchObject({label: 'Taglia maglietta', placeholder: 'Esempio: M',
                helperLabel: 'Indica la taglia desiderata.', required: false});
            await take(page, 6);
            await tab('Quote');
            await expect(page.locator('input[name="subscription_fee"]')).toHaveValue('30,00');
            await tab('Sezioni Modulo');
            await expect(page.getByPlaceholder('Nome sezione...')).toHaveValue('MATERIALE PER ALLENAMENTO');
            await expect(sectionCard().locator('[contenteditable="true"]')).toContainText('Porta borraccia');
            await expect(sectionCard().locator('input[type="checkbox"]').nth(1)).toBeChecked();
            await take(page, 7, sectionCard());
            await openField();
            await expect(property('Etichetta')).toHaveValue('Taglia maglietta');
            await expect(property('Placeholder')).toHaveValue('Esempio: M');
            await expect(property('Etichetta aiuto')).toHaveValue('Indica la taglia desiderata.');
            await expect(property('Obbligatorio')).not.toBeChecked();
            await take(page, 8, fieldCard());
            await open('Organizzazione', '/#/members/list');
            await page.getByRole('button', {name: 'Libro Soci', exact: true}).click();
            await expect(page).toHaveURL(/#\/members\/members-book$/);
            await expect(page.getByRole('heading', {name: 'Libro Soci', exact: false})).toBeVisible();
            const share = page.getByText('Condividi link iscrizioni', {exact: true});
            await expect(share).toBeVisible();
            await take(page, 9);
            await share.click();
            const modal = page.locator('#share-link');
            await expect(modal).toBeVisible();
            await expect(modal.getByText('Condividi link iscrizioni', {exact: true})).toBeVisible();
            const link = await modal.locator('input[type="text"]').inputValue();
            const parsed = new URL(link);
            expect(parsed.origin === input.origin).toBe(true);
            expect(parsed.search).toBe('');
            expect(parsed.hash === '#/subscribe/' + initialProfile.username.toLowerCase()).toBe(true);
            expect(parsed.hash).not.toContain('preregistration');
            await context.grantPermissions(['clipboard-read', 'clipboard-write'], {origin: input.origin});
            await modal.locator('[data-clipboard="true"]').click();
            await expect(page.getByText('Link copiato negli appunti', {exact: true})).toBeVisible();
            expect(await page.evaluate(() => navigator.clipboard.readText()) === link).toBe(true);
            await take(page, 10, modal.locator('.modal-content'), {mask: [modal.locator('input'), modal.locator('#qr-code-subscriptions')],
                redactionReason: 'Registration URL and encoded QR are excluded from manual captures.'});
            const popup = page.waitForEvent('popup');
            await modal.getByRole('button', {name: 'Apri', exact: true}).click();
            publicPage = await popup;
            observe(publicPage);
            await publicPage.waitForLoadState('domcontentloaded');
            await expect(publicPage.getByText('Associazione Sportiva Aurora', {exact: true})).toBeVisible();
            await expect(publicPage.getByText("Stai compilando l'iscrizione come", {exact: false})).toContainText('Socio e Tesserato');
            const publicConfig = (await json('search/profile/' + initialProfile.username.toLowerCase() + '?module_info=1')).data.user.sport_association;
            expect(publicConfig.enabled_for).toEqual(persisted.enabled_for);
            expect(publicConfig.additional_fields).toEqual(persisted.additional_fields);
            expect(publicConfig.additional_sections).toEqual(persisted.additional_sections);
            await take(publicPage, 11);
            await publicPage.getByRole('button', {name: /^Continua come/}).click();
            const shirtField = publicPage.getByLabel('Taglia maglietta', {exact: true});
            await expect(shirtField).toBeVisible();
            await expect(shirtField).toHaveAttribute('placeholder', 'Esempio: M');
            await expect(shirtField).not.toHaveAttribute('required', '');
            await expect(publicPage.getByText('Indica la taglia desiderata.', {exact: true})).toBeVisible();
            await take(publicPage, 12, shirtField.locator('..').locator('..'), {
                mask: [publicPage.locator('input:not([name="' + persisted.additional_fields[0].props.name + '"])')],
                redactionReason: 'Other personal-data inputs are masked; the configured custom field remains visible.'});
            await publicPage.close();
            await modal.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
            const reader = await actor('reader');
            await reader.open('Organizzazione', '/#/members/subscription/template');
            await expect(reader.page.locator('#bkn_form_account_update_submit')).toBeDisabled();
            await take(reader.page, 13);
            const denied = await reader.api('profile/update/subscription/template', {method: 'PATCH',
                data: {sport_association: {...persisted, enabled_for: ['associate']}}});
            expect(denied.status()).toBe(403);
            expect((await profile()).sport_association).toEqual(persisted);
            expect(await records()).toEqual(initialRecords);
            expect(businessWrites).toEqual([]);
            expect(publicErrors).toEqual([]);
            report.registration_forms_workflow = {...registrationFormsExpectedOutcome};
            report.checks = ['owner edits type, simple quote, custom section and optional text field through actual controls',
                'actual Save PATCH and authenticated reload preserve every configured property',
                'actual clipboard matches current registration link; URL and QR omitted from reports and masked in captures',
                'public opening observes configured type and custom field without creating or submitting a registration',
                'reader sees disabled top Save and API 403; existing registrations and payment records remain unchanged'];
            report.external_gaps = [{operation: 'registration-share-delivery-print-and-submission',
                status: 'needs_external_verification', reason: 'No WhatsApp/email delivery, QR printing/scanning, registration submission, multiple quotes or archive-template PDF was exercised.'}];
        } finally {
            if (publicPage && !publicPage.isClosed()) await publicPage.close();
            if (savedOnce) {
                const reset = await api('profile/update/subscription/template', {method: 'PATCH', data: {sport_association: original}});
                expect(reset.status()).toBe(200);
                const restored = (await profile()).sport_association;
                for (const key of ['enabled_for', 'additional_fields', 'additional_sections', 'subscription_fee',
                    'membership_fee', 'enable_quotes_management', 'multiple_subscription_fee', 'multiple_membership_fee'])
                    expect(restored[key]).toEqual(original[key]);
                for (const key of ['regulation', 'demand']) expect(restored[key]?.trim()).toBe(original[key]?.trim());
                expect(await records()).toEqual(initialRecords);
            }
        }
    }});
