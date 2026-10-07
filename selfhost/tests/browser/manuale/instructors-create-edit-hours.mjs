// All writes use the actual UI and backend; execution belongs to the run owner.
import {scenario, expect} from './scenario.mjs';
import {organizationBasicsAuthoredWorkflows} from '../../../../docs/manuale/organization-basics-authored-workflows.mjs';
const spec = organizationBasicsAuthoredWorkflows['instructors-create-edit-hours'];
import {instructorWorkflowSources} from './instructor-sources.mjs';
import {focusRegion} from './focus.mjs';
// Seeded and frozen-clock rows share timestamps (or the list is unordered), so compare
// record sets: identical rows and contents, independent of physical row order.
const records = rows => (Array.isArray(rows) ? rows.map(row => JSON.stringify(row))
    : Object.entries(rows).map(entry => JSON.stringify(entry))).sort();

await scenario({id: 'instructors-create-edit-hours', prefix: 'images/istruttori/creazione-ore',
    sources: spec.sources, actions: async ({page, api, open, actor, input, capture, report}) => {
        const take = async (number, checkpoint, locator) => {
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            await capture(page, number, checkpoint, locator);
        };
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const instructors = async () => {
            const response = await api('instructor/list');
            expect(response.status()).toBe(200);
            return (await response.json()).data;
        };
        const initial = await instructors();
        const owned = new Set();
        expect(initial).toHaveLength(0);
        try {
        await open('Attività', '/#/course/instructor/list/');
        await expect(page.getByRole('heading', {name: 'Istruttori', exact: false})).toBeVisible();
        await expect(page.locator('[data-row]')).toHaveCount(0);
        await take(1, 'empty-instructor-list-and-create-action');
        await page.getByRole('button', {name: 'Istruttore', exact: true}).click();
        await expect(page.getByRole('heading', {name: 'Nuovo Istruttore', exact: true})).toBeVisible();
        const form = page.locator('#instructor_form');
        for (const [name, value] of Object.entries({first_name: 'Paolo', last_name: 'Riva',
            email: 'paolo@example.test', born_date: '1985-05-10', born_city: 'Roma', born_province: 'RM',
            address_city: 'Roma', address: 'Via dello Sport', civic_number: '8', address_province: 'RM'}))
            await form.locator(`[name="${name}"]`).fill(value);
        await form.locator('[name="first_name"]').scrollIntoViewIfNeeded();
        await take(2, 'instructor-required-and-personal-data');
        await form.locator('[name="stipulated_contract_in"]').fill(input.reference_date);
        await form.locator('[name="study_title"]').fill('Laurea in scienze motorie');
        await form.locator('[name="default_hourly_billing"]').fill('15');
        await form.locator('[name="default_percentage_billing"]').fill('20');
        await expect(form.locator('[name="role"]')).toHaveValue('Istruttore');
        await expect(form.locator('[name="is_volunteer"]')).not.toBeChecked();
        await expect(form.locator('input[type="hidden"][name="associated_user_id"]')).toHaveValue('');
        const defaultRates = form.locator('[name="default_hourly_billing"]').locator('..').locator('..');
        await defaultRates.scrollIntoViewIfNeeded();
        await expect(form.locator('[name="default_hourly_billing"]')).toBeInViewport({ratio: 1});
        await expect(form.locator('[name="default_percentage_billing"]')).toBeInViewport({ratio: 1});
        await take(3, 'instructor-contract-default-rates-and-unlinked-account', focusRegion(defaultRates,
            form.getByRole('heading', {name: 'Informazioni contratto (se applicabile)', exact: true})));
        const creation = page.waitForResponse(response => new URL(response.url()).pathname === '/api/instructor/add'
            && response.request().method() === 'POST');
        await form.getByRole('button', {name: 'Crea Istruttore', exact: true}).click();
        expect((await creation).status()).toBe(200);
        await expect(page).toHaveURL(/#\/course\/instructor\/list\/?$/);
        await page.reload();
        const list = await instructors();
        expect(list).toHaveLength(1);
        const created = list[0];
        expect(created.first_name).toBe('Paolo');
        expect(created.last_name).toBe('Riva');
        expect(created.email).toBe('paolo@example.test');
        expect(created.user).toBe(input.user_id);
        expect(created.associated_user_id).toBeNull();
        expect(Number(created.default_hourly_billing)).toBe(15);
        expect(Number(created.default_percentage_billing)).toBe(20);
        const uid = created.instructor_id; owned.add(uid);
        const instructorRow = page.locator('[data-row]').filter({hasText: 'PAOLO RIVA'});
        await expect(instructorRow).toBeVisible();
        await expect(instructorRow).toContainText('nessuno');
        await take(4, 'created-instructor-persists-after-reload');
        await instructorRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        const edit = page.locator(`#editModal-${uid}`);
        await expect(edit).toBeVisible();
        await edit.locator('[name="default_hourly_billing"]').fill('18');
        await edit.locator('[name="default_hourly_billing"]').scrollIntoViewIfNeeded();
        await take(5, 'edit-instructor-default-hourly-rate');
        const update = page.waitForResponse(response => new URL(response.url()).pathname === `/api/instructor/${uid}/update`
            && response.request().method() === 'PATCH');
        await edit.getByRole('button', {name: 'Salva', exact: true}).click();
        expect((await update).status()).toBe(200);
        await page.reload();
        const edited = (await instructors())[0];
        expect(edited.instructor_id).toBe(uid);
        expect(Number(edited.default_hourly_billing)).toBe(18);
        await page.locator('[data-row]').filter({hasText: 'PAOLO RIVA'}).getByRole('link', {name: 'PAOLO RIVA', exact: true}).click();
        await expect(page.getByText('Scheda compensi', {exact: true})).toBeVisible();
        await expect(page.locator('[data-row]')).toHaveCount(0);
        await expect(page.locator('.card-widget').filter({hasText: 'ORE LAVORATE'})).toContainText('0,00');
        await take(6, 'instructor-compensation-card-before-hours');
        await page.getByRole('button', {name: 'Aggiungi', exact: true}).click();
        const hourModal = page.locator(`#modal-${uid}`);
        await expect(hourModal).toBeVisible();
        await expect(hourModal).toContainText('Aggiungi Orario Istruttore');
        await expect(hourModal.locator('[name="hourly_billing"]')).toHaveValue('18');
        await hourModal.locator('[name="date"]').fill(input.reference_date);
        await hourModal.locator('[name="paid"]').selectOption('false');
        await hourModal.locator('[name="hours"]').fill('3');
        await hourModal.locator('[name="notes"]').fill('Lezione dimostrativa di ginnastica');
        await expect(hourModal.locator('input[name="amount"]')).toHaveValue('54');
        await expect(hourModal).toContainText('54,00 €');
        await take(7, 'hourly-hours-default-rate-unpaid-and-calculated-total', hourModal.locator('.modal-content'));
        const hourCreation = page.waitForResponse(response => new URL(response.url()).pathname === `/api/instructor/${uid}/hours/add`
            && response.request().method() === 'POST');
        await hourModal.getByRole('button', {name: 'Salva', exact: true}).click();
        expect((await hourCreation).status()).toBe(200);
        await expect(hourModal).not.toBeVisible();
        await page.reload();
        const hourRow = page.locator('[data-row]').filter({hasText: 'Lezione dimostrativa di ginnastica'});
        await expect(hourRow).toBeVisible();
        await expect(hourRow).toContainText('54,00');
        await expect(hourRow).toContainText(/Da pagare/i);
        const hours = async () => {
            const response = await api(`instructor/${uid}/hours/list`);
            expect(response.status()).toBe(200);
            return await response.json();
        };
        const savedHours = await hours();
        expect(savedHours.meta.total).toBe(1);
        const hour = savedHours.data[0];
        expect(Number(hour.hours)).toBe(3);
        expect(Number(hour.hourly_billing)).toBe(18);
        expect(Number(hour.amount)).toBe(54);
        expect(hour.compensation_type).toBe('hourly');
        expect(hour.paid).toBe(false);
        expect(hour.payment).toBeNull();
        expect(hour.document).toBeNull();
        expect(hour.notes).toBe('Lezione dimostrativa di ginnastica');
        const monthStart = input.reference_date.slice(0, 8) + '01';
        const italianDate = date => date.split('-').reverse().join('/');
        const info = await api(`instructor/${uid}/info?date_range=${encodeURIComponent(italianDate(monthStart) + ' al ' + italianDate(input.reference_date))}`);
        expect(info.status()).toBe(200);
        const stats = (await info.json()).stats;
        expect(Number(stats.hours)).toBe(3);
        expect(Number(stats.total_amount)).toBe(54);
        expect(Number(stats.total_amount_to_pay)).toBe(54);
        expect(Number(stats.total_amount_paid)).toBe(0);
        await expect(page.locator('.card-widget').filter({hasText: 'ORE LAVORATE'})).toContainText('3,00');
        for (const [label, value] of [['COMPENSO TOTALE', '54,00'], ['COMPENSO DA PAGARE', '54,00'],
            ['COMPENSO PAGATO', '0,00']])
            await expect(page.locator('.card-widget').filter({hasText: label})).toContainText(value);
        await take(8, 'saved-hours-unpaid-and-summary-persist-after-reload');
        const reader = await actor('reader');
        await reader.open('Attività', '/#/course/instructor/list/');
        await expect(reader.page.getByRole('button', {name: 'Istruttore', exact: true})).toBeDisabled();
        const readerRow = reader.page.locator('[data-row]').filter({hasText: 'PAOLO RIVA'});
        await expect(readerRow.locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled();
        await readerRow.getByRole('link', {name: 'PAOLO RIVA', exact: true}).click();
        await expect(reader.page.getByRole('button', {name: 'Aggiungi', exact: true})).toBeDisabled();
        await expect(reader.page.locator('[data-row]').filter({hasText: 'Lezione dimostrativa di ginnastica'})).toBeVisible();
        const denied = [];
        for (const [route, method, data] of [
            ['instructor/add', 'POST', {first_name: 'Vietato', last_name: 'Esempio', email: 'vietato@example.test'}],
            [`instructor/${uid}/update`, 'PATCH', {first_name: 'Vietato'}],
            [`instructor/${uid}/hours/add`, 'POST', {hours: 1, amount: 99}],
        ]) {
            const response = await reader.api(route, {method, data});
            expect(response.status()).toBe(403);
            denied.push(response.status());
        }
        expect((await instructors())[0]).toEqual(edited);
        expect(await hours()).toEqual(savedHours);
        report.instructor_workflow = {initial_instructors: initial.length, final_instructors: list.length,
            owner_preserved: created.user === input.user_id, associated_account: false,
            initial_hourly_rate: Number(created.default_hourly_billing), hourly_rate: Number(edited.default_hourly_billing),
            percentage_rate: Number(created.default_percentage_billing), hours: Number(hour.hours),
            amount: Number(hour.amount), compensation_type: hour.compensation_type, paid: hour.paid,
            payment_created: hour.payment !== null, document_created: hour.document !== null,
            total_amount_to_pay: Number(stats.total_amount_to_pay), total_amount_paid: Number(stats.total_amount_paid),
            persisted_after_reload: true, reader_create_status: denied[0], reader_update_status: denied[1],
            reader_hours_create_status: denied[2], denial_left_state_unchanged: true};
        report.checks = ['actual UI creates instructor with mandatory email and default rates',
            'actual profile PATCH persists edited default rate', 'actual hours POST inherits edited rate and calculates 3 × 18 = 54',
            'reload preserves one unpaid hour row and summary without payment or document',
            'reader sees existing records and disabled write buttons; three 403 denials preserve state'];
        } finally {
            // Recover only this invocation’s known new instructor, including an interrupted create.
            for (const row of await instructors()) if (!initial.some(old => old.instructor_id === row.instructor_id)
                && row.first_name === 'Paolo' && row.last_name === 'Riva' && row.email === 'paolo@example.test'
                && row.user === input.user_id) owned.add(row.instructor_id);
            for (const uid of owned) {
                const response = await api(`instructor/${uid}/hours/list`); expect(response.status()).toBe(200);
                for (const row of (await response.json()).data) {
                    expect(row.payment).toBeNull(); expect(row.document).toBeNull();
                    expect((await api(`instructor/${uid}/hours/${row.instructor_hours_id}/delete`, {method: 'DELETE'})).status()).toBe(200);
                }
                expect((await api(`instructor/${uid}/delete`, {method: 'DELETE'})).status()).toBe(200);
            }
            expect(records(await instructors())).toEqual(records(initial));
        }
        report.instructor_workflow.owned_resources_cleaned = true;
    }});
