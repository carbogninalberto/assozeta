// Organization and year writes go through actual UI handlers and persisted APIs.
import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {focusRegion} from './focus.mjs';
import {organizationBasicsAuthoredWorkflows} from '../../../../docs/manuale/organization-basics-authored-workflows.mjs';
const spec = organizationBasicsAuthoredWorkflows['organization-settings'];
import {organizationSettingsSources, reloadOrganizationProfile} from './organization-access-sources.mjs';

await scenario({id: 'organization-settings', prefix: 'images/impostazioni/organizzazione-anno',
    sources: spec.sources, actions: async ({page, api, context, open, actor, capture, report, input}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const json = async (route, client = api) => {
            const response = await client(route);
            expect(response.status()).toBe(200);
            return response.json();
        };
        const profile = (await json('profile/info')).user_data;
        const originalSettings = (await json('profile/settings')).settings;
        const original = profile.sport_association;
        expect(original.denomination).toBe('Associazione Sportiva Aurora');
        expect(original.address).toBe('Via dello Sport 12');
        expect(originalSettings.balance_sheet_year).toBe('1');
        expect(originalSettings.subscription_start_month).toBe(9);
        expect(originalSettings.subscription_start_day).toBe(1);
        expect(originalSettings.custom_end_date).toBe(false);
        const registrationState = async () => {
            // Read the non-printing list endpoint. Detail GET can enqueue a PDF
            // and would introduce an unrelated document mutation into this run.
            const listed = (await json('subscription/list?pagination[perpage]=100')).data;
            return input.subscription_ids.map(uid => {
                const row = Object.values(listed).find(item => item.subscription_id === uid);
                expect(row).toBeTruthy();
                return Object.fromEntries(['subscription_id', 'sport_association', 'creation_date',
                    'start_date', 'end_date', 'status_flag', 'type', 'role', 'archived', 'deleted',
                    'subscription_number', 'subscription_type', 'payment'].map(field => [field, row[field]]));
            });
        };
        const existingRegistrations = await registrationState();
        const fields = {'Inserisci indirizzo...': 'Via dello Sport 14', 'Inserisci CAP...': '00101',
            'Inserisci URL sito web...': 'https://aurora.example.test', 'Inserisci nome abbreviato...': 'Aurora',
            'Inserisci sport...': 'Ginnastica'};
        const originalFields = {'Inserisci indirizzo...': original.address, 'Inserisci CAP...': original.address_cap,
            'Inserisci URL sito web...': original.website, 'Inserisci nome abbreviato...': original.abbreviated,
            'Inserisci sport...': original.sport};
        const saveInfo = async () => {
            const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/profile/update'
                && response.request().method() === 'PATCH');
            await page.locator('#bkn_form_account_update_submit').click();
            expect((await saved).status()).toBe(200);
        };
        const saveSettings = async () => {
            const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/profile/settings'
                && response.request().method() === 'POST');
            await page.locator('#bkn_form_password_update_submit').click();
            expect((await saved).status()).toBe(200);
            await expect(page.locator('#bkn_form_password_update_submit')).toBeDisabled();
        };
        const take = async (number, checkpoint, locator) => {
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            await capture(page, number, checkpoint, locator);
        };
        try {
        await open('Impostazioni', '/#/profile');
        await page.getByText('Informazioni Account', {exact: true}).click();
        await expect(page.getByPlaceholder('Inserisci denominazione...', {exact: true})).toHaveValue(original.denomination);
        await page.getByPlaceholder('Inserisci denominazione...', {exact: true}).scrollIntoViewIfNeeded();
        await take(1, 'organization-info-original-address-and-owner-controls');
        for (const [placeholder, value] of Object.entries(fields))
            await page.getByPlaceholder(placeholder, {exact: true}).fill(value);
        const changedFieldGroups = Object.keys(fields).map(placeholder => page.getByPlaceholder(placeholder,
            {exact: true}).locator('..').locator('..'));
        const sportGroup = changedFieldGroups.at(-1);
        await sportGroup.scrollIntoViewIfNeeded();
        await expect(page.getByPlaceholder('Inserisci sport...', {exact: true})).toBeInViewport({ratio: 1});
        await take(2, 'organization-address-website-abbreviation-and-sport-before-save',
            focusRegion(sportGroup, ...changedFieldGroups.slice(0, -1)));
        await saveInfo();
        const savedProfile = await reloadOrganizationProfile({page, api, context, expect});
        for (const [key, value] of Object.entries({address: 'Via dello Sport 14', address_cap: '00101',
            website: 'https://aurora.example.test', abbreviated: 'Aurora', sport: 'Ginnastica'}))
            expect(savedProfile.sport_association[key]).toBe(value);
        await expect(page.getByPlaceholder('Inserisci indirizzo...', {exact: true})).toHaveValue('Via dello Sport 14');
        await expect(page.getByPlaceholder('Inserisci nome abbreviato...', {exact: true})).toHaveValue('Aurora');
        await page.getByPlaceholder('Inserisci indirizzo...', {exact: true}).scrollIntoViewIfNeeded();
        await take(3, 'organization-info-persists-after-authenticated-reload');
        await page.getByText('Generali', {exact: true}).click();
        await expect(page.locator('#balance-sheet')).toHaveValue('1');
        await expect(page.locator('#season-start-month')).toHaveValue('9');
        await take(4, 'general-settings-fiscal-presets-and-separate-season');
        await page.locator('#balance-sheet').selectOption('4');
        await page.locator('#balance-sheet-start-month').selectOption('10');
        await page.locator('#balance-sheet-start-day').selectOption('15');
        await expect(page.locator('#balance-sheet').locator('xpath=ancestor::div[contains(@class,"form-group")][1]'))
            .toContainText(/15\s+Ottobre/);
        await take(5, 'custom-fiscal-year-starts-october-fifteen');
        await page.locator('#season-start-month').selectOption('9');
        await page.locator('#season-start-day').selectOption('1');
        await setCheckbox(page.locator('[name="custom_end_date"]'), true);
        await page.locator('#season-end-month').selectOption('6');
        // The UI's end-month handler also resets the start day; recheck it.
        await page.locator('#season-start-day').selectOption('1');
        await page.locator('#season-end-day').selectOption('30');
        const durationBlock = label => page.locator('.form-group').filter({has: page.locator('label').filter({hasText: label})});
        await durationBlock('Durata delle iscrizioni').locator('select').selectOption('3');
        await durationBlock('Durata dei tesseramenti').locator('select').first().selectOption('3');
        await page.locator('#season-start-month').scrollIntoViewIfNeeded();
        await take(6, 'short-sport-season-and-registration-duration-before-save');
        await saveSettings();
        await page.reload();
        await expect(page.locator('#balance-sheet')).toHaveValue('4');
        await expect(page.locator('#balance-sheet-start-month')).toHaveValue('10');
        await expect(page.locator('#balance-sheet-start-day')).toHaveValue('15');
        await expect(page.locator('#season-start-month')).toHaveValue('9');
        await expect(page.locator('#season-end-month')).toHaveValue('6');
        await expect(page.locator('#season-end-day')).toHaveValue('30');
        const saved = (await json('profile/settings')).settings;
        for (const [key, value] of Object.entries({balance_sheet_year: '4', balance_sheet_start_month: 10,
            balance_sheet_start_day: 15, subscription_start_month: 9, subscription_start_day: 1,
            custom_end_date: true, subscription_end_month: 6, subscription_end_day: 30,
            subscription_duration: 3, membership_duration: 3})) expect(saved[key]).toBe(value);
        expect(await registrationState()).toEqual(existingRegistrations);
        await take(7, 'saved-fiscal-and-season-settings-persist-after-reload');
        const reader = await actor('reader');
        await reader.open('Impostazioni', '/#/profile');
        await reader.page.getByText('Generali', {exact: true}).click();
        await expect(reader.page.locator('#balance-sheet')).toBeDisabled();
        await expect(reader.page.locator('#season-start-month')).toBeDisabled();
        await expect(reader.page.locator('#bkn_form_password_update_submit')).toBeDisabled();
        await capture(reader.page, 8, 'reader-can-consult-fiscal-season-settings-with-disabled-writes');
        const denied = await reader.api('profile/settings', {method: 'POST', data: {...saved, balance_sheet_start_day: 1}});
        expect(denied.status()).toBe(403);
        expect((await json('profile/settings')).settings).toEqual(saved);
        // Restore the baseline using the same UI before another scenario runs.
        await page.locator('#balance-sheet').selectOption(originalSettings.balance_sheet_year);
        await setCheckbox(page.locator('[name="custom_end_date"]'), false);
        await durationBlock('Durata delle iscrizioni').locator('select').selectOption(String(originalSettings.subscription_duration));
        await durationBlock('Durata dei tesseramenti').locator('select').first().selectOption(String(originalSettings.membership_duration));
        await saveSettings();
        // Restore hidden custom end values explicitly through the existing form.
        await setCheckbox(page.locator('[name="custom_end_date"]'), true);
        await page.locator('#season-end-month').selectOption(String(originalSettings.subscription_end_month));
        await page.locator('#season-end-day').selectOption(String(originalSettings.subscription_end_day));
        await setCheckbox(page.locator('[name="custom_end_date"]'), false);
        await saveSettings();
        expect((await json('profile/settings')).settings).toEqual(originalSettings);
        await page.getByText('Informazioni Account', {exact: true}).click();
        for (const [placeholder, value] of Object.entries(originalFields))
            await page.getByPlaceholder(placeholder, {exact: true}).fill(value || '');
        await saveInfo();
        const restored = (await json('profile/info')).user_data.sport_association;
        for (const [key, value] of Object.entries({address: original.address, address_cap: original.address_cap,
            website: original.website, abbreviated: original.abbreviated, sport: original.sport}))
            expect(restored[key]).toBe(value || '');
        report.organization_settings = {organization: savedProfile.sport_association.denomination,
            address: savedProfile.sport_association.address, cap: savedProfile.sport_association.address_cap,
            website: savedProfile.sport_association.website, abbreviated: savedProfile.sport_association.abbreviated,
            sport: savedProfile.sport_association.sport, fiscal_type: saved.balance_sheet_year,
            fiscal_month: saved.balance_sheet_start_month, fiscal_day: saved.balance_sheet_start_day,
            season_month: saved.subscription_start_month, season_day: saved.subscription_start_day,
            short_season: saved.custom_end_date, season_end_month: saved.subscription_end_month,
            season_end_day: saved.subscription_end_day, subscription_duration: saved.subscription_duration,
            membership_duration: saved.membership_duration, persisted_after_reload: true,
            existing_registrations_unchanged: true, reader_settings_write_status: denied.status(),
            denial_left_settings_unchanged: true, baseline_restored: true};
        report.checks = ['actual owner profile PATCH persists association fields after authenticated reload',
            'actual general settings POST persists separate fiscal and sport periods plus durations',
            'existing registration dates/status/card/payment fields remain equal in the non-printing list API',
            'reader consults disabled controls and denied POST preserves settings', 'owner restores baseline through UI'];
        } finally {
            // Restore only the profile and settings of this run-owned association.
            const currentSettings = (await json('profile/settings')).settings;
            if (JSON.stringify(currentSettings) !== JSON.stringify(originalSettings))
                expect((await api('profile/settings', {method: 'POST', data: originalSettings})).status()).toBe(200);
            const own = (await json('profile/info')).user_data;
            const changed = Object.keys(originalFields).some(placeholder => {
                const key = {'Inserisci indirizzo...': 'address', 'Inserisci CAP...': 'address_cap', 'Inserisci URL sito web...': 'website',
                    'Inserisci nome abbreviato...': 'abbreviated', 'Inserisci sport...': 'sport'}[placeholder];
                return own.sport_association[key] !== original[key];
            });
            if (changed) expect((await api('profile/update', {method: 'PATCH', data: {user_data: {...own, sport_association: original}}})).status()).toBe(200);
            expect((await json('profile/settings')).settings).toEqual(originalSettings);
            expect((await json('profile/info')).user_data.sport_association).toEqual(original);
            expect(await registrationState()).toEqual(existingRegistrations);
        }
        report.organization_settings.scoped_cleanup_completed = true;
    }});
