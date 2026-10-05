// Correct a real persisted persona; never treat a typed input or toast as proof.
import {scenario, expect} from './scenario.mjs';
import {memberAuthoredWorkflows} from '../../../../docs/manuale/member-authored-workflows.mjs';
import {openGiulia} from './member-profile-sources.mjs';
const id = 'members-tax-code-correction', spec = memberAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const json = async route => {const response = await api(route); expect(response.status()).toBe(200); return response.json();};
        const subscription = async () => (await json(`subscription/${input.subscription_ids[0]}/info`)).data.info;
        const originalSubscription = await subscription(), uid = originalSubscription.associate.associate_id;
        expect(originalSubscription.associate.first_name).toBe('Giulia');
        const persona = () => json(`personas/${uid}/info`), original = await persona();
        expect(original.sport_association).toBe(input.association_id);
        const originalOthers = (await json('personas/list?pagination[perpage]=100')).data.filter(row => row.associate_id !== uid);
        const documentCode = 'BNCGLI96C50H501J', previousCode = 'BNCGLI96C50H501A';
        const patch = async data => {const response = await api(`personas/${uid}/update`, {method: 'PATCH', data});
            expect(response.status()).toBe(200); return response.json();};
        const facts = {}, proof = (key, value) => {expect(value, key).toEqual(spec.outcome.expected[key]); facts[key] = value;};
        const take = async checkpoint => {
            await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            const n = spec.checkpoints.findIndex(item => item.id === checkpoint) + 1;
            await capture(page, n, checkpoint, page.getByRole('dialog', {name: 'Giulia Bianchi', exact: true}));
        };
        const openPersona = async () => {
            await open('Organizzazione', '/#/personas/list');
            // Anagrafiche renders surname first; its drawer title uses first name first.
            const row = page.locator('[data-row]').filter({hasText: /Bianchi Giulia/i}); await expect(row).toHaveCount(1);
            await row.getByText(/^Bianchi Giulia$/i).first().click();
            const drawer = page.getByRole('dialog', {name: 'Giulia Bianchi', exact: true});
            await expect(drawer.locator('#associate_form')).toBeVisible(); return drawer;
        };
        let prepared = false;
        try {
            // Demonstration-only initial typo, scoped to the known fixture person.
            prepared = true; await patch({tax_code: previousCode});
            report.fixture_preparation = {existing_persona: true, previous_code: 'fictional transcription error',
                document_code: 'fictional example, no external tax-registry lookup'};
            let drawer = await openPersona();
            await expect(drawer.locator('[name="taxCode"]')).toHaveValue(previousCode);
            await take('tax-code-persona-opened');
            await drawer.locator('[name="taxCode"]').fill(documentCode);
            await drawer.locator('[name="taxCode"]').press('Tab');
            proof('typed_value_not_saved_automatically', (await persona()).tax_code === previousCode);
            await take('tax-code-correction-not-saved');
            const saving = page.waitForResponse(response => new URL(response.url()).pathname === `/api/personas/${uid}/update`
                && response.request().method() === 'PATCH');
            await page.locator('#portal-save-foreground .fixed-bottom-bar-actions').getByRole('button', {name: 'Salva', exact: true}).click();
            const saved = await saving; expect(saved.status()).toBe(200);
            proof('real_patch_submitted', saved.request().postDataJSON().tax_code === documentCode);
            await expect(page.getByText('Dati aggiornati con successo', {exact: true})).toBeVisible();
            proof('corrected_code_persisted', (await persona()).tax_code === documentCode);
            await page.keyboard.press('Escape'); await expect(drawer).toHaveCount(0);
            await page.reload(); drawer = await openPersona();
            await expect(drawer.locator('[name="taxCode"]')).toHaveValue(documentCode);
            proof('correction_survives_reopen', (await persona()).tax_code === documentCode);
            await take('tax-code-correction-persisted');
            await page.keyboard.press('Escape'); await expect(drawer).toHaveCount(0);
            drawer = await openGiulia({page, open, expect});
            await expect(drawer).toContainText(documentCode);
            const after = await subscription();
            proof('subscription_uses_same_persona', after.associate.associate_id === uid);
            proof('subscription_code_updated', after.associate.tax_code === documentCode);
            await take('tax-code-subscription-reflects-correction');
            const reader = await actor('reader');
            const deniedRead = await reader.api(`personas/${uid}/info`);
            const deniedWrite = await reader.api(`personas/${uid}/update`, {method: 'PATCH', data: {tax_code: previousCode}});
            proof('reader_persona_read_denied', deniedRead.status() === 403);
            proof('reader_persona_update_denied', deniedWrite.status() === 403);
            proof('denials_preserve_saved_code', (await persona()).tax_code === documentCode);
            const stripPersona = ({associate, ...rest}) => rest;
            proof('subscription_fields_preserved', JSON.stringify(stripPersona(after)) === JSON.stringify(stripPersona(originalSubscription)));
            expect((await json('personas/list?pagination[perpage]=100')).data.filter(row => row.associate_id !== uid)).toEqual(originalOthers);
        } finally {
            if (prepared) {
                // Restore every editable field that the full form can normalize.
                const immutable = new Set(['associate_id', 'creation_date', 'full_name', 'is_minor_now', 'is_tutor',
                    'age', 'family_members', 'tutors', 'incomplete']);
                const data = Object.fromEntries(Object.entries(original).filter(([key]) => !immutable.has(key)));
                data.born_date = original.born_date ? original.born_date.split('-').reverse().join('/') : null;
                data.tutors_data = original.tutors;
                await patch(data);
                expect(await persona()).toEqual(original);
                proof('original_persona_restored', true);
            }
        }
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.checks = ['actual typed code included in real PATCH', 'server code survives reopening and reload',
            'same persona reflected in subscription readback', 'distinct persona permissions deny reader',
            'owned persona restored; other records unchanged'];
    }});
