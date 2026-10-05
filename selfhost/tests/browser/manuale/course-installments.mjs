import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {courseSettingsAuthoredWorkflows} from '../../../../docs/manuale/course-settings-authored-workflows.mjs';

const id = 'course-installments-manage';
const spec = courseSettingsAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const read = async route => {
            const response = await api(route);
            expect(response.status()).toBe(200);
            return response.json();
        };
        const take = async (checkpoint, focus) => {
            const index = spec.checkpoints.findIndex(item => item.id === checkpoint);
            expect(index).toBeGreaterThanOrEqual(0);
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await capture(page, index + 1, checkpoint, focus);
        };
        const day = offset => {
            const value = new Date(input.reference_date + 'T12:00:00Z');
            value.setUTCDate(value.getUTCDate() + offset);
            return value.toISOString().slice(0, 10);
        };
        const dates = [day(-7), day(7), day(14)];
        const italian = date => date.split('-').reverse().join('/');
        const originalCourse = (await read(`course/${input.course_id}/overview`)).data.course;
        const originalPayments = Object.values((await read('payment/list?pagination[perpage]=100')).data);
        const settings = (await read('profile/settings')).settings;
        expect(settings.full_installments_plan).toBe(false);
        await open('Attività', '/#/course/list');
        await page.getByRole('button', {name: 'Corso o Abbonamento', exact: true}).click();
        const drawer = page.locator('.drawer').filter({has: page.getByPlaceholder('Titolo Corso')});
        await expect(drawer).toBeVisible();
        await drawer.getByPlaceholder('Titolo Corso').fill('Ginnastica con piano rate');
        await drawer.locator('[contenteditable="true"]').fill('Piano dimostrativo con tre scadenze concordate.');
        await drawer.locator('input[name="fee"]').fill('300,00');
        const switchBlock = (scope, label) => scope.getByText(label, {exact: true}).locator('xpath=ancestor::div[contains(@class,"form-group")][1]');
        await setCheckbox(switchBlock(drawer, 'Rateizza').locator('input[type="checkbox"]'), true);
        await drawer.locator('input[type="number"]').fill('3');
        await drawer.getByText('Crea Rate', {exact: true}).click();
        for (let index = 0; index < dates.length; index++) {
            await drawer.locator(`input#datepicker_${index}[type="date"]`).fill(dates[index]);
            await expect(drawer.locator(`input#datepicker_${index}[type="date"]`)).toHaveValue(dates[index]);
        }
        await setCheckbox(switchBlock(drawer, 'Unica soluzione').locator('input[type="checkbox"]'), true);
        await drawer.locator('input[name="one_fee"]').fill('280,00');
        await drawer.getByText('Rateizzazione della quota attività', {exact: true}).scrollIntoViewIfNeeded();
        await take('uniform-installments-and-single-fee-before-save', drawer.locator('#bkn_form'));
        const creation = page.waitForResponse(response => new URL(response.url()).pathname === '/api/course/add'
            && response.request().method() === 'POST');
        await drawer.getByRole('button', {name: 'Salva', exact: true}).click();
        expect((await creation).status()).toBe(200);
        await expect(drawer).not.toBeVisible();
        const created = (await read('course/list?all=1')).data.find(item => item.title === 'Ginnastica con piano rate');
        expect(created).toBeTruthy();
        const courseId = created.course_id;
        const course = async () => (await read(`course/${courseId}/overview`)).data.course;
        const memberships = async () => (await read(`course-subscriptions/list?course_id=${courseId}`)).data;
        const payments = async () => Object.values((await read('payment/list?pagination[perpage]=100')).data);
        const coursePayments = async () => (await payments()).filter(item => item.meta?.name === created.title
            || item.meta?.course_id === courseId || item.description?.includes(created.title) || item.course?.value === courseId);
        const assertPlan = async amounts => {
            const saved = await course();
            expect(saved.multi_payments).toBe(true);
            expect(saved.one_fee_payment).toBe(true);
            expect(Number(saved.one_fee)).toBe(280);
            expect(Number(saved.fee)).toBe(300);
            expect(saved.events.map(event => Number(event.amount))).toEqual(amounts);
            expect(saved.events.map(event => event.payment_date)).toEqual(dates.map(italian));
            return saved;
        };
        await assertPlan([100, 100, 100]);
        await page.locator('[data-row]').filter({hasText: created.title}).getByText(created.title, {exact: true}).click();
        await page.reload();
        await expect(page.locator('.card-title').getByText(created.title, {exact: true})).toBeVisible();
        await take('new-installment-course-persists-after-reload');
        // Exercise the creation generator itself, including a preparatory removal.
        // The overview editor alone cannot prove its differing-amount controls.
        const alternativeTitle = 'Piano dimostrativo con importi differenti';
        let alternativeId = null;
        try {
            await open('Attività', '/#/course/list');
            await page.getByRole('button', {name: 'Corso o Abbonamento', exact: true}).click();
            await expect(drawer).toBeVisible();
            await drawer.getByPlaceholder('Titolo Corso').fill(alternativeTitle);
            await drawer.locator('[contenteditable="true"]').fill('Prova del generatore e del totale dopo la rimozione di una riga.');
            await drawer.locator('input[name="fee"]').fill('300,00');
            await setCheckbox(switchBlock(drawer, 'Rateizza').locator('input[type="checkbox"]'), true);
            await drawer.locator('input[type="number"]').fill('3');
            await drawer.getByText('Crea Rate', {exact: true}).click();
            for (let index = 0; index < dates.length; index++) {
                await drawer.locator(`input#datepicker_${index}[type="date"]`).fill(dates[index]);
                await expect(drawer.locator(`input#datepicker_${index}[type="date"]`)).toHaveValue(dates[index]);
            }
            await setCheckbox(drawer.getByText('Permetti rate di importi diversi', {exact: true})
                .locator('xpath=ancestor::div[contains(@class,"form-group")][1]').locator('input[type="checkbox"]'), true);
            for (const [index, amount] of [150, 100, 50].entries()) {
                await drawer.locator(`#amount_rate_${index}`).fill(amount.toFixed(2).replace('.', ','));
                await drawer.locator(`#amount_rate_${index}`).blur();
                await expect(drawer.locator(`#amount_rate_${index}`)).toHaveValue(amount.toFixed(2).replace('.', ','));
            }
            await expect(drawer.locator('input[name="fee"]').first()).toHaveValue('300,00');
            await take('different-amounts-in-creation-generator', drawer.locator('#bkn_form'));
            await drawer.locator('#amount_rate_2').locator('xpath=ancestor::div[contains(@class,"form-group")][1]')
                .getByRole('button', {name: 'Elimina', exact: true}).click();
            await expect(drawer.locator('#amount_rate_2')).toHaveCount(0);
            await expect(drawer.locator('input[name="fee"]').first()).toHaveValue('250,00');
            await take('preparatory-rate-removal-updates-total', drawer.locator('#bkn_form'));
            const alternativeCreation = page.waitForResponse(response => new URL(response.url()).pathname === '/api/course/add'
                && response.request().method() === 'POST');
            await drawer.getByRole('button', {name: 'Salva', exact: true}).click();
            const response = await alternativeCreation;
            const submitted = response.request().postDataJSON().new_course;
            // Discover the owned ID before asserting, so failed checks can still clean up.
            const savedAlternative = (await read('course/list?all=1')).data.find(item => item.title === alternativeTitle);
            alternativeId = savedAlternative?.course_id || null;
            expect(response.status()).toBe(200); expect(savedAlternative).toBeTruthy();
            expect(submitted.events.map(event => Number(event.amount))).toEqual([150, 100]);
            expect(submitted.events.map(event => event.payment_date)).toEqual(dates.slice(0, 2).map(italian));
            expect(Number(submitted.fee)).toBe(250);
            const persisted = (await read(`course/${alternativeId}/overview`)).data.course;
            expect(persisted.multi_payments).toBe(true);
            expect(persisted.events.map(event => Number(event.amount))).toEqual([150, 100]);
            expect(persisted.events.map(event => event.payment_date)).toEqual(dates.slice(0, 2).map(italian));
            expect(Number(persisted.fee)).toBe(250);
            await expect(drawer).not.toBeVisible();
            await page.locator('[data-row]').filter({hasText: alternativeTitle}).getByText(alternativeTitle, {exact: true}).click();
            await page.reload();
            await expect(page.locator('.card-title').getByText(alternativeTitle, {exact: true})).toBeVisible();
            await take('creation-alternative-persists-after-reload');
            expect((await read(`course-subscriptions/list?course_id=${alternativeId}`)).data).toHaveLength(0);
        } finally {
            if (alternativeId) {
                expect((await api(`course/${alternativeId}/delete`, {method: 'POST'})).status()).toBe(200);
                expect((await read('course/list?all=1')).data.some(item => item.course_id === alternativeId)).toBe(false);
            }
        }
        await open('Attività', '/#/course/list');
        await page.locator('[data-row]').filter({hasText: created.title}).getByText(created.title, {exact: true}).click();
        await expect(page.locator('.card-title').getByText(created.title, {exact: true})).toBeVisible();
        const editAmounts = async amounts => {
            await page.locator('.card-toolbar').getByRole('button', {name: 'Modifica', exact: true}).click();
            for (let index = 0; index < amounts.length; index++) {
                await page.locator(`#bkn_inputmask_fee_${index}`).fill(amounts[index].toFixed(2).replace('.', ','));
                await page.locator(`#bkn_inputmask_fee_${index}`).blur();
            }
        };
        const saveCourse = async () => {
            const saved = page.waitForResponse(response => new URL(response.url()).pathname === `/api/course/${courseId}/update`
                && response.request().method() === 'PATCH');
            await page.getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await saved).status()).toBe(200);
            await expect(page.locator('.card-toolbar').getByRole('button', {name: 'Modifica', exact: true})).toBeVisible();
        };
        await editAmounts([150, 100, 50]);
        await take('distinct-amounts-in-course-editor', page.locator('#bkn_inputmask_fee_0').locator('xpath=ancestor::div[contains(@class,"row")][1]'));
        await saveCourse();
        await page.reload();
        await assertPlan([150, 100, 50]);
        await take('distinct-course-plan-persists-after-reload');
        await page.getByRole('button', {name: 'Aggiungi tesserato', exact: true}).click();
        const assignment = page.locator('#subscription-modal');
        await expect(assignment).toBeVisible();
        const athletePicker = assignment.locator('label[for="selectedAthletes"]').locator('xpath=ancestor::div[contains(@class,"form-group")][1]');
        for (const [index, name] of ['GIULIA BIANCHI', 'LUCA VERDI'].entries()) {
            await athletePicker.locator('input:not([type="hidden"])').click();
            await athletePicker.locator('input:not([type="hidden"])').fill(name);
            await athletePicker.locator('input:not([type="hidden"])').press('ArrowDown');
            await athletePicker.locator('.list-item').filter({hasText: name}).click();
            await expect.poll(async () => JSON.parse(await assignment.locator('input[name="selectedAthletes"]').inputValue())
                .map(item => item.value)).toContain(input.subscription_ids[index]);
            await expect(athletePicker.locator('.svelte-select-list')).toHaveCount(0);
        }
        const planPicker = modal => modal.locator('label[for="selectedEvents"]').locator('xpath=ancestor::div[contains(@class,"form-group")][1]');
        expect(JSON.parse(await assignment.locator('input[name="selectedEvents"]').inputValue())).toHaveLength(3);
        await take('explicit-selection-includes-historical-installment', assignment.locator('.modal-content'));
        const assigned = page.waitForResponse(response => new URL(response.url()).pathname === '/api/course-subscriptions/add'
            && response.request().method() === 'POST');
        await assignment.getByRole('button', {name: 'Aggiungi', exact: true}).click();
        expect((await assigned).status()).toBe(201);
        await expect(assignment).not.toBeVisible();
        const assignedRows = await memberships();
        const amountsById = rates => [...rates].sort((left, right) => left.id - right.id).map(rate => Number(rate.amount));
        expect(assignedRows).toHaveLength(2);
        for (const entry of assignedRows) {
            expect(entry.installments.map(rate => rate.id).sort()).toEqual([0, 1, 2]);
            expect(amountsById(entry.installments)).toEqual([150, 100, 50]);
            expect(entry.installments.every(rate => rate.paid === false)).toBe(true);
            expect(entry.installments.find(rate => rate.id === 0).payment_date).toBe(dates[0]);
        }
        const giulia = assignedRows.find(entry => entry.subscription.subscription_id === input.subscription_ids[0]);
        const luca = assignedRows.find(entry => entry.subscription.subscription_id === input.subscription_ids[1]);
        expect(giulia).toBeTruthy(); expect(luca).toBeTruthy();
        const lucaSnapshot = JSON.stringify(luca);
        const initialPayments = await coursePayments();
        expect(initialPayments).toHaveLength(6);
        expect(initialPayments.every(payment => !payment.paid && payment.sport_association === input.association_id)).toBe(true);
        const giuliaRow = () => page.locator('[data-row]').filter({hasText: 'GIULIA BIANCHI'});
        const lucaRow = () => page.locator('[data-row]').filter({hasText: 'LUCA VERDI'});
        await page.reload();
        await expect(giuliaRow()).toBeVisible(); await expect(lucaRow()).toBeVisible();
        await giuliaRow().getByRole('button', {name: 'Pagamenti', exact: true}).click();
        const paymentModal = page.locator('#payments-modal-' + input.subscription_ids[0]);
        await expect(paymentModal).toBeVisible();
        await expect(paymentModal.locator('[data-row]')).toHaveCount(3);
        await take('assigned-installments-and-course-payments', paymentModal.locator('.modal-content'));
        await paymentModal.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
        await expect(paymentModal).not.toBeVisible();
        await giuliaRow().locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        await expect(assignment).toBeVisible();
        await expect(assignment).toContainText('Giulia'); await expect(assignment).toContainText('Bianchi');
        expect(JSON.parse(await assignment.locator('input[name="selectedEvents"]').inputValue())
            .map(rate => rate.value).sort()).toEqual([0, 1, 2]);
        await take('assigned-plan-reopened-for-correct-athlete', assignment.locator('.modal-content'));
        await assignment.getByRole('button', {name: 'Annulla', exact: true}).click();
        await expect(assignment).not.toBeVisible();
        // The editor updates the general plan, without rewriting existing rate amounts.
        await editAmounts([150, 110, 40]); await saveCourse(); await assertPlan([150, 110, 40]);
        await giuliaRow().locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        const individual = page.locator('#subscription-modal');
        await expect(individual).toBeVisible();
        const picker = planPicker(individual);
        const historic = picker.locator('.multi-item').filter({hasText: 'n.1 '});
        await historic.locator('.multi-item-clear').click();
        const selected = JSON.parse(await individual.locator('input[name="selectedEvents"]').inputValue());
        expect(selected.map(rate => rate.value).sort()).toEqual([1, 2]);
        // Existing rows still carry 100 and 50, despite the general 110/40 plan.
        await expect(picker).toContainText('100'); await expect(picker).toContainText('50');
        await take('individual-selection-before-update', individual.locator('.modal-content'));
        const changed = page.waitForResponse(response => new URL(response.url()).pathname === `/api/course-subscriptions/${giulia.course_subscription_id}/update`
            && response.request().method() === 'PATCH');
        await individual.getByRole('button', {name: 'Modifica', exact: true}).click();
        expect((await changed).status()).toBe(200);
        await expect(individual).not.toBeVisible();
        await page.reload();
        const afterIndividual = (await memberships()).find(entry => entry.course_subscription_id === giulia.course_subscription_id);
        expect(afterIndividual.installments.map(rate => rate.id).sort()).toEqual([1, 2]);
        expect(amountsById(afterIndividual.installments)).toEqual([100, 50]);
        expect(afterIndividual.installments.map(rate => rate.course_subscription_installment_id))
            .toEqual(giulia.installments.filter(rate => rate.id !== 0).map(rate => rate.course_subscription_installment_id));
        expect(await coursePayments()).toHaveLength(5);
        const originalHistoricPayment = initialPayments.find(payment => payment.associate.first_name === 'Giulia'
            && /Rata n\.1\b/.test(payment.description));
        expect(originalHistoricPayment).toBeTruthy();
        expect((await payments()).some(payment => payment.payment_id === originalHistoricPayment.payment_id)).toBe(false);
        expect(JSON.stringify((await memberships()).find(entry => entry.course_subscription_id === luca.course_subscription_id))).toBe(lucaSnapshot);
        await giuliaRow().locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        await expect(individual).toBeVisible();
        expect(JSON.parse(await individual.locator('input[name="selectedEvents"]').inputValue())).toHaveLength(2);
        await take('individual-selection-persists-after-reload', individual.locator('.modal-content'));
        await individual.getByRole('button', {name: 'Annulla', exact: true}).click();
        await expect(individual).not.toBeVisible();
        // Adding a missing rate creates one new payment, retaining existing amounts/IDs.
        await giuliaRow().locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        await expect(individual).toBeVisible();
        await expect(individual).toContainText('Giulia');
        await expect(individual).toContainText('Bianchi');
        await picker.locator('input:not([type="hidden"])').fill('N.1');
        await page.locator('.list-item').filter({hasText: /N\.1 /}).click();
        expect(JSON.parse(await individual.locator('input[name="selectedEvents"]').inputValue())
            .map(rate => rate.value).sort()).toEqual([0, 1, 2]);
        await take('individual-missing-rate-selected', individual.locator('.modal-content'));
        const saveIndividual = async () => {
            const response = page.waitForResponse(response => new URL(response.url()).pathname === `/api/course-subscriptions/${giulia.course_subscription_id}/update`
                && response.request().method() === 'PATCH');
            await individual.getByRole('button', {name: 'Modifica', exact: true}).click();
            const saved = await response;
            expect(saved.status()).toBe(200);
            await expect(individual).not.toBeVisible();
            await page.reload();
            return (await memberships()).find(entry => entry.course_subscription_id === giulia.course_subscription_id);
        };
        const reattached = await saveIndividual();
        expect(amountsById(reattached.installments)).toEqual([150, 100, 50]);
        expect(reattached.installments.find(rate => rate.id === 0).course_subscription_installment_id)
            .not.toBe(giulia.installments.find(rate => rate.id === 0).course_subscription_installment_id);
        for (const retained of afterIndividual.installments)
            expect(reattached.installments.find(rate => rate.id === retained.id)).toEqual(retained);
        const reattachedPayments = await coursePayments();
        expect(reattachedPayments).toHaveLength(6);
        const addedPayment = reattachedPayments.find(payment => payment.associate.first_name === 'Giulia'
            && /Rata n\.1\b/.test(payment.description));
        expect(addedPayment).toBeTruthy(); expect(Number(addedPayment.amount)).toBe(150);
        expect(addedPayment.payment_id).not.toBe(originalHistoricPayment.payment_id);
        await giuliaRow().locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        await expect(individual).toBeVisible();
        expect(JSON.parse(await individual.locator('input[name="selectedEvents"]').inputValue())).toHaveLength(3);
        await take('individual-added-rate-persists-after-reload', individual.locator('.modal-content'));
        await individual.getByRole('button', {name: 'Annulla', exact: true}).click();
        await expect(individual).not.toBeVisible();
        await giuliaRow().getByRole('button', {name: 'Pagamenti', exact: true}).click();
        await expect(paymentModal).toBeVisible();
        await expect(paymentModal.locator('[data-row]')).toHaveCount(3);
        await take('individual-added-rate-and-existing-payment-amounts', paymentModal.locator('.modal-content'));
        const retainedPayment = reattachedPayments.find(payment => payment.associate.first_name === 'Giulia'
            && /Rata n\.2\b/.test(payment.description));
        expect(Number(retainedPayment.amount)).toBe(100);
        await paymentModal.locator('[data-row]').filter({hasText: retainedPayment.description})
            .getByText(retainedPayment.description, {exact: true}).click();
        const paymentDetails = page.locator('.drawer').filter({hasText: 'Dettagli Pagamento'});
        await expect(paymentDetails).toBeVisible();
        await expect(paymentDetails).toContainText(retainedPayment.description);
        await expect(paymentDetails).toContainText('100');
        await take('individual-payment-keeps-recorded-amount', paymentDetails);
        await paymentDetails.locator('button.close').click();
        await expect(paymentDetails).not.toBeVisible();
        await paymentModal.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
        await expect(paymentModal).not.toBeVisible();
        // Restore the original two-rate branch before the existing bulk checks.
        await giuliaRow().locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        await expect(individual).toBeVisible();
        await picker.locator('.multi-item').filter({hasText: 'n.1 '}).locator('.multi-item-clear').click();
        const restoredIndividual = await saveIndividual();
        expect(restoredIndividual.installments).toEqual(afterIndividual.installments);
        expect(await coursePayments()).toHaveLength(5);
        expect((await payments()).some(payment => payment.payment_id === addedPayment.payment_id)).toBe(false);
        expect(JSON.stringify((await memberships()).find(entry => entry.course_subscription_id === luca.course_subscription_id))).toBe(lucaSnapshot);
        const bulk = async (operation, checkpoint, confirmationButton) => {
            await setCheckbox(giuliaRow().locator('input[type="checkbox"]'), true);
            await page.getByRole('button', {name: 'Operazioni su selezionati', exact: true}).click();
            await page.locator('.dropdown-menu:visible .navi-item').filter({hasText: new RegExp('^\\s*' + operation + '\\s+Sostituisce')}).click();
            const confirmation = page.getByRole('dialog');
            await expect(confirmation).toBeVisible();
            await take(checkpoint, confirmation);
            const request = page.waitForResponse(response => new URL(response.url()).pathname === `/api/course/${courseId}/overview/${input.subscription_ids[0]}/update`
                && response.request().method() === 'POST');
            await confirmation.getByRole('button', {name: confirmationButton, exact: true}).click();
            expect((await request).status()).toBe(200);
            await expect(confirmation).not.toBeVisible();
            await page.reload();
        };
        await bulk('Assegna piano rate', 'future-plan-operation-confirmation', 'Procedi');
        const ordinary = (await memberships()).find(entry => entry.course_subscription_id === giulia.course_subscription_id);
        expect(ordinary.installments.map(rate => rate.id).sort()).toEqual([1, 2]);
        expect(amountsById(ordinary.installments)).toEqual([110, 40]);
        await expect(giuliaRow()).toBeVisible();
        await take('future-plan-persists-after-reload');
        await bulk('Assegna piano rate completo', 'full-plan-operation-confirmation', 'Procedi');
        const complete = (await memberships()).find(entry => entry.course_subscription_id === giulia.course_subscription_id);
        expect(complete.installments.map(rate => rate.id).sort()).toEqual([0, 1, 2]);
        expect(amountsById(complete.installments)).toEqual([150, 110, 40]);
        await expect(giuliaRow()).toBeVisible();
        await take('full-plan-persists-after-reload');
        await bulk('Cambia in unica soluzione', 'single-fee-operation-confirmation', 'Applica');
        const finalRows = await memberships();
        const single = finalRows.find(entry => entry.course_subscription_id === giulia.course_subscription_id);
        expect(single.installments).toHaveLength(0);
        expect(single.multi_payments).toBe(false); expect(single.one_fee_payment).toBe(true);
        expect(JSON.stringify(finalRows.find(entry => entry.course_subscription_id === luca.course_subscription_id))).toBe(lucaSnapshot);
        const finalPayments = await coursePayments();
        const singlePayment = finalPayments.find(payment => payment.associate.associate_id === single.subscription.associate.associate_id);
        expect(singlePayment).toBeTruthy();
        expect(Number(singlePayment.amount)).toBe(280); expect(singlePayment.paid).toBe(false);
        expect(finalPayments).toHaveLength(4);
        expect(finalPayments.filter(payment => payment.associate.first_name === 'Giulia')).toHaveLength(1);
        expect(finalPayments.every(payment => payment.sport_association === input.association_id && !payment.paid)).toBe(true);
        const finalBaseline = (await read(`course/${input.course_id}/overview`)).data.course;
        expect(finalBaseline).toEqual(originalCourse);
        for (const payment of originalPayments)
            expect((await payments()).find(entry => entry.payment_id === payment.payment_id)).toEqual(payment);
        await expect(giuliaRow()).toBeVisible(); await expect(lucaRow()).toBeVisible();
        await take('single-fee-and-other-athlete-preserved');
        const reader = await actor('reader');
        await reader.open('Attività', '/#/course/list');
        await expect(reader.page.locator('[data-row]').filter({hasText: created.title})).toBeVisible();
        await expect(reader.page.getByRole('button', {name: 'Corso o Abbonamento', exact: true})).toHaveCount(0);
        expect((await reader.api('course/add', {method: 'POST', data: {new_course: {
            title: 'Creazione non consentita', description: 'Tentativo del lettore', course_type: 1,
            fee: 300, multi_payments_split: true, events: (await course()).events,
        }, subscriptions: []}})).status()).toBe(403);
        expect((await reader.api('course-subscriptions/add', {method: 'POST', data: [{course: courseId, subscription_id: input.subscription_ids[2]}]})).status()).toBe(403);
        expect((await reader.api(`course-subscriptions/${giulia.course_subscription_id}/update`, {method: 'PATCH', data: {events: []}})).status()).toBe(403);
        expect((await reader.api(`course/${courseId}/overview/${input.subscription_ids[0]}/update`, {method: 'POST', data: {all: true, multi_payments: true}})).status()).toBe(403);
        expect(await memberships()).toEqual(finalRows);
        // Empty explicit selection invokes the server's default-plan branch. A
        // preselected full plan would not prove the global preference at all.
        let trialId = null;
        let trialPaymentIds = [];
        let defaultOrdinary;
        let defaultComplete;
        const openOwnedCourse = async () => {
            await open('Attività', '/#/course/list');
            await page.locator('[data-row]').filter({hasText: created.title}).getByText(created.title, {exact: true}).click();
            await expect(page.locator('.card-title').getByText(created.title, {exact: true})).toBeVisible();
        };
        const removeOwnedTrial = async () => {
            if (!trialId) return;
            if ((await memberships()).some(entry => entry.course_subscription_id === trialId))
                expect((await api(`course-subscriptions/${trialId}/delete`, {method: 'DELETE'})).status()).toBe(204);
            // Only payments discovered for this scenario-owned trial are eligible.
            // Cleanup may repair an orphan, but does not attest product deletion behavior.
            for (const paymentId of trialPaymentIds) {
                if ((await payments()).some(payment => payment.payment_id === paymentId))
                    expect((await api(`payment/${paymentId}/delete`, {method: 'DELETE'})).status()).toBe(200);
            }
            expect((await memberships()).some(entry => entry.course_subscription_id === trialId)).toBe(false);
            expect((await payments()).some(payment => trialPaymentIds.includes(payment.payment_id))).toBe(false);
            trialId = null; trialPaymentIds = [];
        };
        const assignDefault = async (selectionCheckpoint, expectedIds, expectedAmounts) => {
            await openOwnedCourse();
            await page.getByRole('button', {name: 'Aggiungi tesserato', exact: true}).click();
            await expect(assignment).toBeVisible();
            await athletePicker.locator('input:not([type="hidden"])').fill('SARA CONTI');
            await page.locator('.list-item').filter({hasText: 'SARA CONTI'}).click();
            const defaultsPicker = planPicker(assignment);
            expect(JSON.parse(await assignment.locator('input[name="selectedEvents"]').inputValue())).toHaveLength(3);
            for (let remaining = 3; remaining > 0; remaining--)
                await defaultsPicker.locator('.multi-item-clear').first().click();
            expect(JSON.parse(await assignment.locator('input[name="selectedEvents"]').inputValue() || '[]') || []).toEqual([]);
            await take(selectionCheckpoint, assignment.locator('.modal-content'));
            const beforeIds = new Set((await payments()).map(payment => payment.payment_id));
            const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/course-subscriptions/add'
                && response.request().method() === 'POST');
            await assignment.getByRole('button', {name: 'Aggiungi', exact: true}).click();
            const saved = await response;
            const entry = (await memberships()).find(item => item.subscription.subscription_id === input.subscription_ids[2]);
            trialId = entry?.course_subscription_id || null;
            trialPaymentIds = (await payments()).filter(payment => !beforeIds.has(payment.payment_id)
                && payment.associate.first_name === 'Sara' && payment.description?.includes(created.title))
                .map(payment => payment.payment_id);
            expect(saved.status()).toBe(201); expect(entry).toBeTruthy();
            expect(saved.request().postDataJSON()).toEqual([{course: courseId, subscription_id: input.subscription_ids[2],
                multiple_quote: null, events: []}]);
            expect(entry.installments.map(rate => rate.id).sort()).toEqual(expectedIds);
            expect(amountsById(entry.installments)).toEqual(expectedAmounts);
            expect(entry.installments.every(rate => !rate.paid)).toBe(true);
            expect(trialPaymentIds).toHaveLength(expectedIds.length);
            const trialPayments = (await payments()).filter(payment => trialPaymentIds.includes(payment.payment_id));
            expect(trialPayments.map(payment => Number(payment.amount)).sort((a, b) => a - b))
                .toEqual([...expectedAmounts].sort((a, b) => a - b));
            expect(trialPayments.every(payment => !payment.paid && payment.sport_association === input.association_id)).toBe(true);
            await expect(assignment).not.toBeVisible();
            await page.reload();
            expect((await memberships()).find(item => item.course_subscription_id === trialId)).toEqual(entry);
            return entry;
        };
        const openDefaultSettings = async () => {
            await open('Impostazioni', '/#/profile');
            await page.getByText('Generali', {exact: true}).click();
        };
        const defaultBlock = scope => scope.getByText('Assegna piano rate completo di default', {exact: true}).locator('xpath=ancestor::div[contains(@class,"form-group")][1]');
        const defaultSwitch = () => defaultBlock(page).locator('input[type="checkbox"]');
        const saveDefaultSettings = async () => {
            const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/profile/settings'
                && response.request().method() === 'POST');
            await page.locator('#bkn_form_password_update_submit').click();
            expect((await response).status()).toBe(200);
            await expect(page.locator('#bkn_form_password_update_submit')).toBeDisabled();
            await page.reload();
        };
        try {
            defaultOrdinary = await assignDefault('ordinary-default-with-no-explicit-rates', [1, 2], [110, 40]);
            expect(defaultOrdinary.installments.every(rate => rate.payment_date >= input.reference_date)).toBe(true);
            await page.locator('[data-row]').filter({hasText: 'SARA CONTI'}).locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
            await expect(individual).toBeVisible();
            expect(JSON.parse(await individual.locator('input[name="selectedEvents"]').inputValue())).toHaveLength(2);
            await take('ordinary-default-plan-persists-after-reload', individual.locator('.modal-content'));
            await individual.getByRole('button', {name: 'Annulla', exact: true}).click();
        await expect(individual).not.toBeVisible();
            await removeOwnedTrial(); expect(await memberships()).toEqual(finalRows);
            await openDefaultSettings();
            await expect(defaultSwitch()).not.toBeChecked();
            await defaultBlock(page).scrollIntoViewIfNeeded();
            await take('complete-plan-default-before-change', defaultBlock(page));
            await setCheckbox(defaultSwitch(), true);
            await take('complete-plan-default-before-save', defaultBlock(page));
            await saveDefaultSettings();
            await expect(defaultSwitch()).toBeChecked();
            expect((await read('profile/settings')).settings).toEqual({...settings, full_installments_plan: true});
            expect(await memberships()).toEqual(finalRows);
            expect(await coursePayments()).toEqual(finalPayments);
            await defaultBlock(page).scrollIntoViewIfNeeded();
            await take('complete-plan-default-persists-after-reload', defaultBlock(page));
            await reader.open('Impostazioni', '/#/profile');
            await reader.page.getByText('Generali', {exact: true}).click();
            await expect(defaultBlock(reader.page).locator('input[type="checkbox"]')).toBeDisabled();
            expect((await reader.api('profile/settings', {method: 'POST', data: settings})).status()).toBe(403);
            expect((await read('profile/settings')).settings.full_installments_plan).toBe(true);
            defaultComplete = await assignDefault('complete-default-with-no-explicit-rates', [0, 1, 2], [150, 110, 40]);
            expect(defaultComplete.installments.find(rate => rate.id === 0).payment_date).toBe(dates[0]);
            for (const row of finalRows)
                expect((await memberships()).find(entry => entry.course_subscription_id === row.course_subscription_id)).toEqual(row);
            await page.locator('[data-row]').filter({hasText: 'SARA CONTI'}).locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
            await expect(individual).toBeVisible();
            expect(JSON.parse(await individual.locator('input[name="selectedEvents"]').inputValue())).toHaveLength(3);
            await take('complete-default-plan-persists-after-reload', individual.locator('.modal-content'));
            await individual.getByRole('button', {name: 'Annulla', exact: true}).click();
        await expect(individual).not.toBeVisible();
            await page.locator('[data-row]').filter({hasText: 'SARA CONTI'}).getByRole('button', {name: 'Pagamenti', exact: true}).click();
            const trialPaymentModal = page.locator('#payments-modal-' + input.subscription_ids[2]);
            await expect(trialPaymentModal).toBeVisible();
            await expect(trialPaymentModal.locator('[data-row]')).toHaveCount(3);
            await take('complete-default-assignment-and-real-payments', trialPaymentModal.locator('.modal-content'));
            await trialPaymentModal.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
            await expect(trialPaymentModal).not.toBeVisible();
            await openDefaultSettings();
            await setCheckbox(defaultSwitch(), false); await saveDefaultSettings();
            await expect(defaultSwitch()).not.toBeChecked();
            expect((await read('profile/settings')).settings).toEqual(settings);
            expect((await memberships()).find(entry => entry.course_subscription_id === trialId)).toEqual(defaultComplete);
            for (const row of finalRows)
                expect((await memberships()).find(entry => entry.course_subscription_id === row.course_subscription_id)).toEqual(row);
            await defaultBlock(page).scrollIntoViewIfNeeded();
            await take('restored-default-preserves-existing-plans', defaultBlock(page));
        } finally {
            // Restore the precise original settings even when a live assertion fails.
            if ((await read('profile/settings')).settings.full_installments_plan !== settings.full_installments_plan)
                expect((await api('profile/settings', {method: 'POST', data: settings})).status()).toBe(200);
            await removeOwnedTrial();
        }
        expect((await read('profile/settings')).settings).toEqual(settings);
        expect(await memberships()).toEqual(finalRows); expect(await coursePayments()).toEqual(finalPayments);
        report[spec.outcome.field] = {
            created_events: (await course()).events.length, uniform_plan_saved: true, edited_amounts_saved: true,
            explicitly_assigned_historical_rate: giulia.installments.some(rate => rate.payment_date < input.reference_date),
            immediate_unpaid_payment_count: initialPayments.length, individual_selection_saved: true,
            existing_installment_amount_preserved: Number(afterIndividual.installments.find(rate => rate.id === 1).amount) === 100,
            ordinary_plan_count: ordinary.installments.length, complete_plan_count: complete.installments.length,
            single_fee_payment_count: finalPayments.filter(payment => payment.associate.first_name === 'Giulia').length,
            single_fee_amount: Number(singlePayment.amount), control_athlete_installments: luca.installments.length,
            control_athlete_unchanged: true, scoped_course_payments: true, baseline_course_unchanged: true, reader_writes_denied: true,
            creation_alternative_saved: true, preparatory_removal_updates_total: true, creation_alternative_removed: true,
            individual_addition_creates_payment: true, individual_removal_deletes_unpaid_payment: true,
            individual_payment_keeps_recorded_amount: true, default_ordinary_plan_count: defaultOrdinary.installments.length,
            default_complete_plan_count: defaultComplete.installments.length, default_saved_and_reopened: true,
            defaults_do_not_rewrite_existing_plans: true, reader_default_write_denied: true,
            original_default_restored: true, owned_trial_memberships_and_payments_removed: true,
        };
        expect(report[spec.outcome.field]).toEqual(spec.outcome.expected);
        report.checks = ['UI creates a uniform plan and separate single-fee alternative', 'bound overview editor saves distinct amounts',
            'explicit selection immediately creates an unpaid historical installment', 'individual edit preserves existing amounts and installment IDs',
            'ordinary/full plan and single-fee changes persist with scoped amounts', 'other athlete, original course and membership payments stay unchanged',
            'restricted reader cannot add, edit or reassign course installments',
            'creation generator submits and persists distinct amounts and corrected dates after a preparatory removal',
            'individual addition creates one payment and removal deletes only its unpaid payment',
            'empty explicit selection exercises ordinary and complete defaults for new trial assignments',
            'global preference persists, reader writes are denied, existing plans are unchanged and owned trials are cleaned up'];
    }});
