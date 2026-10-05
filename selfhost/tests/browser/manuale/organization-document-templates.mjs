// Actual Archivio editor and printing service; no fabricated document responses.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import {openGiulia} from './member-profile-sources.mjs';
import {organizationBasicsAuthoredWorkflows} from '../../../../docs/manuale/organization-basics-authored-workflows.mjs';
const id = 'organization-document-templates', spec = organizationBasicsAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8); expect(input.fixture_profile || 'baseline').toBe('baseline');
        const json = async route => {const response = await api(route); expect(response.status()).toBe(200); return response.json();};
        const templates = async () => json('templates/list');
        const memberId = input.subscription_ids[0], name = 'Attestazione attività dimostrativa';
        const info = async () => (await json(`subscription/${memberId}/info`)).data.info;
        const baseline = {templates: await templates(), files: (await info()).subscription_files,
            members: (await json('subscription/list?pagination[perpage]=100')).data,
            payments: (await json('payment/list?pagination[perpage]=100')).data};
        expect(baseline.templates.some(row => row.name === name)).toBe(false);
        const facts = {}, proof = (key, value) => {expect(value, key).toEqual(spec.outcome.expected[key]); facts[key] = value;};
        const take = async (checkpoint, focus, browserPage = page) => {
            await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            await capture(browserPage, spec.checkpoints.findIndex(point => point.id === checkpoint) + 1, checkpoint, focus);
        };
        const owned = {template: null, files: new Set()}, browserErrors = [];
        const viewPattern = input.origin + '/api/document/template/*/view/**';
        const authenticate = route => route.fallback({headers: {...route.request().headers(), Authorization: 'Bearer ' + input.token}});
        await page.context().route(viewPattern, authenticate);
        try {
            await open('Archivio', '/#/archive'); await page.getByRole('button', {name: 'Modulistica', exact: true}).click();
            await expect(page.getByText('Modulistica', {exact: true}).last()).toBeVisible();
            await take('templates-list-before-create');
            await page.getByRole('button', {name: 'Modello', exact: true}).click();
            const drawer = page.getByRole('dialog', {name: 'Aggiungi Modello', exact: true}); await expect(drawer).toBeVisible();
            const requests = []; page.on('request', request => {if (new URL(request.url()).pathname === '/api/templates/add' && request.method() === 'POST') requests.push(request);});
            await page.locator('#portal-save-foreground').getByRole('button', {name: 'Salva', exact: true}).click();
            await expect(page.getByText('Il nome del modello è obbligatorio', {exact: true})).toBeVisible();
            await expect(page.getByText('Il contenuto del modello è obbligatorio', {exact: true})).toBeVisible();
            proof('empty_fields_block_backend_create', requests.length === 0); await take('template-required-fields-validation', drawer);
            await drawer.getByPlaceholder('Inserisci il nome del modello...', {exact: true}).fill(name);
            await setCheckbox(drawer.locator('input[name="custom_footer_header"]'), true);
            const editors = drawer.locator('[contenteditable="true"]'); await expect(editors).toHaveCount(3);
            await editors.nth(0).fill('Associazione Sportiva Aurora — Attività');
            await editors.nth(1).fill('Copia per la persona iscritta');
            const content = editors.nth(2); await content.fill('Partecipante: '); await content.press('End'); await content.pressSequentially('@Nome');
            const suggestions = page.locator('.dropdown-menu-mention'); await expect(suggestions).toBeVisible();
            const associateGroup = suggestions.locator('details').filter({has: page.getByText('Associato', {exact: true})});
            await associateGroup.locator('summary').click(); await associateGroup.getByRole('button', {name: 'Nome', exact: true}).click();
            await expect(content.locator('.mention')).toHaveCount(1);
            await expect(content.locator('.mention')).toHaveAttribute('data-type', 'mention');
            await content.press('End'); await content.pressSequentially(' — Ginnastica dimostrativa.');
            await take('template-name-layout-and-dynamic-person-before-save', drawer);
            const created = page.waitForResponse(response => new URL(response.url()).pathname === '/api/templates/add' && response.request().method() === 'POST');
            await page.locator('#portal-save-foreground').getByRole('button', {name: 'Salva', exact: true}).click();
            const response = await created; expect(response.status()).toBe(201); const saved = await response.json(); owned.template = saved.sport_association_module_templates_id;
            expect(owned.template).toBeTruthy(); expect(saved.header).toBe(true); expect(saved.footer).toBe(true); expect(saved.custom_footer_header).toBe(true);
            expect(saved.template).toContain('associate.first_name'); expect(saved.custom_header).toContain('Aurora');
            proof('template_real_create_and_mention_persisted', true);
            await page.reload(); const row = page.locator('[data-row]').filter({hasText: /Attestazione attività dimostrativa/i});
            await expect(row).toBeVisible(); await take('template-created-after-reload', row);
            await row.locator('.navi-text').click(); const reopened = page.getByRole('dialog', {name, exact: true});
            await expect(reopened.getByPlaceholder('Inserisci il nome del modello...', {exact: true})).toHaveValue(name);
            await expect(reopened.locator('[contenteditable="true"]').last().locator('.mention')).toHaveCount(1);
            proof('template_reopened_with_layout_and_mention', (await templates()).find(item => item.sport_association_module_templates_id === owned.template).custom_footer_header === true);
            await take('template-reopened-before-generation', reopened);
            await reopened.locator('.drawer-header button.close').click();
            let member = await openGiulia({page, open, expect}); await member.locator('.nav-text').getByText('Documenti', {exact: true}).click();
            await take('member-documents-before-generation', member);
            await member.getByRole('button', {name: 'Genera da Modello', exact: true}).click();
            const modal = page.locator('#generate-from-template'); await expect(modal).toBeVisible();
            const selector = modal.locator('.svelte-select').filter({has: page.locator('input[type="hidden"][name="selectedTemplate"]')});
            await selector.locator('input:not([type="hidden"])').click(); await modal.locator('.list-item').filter({hasText: name}).click();
            await take('member-template-selected-before-generation', modal.locator('.modal-content'));
            const generated = page.waitForResponse(r => new URL(r.url()).pathname === '/api/document/template/' + owned.template && r.request().method() === 'POST');
            await modal.getByRole('button', {name: 'Genera Documento', exact: true}).click(); expect((await generated).status()).toBe(200);
            await expect(modal).toHaveCount(0); await page.reload(); member = await openGiulia({page, open, expect}); await member.locator('.nav-text').getByText('Documenti', {exact: true}).click();
            const after = await info(), additions = after.subscription_files.filter(file => !baseline.files.some(old => old.subscription_file_id === file.subscription_file_id));
            expect(additions).toHaveLength(1); const file = additions[0]; owned.files.add(file.subscription_file_id);
            expect(file.filename).toContain(name); await expect(member.getByRole('link', {name: file.filename, exact: true})).toBeVisible();
            proof('one_generated_file_attached_and_reopened', true); await take('generated-document-persists-in-member-files', member);
            const source = await api(`document/template/${owned.template}/view/?subscription_id=${memberId}`); expect(source.status()).toBe(200);
            const sourceHtml = await source.text(), sourceText = await page.evaluate(html => new DOMParser().parseFromString(html, 'text/html').body.textContent, sourceHtml);
            proof('real_renderer_resolves_person_and_custom_layout', /Partecipante:\s*Giulia/.test(sourceText) && /Ginnastica dimostrativa/.test(sourceText)
                && /Associazione Sportiva Aurora/.test(sourceText) && /Copia per la persona iscritta/.test(sourceText) && !sourceText.includes('associate.first_name'));
            const renderer = await page.context().newPage(); renderer.on('pageerror', error => browserErrors.push(error.message));
            await renderer.goto(input.origin + `/api/document/template/${owned.template}/view/?subscription_id=${memberId}`);
            await expect(renderer.getByText(/Partecipante:\s*Giulia/)).toBeVisible();
            await take('generated-document-real-renderer-with-person', undefined, renderer); await renderer.close();
            await expect.poll(async () => (await api(`document/retrieve/${file.document_id}?download=true&token=${encodeURIComponent(file.document_token)}`)).status(), {timeout: 45000}).toBe(200);
            const download = page.waitForEvent('download'); await member.getByRole('link', {name: file.filename, exact: true}).click();
            const downloaded = await download; expect(await downloaded.failure()).toBeNull(); const bytes = fs.readFileSync(await downloaded.path());
            const retrieved = await api(`document/retrieve/${file.document_id}?download=true&token=${encodeURIComponent(file.document_token)}`);
            expect(retrieved.status()).toBe(200); expect(retrieved.headers()['content-type']).toContain('application/pdf');
            proof('actual_ui_pdf_download_matches_persisted_file', bytes.equals(await retrieved.body()) && bytes.subarray(0,5).toString() === '%PDF-' && bytes.length > 1000);
            const relative = `downloads/${id}/attestazione.pdf`, destination = path.join(process.env.ASSOZETA_MANUAL_RUN, relative);
            fs.mkdirSync(path.dirname(destination), {recursive: true}); fs.writeFileSync(destination, bytes);
            report.downloads = [{path: relative, bytes: bytes.length, sha256: crypto.createHash('sha256').update(bytes).digest('hex')}];
            await take('generated-document-after-download', member);
            const reader = await actor('reader'); const readerMember = await openGiulia({...reader, expect});
            await readerMember.locator('.nav-text').getByText('Documenti', {exact: true}).click();
            await expect(readerMember.getByRole('button', {name: 'Genera da Modello', exact: true})).toHaveCount(0);
            const denials = []; for (const [route, method, data] of [['templates/add', 'POST', {name: 'Vietato', template: '<p>Vietato</p>'}],
                [`templates/${owned.template}/update`, 'PATCH', {name: 'Vietato'}], [`templates/${owned.template}/delete`, 'DELETE', {}]])
                denials.push((await reader.api(route, {method, data})).status());
            proof('reader_template_writes_denied', denials.every(status => status === 403));
            expect(await templates()).toHaveLength(baseline.templates.length + 1);
            expect((await templates()).find(item => item.sport_association_module_templates_id === owned.template)).toEqual(saved);
            await take('reader-document-generation-action-absent', readerMember, reader.page);
            expect(browserErrors).toEqual([]);
        } finally {
            // Remove only this template and files generated by this invocation.
            if (!owned.template) owned.template = (await templates()).find(item => item.name === name
                && !baseline.templates.some(old => old.sport_association_module_templates_id === item.sport_association_module_templates_id))?.sport_association_module_templates_id;
            const files = (await info()).subscription_files;
            for (const file of files) if (!baseline.files.some(old => old.subscription_file_id === file.subscription_file_id)
                && file.filename.includes(name)) owned.files.add(file.subscription_file_id);
            for (const fileId of owned.files) expect((await api(`subscription/${memberId}/delete-document/${fileId}`, {method: 'DELETE'})).status()).toBe(200);
            if (owned.template) expect((await api(`templates/${owned.template}/delete`, {method: 'DELETE'})).status()).toBe(204);
            await page.context().unroute(viewPattern, authenticate);
            expect(await templates()).toEqual(baseline.templates); expect((await info()).subscription_files).toEqual(baseline.files);
            proof('baseline_records_preserved', JSON.stringify((await json('subscription/list?pagination[perpage]=100')).data) === JSON.stringify(baseline.members)
                && JSON.stringify((await json('payment/list?pagination[perpage]=100')).data) === JSON.stringify(baseline.payments));
            proof('owned_template_and_generated_file_removed', true);
        }
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.external_gaps = [{operation: 'native-printing-and-document-delivery', status: 'needs_external_verification',
            reason: 'Local creation, real printing service, persisted PDF and UI download are covered; OS print dialog, paper printer and outbound delivery are external.'}];
        report.checks = ['real editor validation, name/layout/mention creation and reopening', 'real existing-person document generation, backend renderer dynamic value and persisted PDF',
            'actual UI PDF download bytes match storage; reader template mutations denied', 'only generated document/template removed; baseline records preserved'];
    }});
