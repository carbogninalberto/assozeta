// Update only recipes with passed real captures; enumerate every other page as a gap.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {additionalPages} from '../manuale/content-recipes.mjs';
import {referenceCatalogue} from '../manuale/reference-catalogue.mjs';
import {paymentPages, paymentCaptureSpecs} from '../manuale/payment-recipes.mjs';
import {memberPages, memberCaptureSpecs} from '../manuale/member-recipes.mjs';
import {profilePages, profileCaptureSpecs} from '../manuale/profile-recipes.mjs';
import {instructorPages, instructorCaptureSpecs} from '../manuale/instructor-recipes.mjs';
import {dashboardPages, dashboardCaptureSpecs} from '../manuale/dashboard-recipes.mjs';
import {attendanceCarnetPages, attendanceCarnetCaptureSpecs} from '../manuale/attendance-carnet-recipes.mjs';
import {organizationAccessPages, organizationAccessCaptureSpecs} from '../manuale/organization-access-recipes.mjs';
import {FULL_HD, pngDimensions, viewportCrop} from '../../selfhost/tests/browser/manuale/frame.mjs';

import {authoredSection, replaceAuthoredSection as replaceSection, materializeCapturedSection, publicationBinding, editorialWorkflowPublication, authoredPublicationOwners, mayPublishAuthoredSection} from '../manuale/manual-content.mjs';
import {searchPages} from '../manuale/search-recipes.mjs';
import {tagPages, tagCaptureSpecs} from '../manuale/tag-recipes.mjs';
import {accountingBalancePages, accountingBalanceCaptureSpecs} from '../manuale/accounting-balance-recipes.mjs';
import {campsCalendarPages, campsCalendarCaptureSpecs} from '../manuale/camps-calendar-recipes.mjs';
import {registrationFormsPages, registrationFormsCaptureSpecs} from '../manuale/registration-forms-recipes.mjs';
import {authoredWorkflows, validateAuthoredWorkflow} from '../manuale/authored-workflows.mjs';
import {displayedImages, capturedImageCaptions} from '../manuale/rendered-content.mjs';

const run = path.resolve(process.argv[2] || '');
const state = JSON.parse(fs.readFileSync(path.join(run, 'run.json')));
const selected = state.selected_recipes || ['tags-create-assign'];
const sameIds = (left, right) => Array.isArray(left) && Array.isArray(right)
    && new Set(left).size === left.length && new Set(right).size === right.length
    && JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const fileSha = file => sha(fs.readFileSync(file));
const manual = state.manual;
const code = state.application;
const bindingPath = path.join(manual, '.manuale-evidence.json');
const authoring = JSON.parse(fs.readFileSync(bindingPath, 'utf8'));
const authoringInputSha = fileSha(bindingPath);
const references = referenceCatalogue(referenceSource);
// A quoted model draft cannot become a text-only verified reference. A passed
// complete workflow may still promote its own bound procedure below.
const editorialText = new Set(state.editorial_automation?.unexecuted_text_sections || []);
for (const [key, binding] of Object.entries(authoring.sections))
    if (binding.editorial_generation?.requires_implementation_review === true) editorialText.add(key);
for (const section of references)
    if (editorialText.has(section.path + '#' + section.id) && section.status === 'verified') {
        section.status = 'pending';
        section.reason = 'Automatic editorial draft requires independent implementation evidence before text-only publication';
    }
const missingAuthored = references.filter(section => section.authored && !authoring.sections[section.path + '#' + section.id])
    .map(({path, id, title}) => ({path, id, title}));
const missingKeys = new Set(missingAuthored.map(section => section.path + '#' + section.id));
const selectedMissing = selected.flatMap(id => (authoredWorkflows[id]?.sections || [])
    .filter(section => missingKeys.has(section.path + '#' + section.id))
    .map(section => ({scenario_id: id, path: section.path, id: section.id, title: section.title})));
const preflight = {format: 1, status: missingAuthored.length ? 'incomplete' : 'passed',
    application_revision: state.application_input.revision, manual_revision: state.manual_input.revision,
    authored_input_sha256: authoringInputSha, missing_sections: missingAuthored, selected_missing_sections: selectedMissing,
    reason: missingAuthored.length ? 'The selected manual input lacks reviewed authored MDX bindings; original prose remains an explicit gap.' : ''};
fs.writeFileSync(path.join(run, 'authoring-preflight.json'), JSON.stringify(preflight, null, 2) + '\n');
if (selectedMissing.length) {
    throw new Error('Selected workflows require reviewed authored MDX before capture: '
        + selectedMissing.map(section => section.path + '#' + section.id).join(', ')
        + '. Inspect authoring-preflight.json and select the authored manual revision.');
}
if (process.argv.includes('--authoring-preflight')) {
    console.log(`Authored MDX preflight: ${preflight.status}; ${missingAuthored.length} explicit unbound sections.`);
    process.exit(0);
}

let reusePlan = null;
let evidenceReuse;
const reusePath = path.join(run, 'evidence-reuse.json');
if (state.evidence_reuse) {
    const binding = state.evidence_reuse;
    const verifier = 'BE/application/manuale/reuse.py';
    if (binding.path !== 'evidence-reuse.json' || !fs.existsSync(reusePath) || fileSha(reusePath) !== binding.sha256
        || state.tooling_hashes?.[verifier] !== fileSha(path.join(code, verifier)))
        throw new Error('Changed or unsealed retained evidence plan/verifier');
    reusePlan = JSON.parse(execFileSync(process.env.ASSOZETA_MANUAL_PYTHON || 'python3',
        [path.join(code, verifier), '--validate', run], {encoding: 'utf8', maxBuffer: 64 * 1024 * 1024}));
    if (reusePlan.format !== 1 || reusePlan.status !== 'validated' || !Array.isArray(reusePlan.reused_recipes)
        || !sameIds(reusePlan.fresh_recipes, selected)
        || new Set(reusePlan.reused_recipes).size !== reusePlan.reused_recipes.length
        || reusePlan.reused_recipes.some(id => selected.includes(id))
        || !sameIds(binding.reused_recipes, reusePlan.reused_recipes))
        throw new Error('Retained evidence plan does not match the explicit target recipe selection');
    evidenceReuse = {path: 'evidence-reuse.json', sha256: binding.sha256, reused_recipes: reusePlan.reused_recipes};
} else if (fs.existsSync(reusePath)) throw new Error('Unbound retained evidence plan');
const reused = new Set(reusePlan?.reused_recipes || []);
const reports = [...selected, ...reused].map(id => {
    if (!/^[a-z][a-z0-9-]*$/.test(id)) throw new Error('Invalid scenario identity');
    const file = path.join(run, id + '.json');
    const result = JSON.parse(fs.readFileSync(file));
    if (result.id !== id || result.status !== 'passed' || result.backend !== 'real')
        throw new Error('Invalid real capture: ' + id);
    if (reused.has(id)) {
        const entry = reusePlan.recipes[id];
        if (!entry || entry.report_path !== id + '.json' || entry.report_sha256 !== fileSha(file)
            || result.capture_id !== entry.origin.capture_id || result.application_revision !== entry.origin.application_revision)
            throw new Error('Retained capture differs from its individually validated origin: ' + id);
    } else if (!state.capture_id || result.capture_id !== state.capture_id
        || result.application_revision !== state.application_input.revision
        || JSON.stringify(result.tooling_hashes || {}) !== JSON.stringify(state.tooling_hashes || {})
        || result.reference_date !== state.reference_date)
        throw new Error('Invalid compatible fresh capture: ' + id);
    return result;
});
const retainedOnly = !selected.length && reused.size > 0;
if ((!['captured', 'generated', 'rendered', 'indexed', 'evaluated'].includes(state.status)
    && !(retainedOnly && ['prepared', 'running'].includes(state.status))) || !reports.length)
    throw new Error('Generation requires passed fresh captures or explicitly compatible retained real evidence');
function source(report, relative, symbol, length) {
    const file = path.join(code, relative);
    const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
    const found = lines.findIndex(line => line.includes(symbol));
    if (found < 0 || report.source_hashes[relative] !== fileSha(file)) throw new Error('Changed or missing source: ' + relative);
    return {path: relative, symbol, start: found + 1, end: Math.min(lines.length - 1, found + length), sha256: fileSha(file), canonical_source_sha256: sha(lines.join('\n'))};
}
const publicationOwners = authoredPublicationOwners(authoredWorkflows);
const canonicalReferenceKeys = new Set(references.filter(section => section.status === 'verified' && section.kind === 'code-reference')
    .map(section => section.path + '#' + section.id));
const reviewed = new Map();
const staged = [];
for (const result of reports) {
    const images = result.screenshots;
    const captures = {

        'members-search': ['images/faq/ricerca-filtri/', 4],
        'courses-create-edit': ['images/corsi/creazione-modifica/', 4],
        'members-archive-restore': ['images/faq/archiviazione/', 5],
        ...Object.fromEntries(Object.entries({...tagCaptureSpecs, ...accountingBalanceCaptureSpecs, ...campsCalendarCaptureSpecs, ...registrationFormsCaptureSpecs, ...dashboardCaptureSpecs, ...attendanceCarnetCaptureSpecs, ...organizationAccessCaptureSpecs}).map(([id, [prefix, checkpoints]]) => [id, [prefix, checkpoints.length]])),
        ...Object.fromEntries(Object.entries(instructorCaptureSpecs).map(([id, [prefix, checkpoints]]) => [id, [prefix, checkpoints.length]])),
        ...Object.fromEntries(Object.entries(profileCaptureSpecs).map(([id, [prefix, checkpoints]]) => [id, [prefix, checkpoints.length]])),
        ...Object.fromEntries(Object.entries(memberCaptureSpecs).map(([id, [prefix, checkpoints]]) => [id, [prefix, checkpoints.length]])),
        ...Object.fromEntries(Object.entries(paymentCaptureSpecs).map(([id, [prefix, checkpoints]]) => [id, [prefix, checkpoints.length]])),
        ...Object.fromEntries(Object.entries(authoredWorkflows).map(([id, spec]) => [id, [spec.prefix, spec.checkpoints.length]])),
    };
    const [prefix, count] = captures[result.id] || [];
    if (!prefix || images.length !== count) throw new Error('Incomplete or unknown workflow capture: ' + result.id);
    if (authoredWorkflows[result.id]) {
        validateAuthoredWorkflow(result, authoredWorkflows[result.id]);
        for (const relative of authoredWorkflows[result.id].sources)
            if (result.source_hashes[relative] !== fileSha(path.join(code, relative)))
                throw new Error('Changed authored workflow source: ' + relative);
    }
    if (result.capture_format !== 'full-hd-v1' || result.viewport.width !== FULL_HD.width
        || result.viewport.height !== FULL_HD.height || result.device_scale_factor !== 1) throw new Error('Workflow capture requires Full HD');
    for (const capture of images) {
        if (!capture.path.startsWith(prefix) || fileSha(path.join(run, 'captures', capture.path)) !== capture.sha256) throw new Error('Invalid staged capture');
        const master = capture.master;
        if (!master || master.path !== 'masters/' + capture.path || fileSha(path.join(run, master.path)) !== master.sha256) throw new Error('Missing or changed Full HD master');
        const size = pngDimensions(fs.readFileSync(path.join(run, master.path)));
        if (size.width !== FULL_HD.width || size.height !== FULL_HD.height
            || master.width !== size.width || master.height !== size.height) throw new Error('Master dimensions are not Full HD');
        const cropSize = pngDimensions(fs.readFileSync(path.join(run, 'captures', capture.path)));
        if (cropSize.width !== capture.width || cropSize.height !== capture.height) throw new Error('Capture dimensions differ');
        if (capture.clip) {
            const bounded = viewportCrop(capture.clip);
            if (JSON.stringify(bounded) !== JSON.stringify(capture.clip)
                || bounded.width !== cropSize.width || bounded.height !== cropSize.height) throw new Error('Crop extends beyond the Full HD master');
        } else if (capture.sha256 !== master.sha256) throw new Error('Uncropped capture differs from master');
        staged.push(capture);
    }
    const add = (relative, id, body, evidence, screenshots, mode = 'replace-section', title = '', status = 'verified', reason = '', kind = 'workflow', explicitIntent = '', imageSpec = {}) => {
        if (!mayPublishAuthoredSection(publicationOwners, result.id, relative + '#' + id, imageSpec)) return;
        if (canonicalReferenceKeys.has(relative + '#' + id)) return;
        if (!['verified', 'pending', 'unsupported', 'needs_external_verification'].includes(status)) throw new Error('Invalid reviewed section status');
        const previous = reviewed.get(relative);
        const original = previous?.text || fs.readFileSync(path.join(manual, relative), 'utf8');
        body = authoredSection(manual, code, relative, id, title);
        const editorialStatus = editorialWorkflowPublication(relative + '#' + id, status, kind,
            authoring.sections[relative + '#' + id], {editedKeys: editorialText,
                sections: authoredWorkflows[result.id]?.sections || []});
        if (editorialStatus.status !== status) {
            status = editorialStatus.status;
            reason = editorialStatus.reason;
        }
        if (status === 'verified' && kind === 'workflow') {
            body = capturedImageCaptions(materializeCapturedSection(body, imageSpec, screenshots), screenshots);
            const key = relative + '#' + id;
            authoring.sections[key] = publicationBinding(authoring.sections[key], body, {
                captureId: result.capture_id, scenarioId: result.id,
                reportSha256: fileSha(path.join(run, result.id + '.json'))});
        }
        if (status === 'verified') {
            const bound = new Set(screenshots.map(image => image.path));
            for (const image of displayedImages(body))
                if (!bound.has(image)) throw new Error('Uncaptured image in reviewed guide: ' + relative + ':' + image);
        }
        const text = replaceSection(original, title, body, id);
        const intents = {
            'stagione-sportiva': 'organization.season.update',
            'come-cambiare-la-stagione-sportiva': 'organization.season.update',
            'pubblicazione-del-bilancio': 'accounting.balance.publish',
            'pubblicare-il-bilancio': 'accounting.balance.publish',
            'creare-un-corso': 'courses.create', 'modificare-le-informazioni-del-corso': 'courses.update',
            'archiviazione-manuale': 'members.archive', 'ripristinare-i-dati-archiviati': 'members.restore',
            'ripristinare-un-elemento-dall-archivio': 'members.restore',
            'creare-un-socio-socio-tesserato-o-tesserato': 'members.create',
            'ciclo-di-vita-dell-iscrizione': 'members.approve',
            'anagrafica-smart': 'members.update', 'dal-profilo-dell-atleta': 'members.medical.upload',
            'impostare-la-data-di-scadenza': 'members.medical.expiration',
            'tieni-traccia-dei-certificati-agonistici': 'members.medical.update',
            'consigli-per-una-gestione-efficiente': 'members.medical.remove',
            'aggiungi-un-socio': 'members.create', '1-informazioni-profilo': 'members.create.profile',
            '2-informazioni-anagrafiche': 'members.create.personal', '3-firma-del-documento': 'members.create.signature',
            '4-certificato-medico': 'members.create.medical', '5-riepilogo-e-creazione': 'members.create.save',
            'come-funziona': 'members.tax_code',
            'la-barra-di-ricerca': 'members.search', 'assegna-tag': 'members.tags.assign',
            'creare-un-pagamento': 'payments.create', 'modificare-un-pagamento': 'payments.update',
            'segna-un-pagamento-come-pagato': 'payments.approve',
            'come-vengono-emesse-le-ricevute': 'receipts.create', 'numerazione-progressiva': 'receipts.numbering',
            'come-posso-scaricare-una-ricevuta': 'receipts.read',
            'modifica-del-progressivo-della-ricevuta': 'receipts.update', 'eliminare-una-ricevuta': 'receipts.delete',
        };
        const instructorIntents = Object.fromEntries([
            ['instructors.create', ['aggiungere-un-istruttore', 'aggiungere-un-nuovo-istruttore',
                'informazioni-obbligatorie', 'informazioni-anagrafiche', 'informazioni-contratto',
                'tariffe-predefinite', 'account-collaboratore-associato']],
            ['instructors.update', ['modificare-un-istruttore', 'modificare-i-dati']],
            ['instructors.hours.create', ['registrare-le-ore-lavorate', 'campi-principali',
                'tipi-di-compenso', 'tipologie-di-compenso', 'compenso-orario', 'note-e-salvataggio']],
            ['instructors.hours.read', ['la-scheda-dell-istruttore', 'stato-dei-compensi', 'riepilogo-ore']],
        ].flatMap(([intent, ids]) => ids.map(id => [id, intent])));
        const intent = explicitIntent || (result.id === 'instructors-create-edit-hours' ? instructorIntents[id] || '' : intents[id] || '');
        const section = {id, title, status, reason, kind, audience: 'public', intent,
            displayed_screenshots: displayedImages(body),
            content_sha256: sha(body.trim()), evidence, scenario_ids: kind === 'code-reference' ? [] : [result.id], screenshots};
        reviewed.set(relative, {text, fullReplacement: mode === 'replace-page' || previous?.fullReplacement,
            sections: [...(previous?.sections || []).filter(existing => existing.id !== id), section]});
    };
    for (const pages of [tagPages, searchPages, accountingBalancePages, campsCalendarPages, registrationFormsPages])
        for (const page of pages(result.id, result, source))
            add(page.path, page.id, page.body, page.evidence, page.screenshots, 'replace-section', page.title,
                page.status || 'verified', page.reason || '', 'workflow', page.intent || '');
    for (const page of additionalPages(result.id, result, source))
        add(page.path, page.id, page.body, page.evidence, page.screenshots, 'replace-section', page.title);
    for (const page of memberPages(result.id, result, source))
        add(page.path, page.id, page.body, page.evidence, page.screenshots, 'replace-section', page.title);
    for (const page of dashboardPages(result.id, result, source))
        add(page.path, page.id, page.body, page.evidence, page.screenshots, 'replace-section', page.title,
            page.status || 'verified', page.reason || '', page.kind === 'reviewed-reference' ? 'code-reference' : 'workflow',
            page.kind === 'reviewed-reference' ? 'dashboard.read.' + page.id : 'dashboard.personalize');
    for (const page of attendanceCarnetPages(result.id, result, source))
        add(page.path, page.id, page.body, page.evidence, page.screenshots, 'replace-section', page.title,
            page.status || 'verified', page.reason || '', 'workflow', page.intent || '');
    for (const page of organizationAccessPages(result.id, result, source))
        add(page.path, page.id, page.body, page.evidence, page.screenshots, 'replace-section', page.title,
            page.status || 'verified', page.reason || '', 'workflow', page.intent || '');
    for (const page of instructorPages(result.id, result, source))
        add(page.path, page.id, page.body, page.evidence, page.screenshots, 'replace-section', page.title);
    for (const page of profilePages(result.id, result, source))
        add(page.path, page.id, page.body, page.evidence, page.screenshots, 'replace-section', page.title);
    for (const page of paymentPages(result.id, result, source))
        add(page.path, page.id, page.body, page.evidence, page.screenshots, 'replace-section', page.title,
            page.status || 'verified', page.reason || '');
    for (const section of authoredWorkflows[result.id]?.sections || []) {
        const descriptor = references.find(item => item.path === section.path && item.id === section.id);
        if (!descriptor?.authored || descriptor.status !== 'pending' || descriptor.title !== section.title)
            throw new Error('Authored workflow must bind an exact reviewed pending procedure: ' + section.path + '#' + section.id);
        for (const ref of descriptor.evidence)
            if (result.source_hashes[ref.path] !== ref.sha256)
                throw new Error('Authored procedure source was not captured: ' + ref.path);
        const screenshots = section.checkpoints.map(checkpoint => {
            const matches = result.screenshots.filter(image => image.checkpoint === checkpoint);
            if (matches.length !== 1) throw new Error('Missing authored section checkpoint: ' + checkpoint);
            return {...matches[0], caption: authoredWorkflows[result.id].checkpoints.find(point => point.id === checkpoint).caption};
        });
        add(section.path, section.id, '', descriptor.evidence, screenshots, 'replace-section', section.title,
            'verified', '', 'workflow', descriptor.intent || '', section);
    }
}
function referenceSource(relative, symbol, length) {
    const lines = fs.readFileSync(path.join(code, relative), 'utf8').split(/\r?\n/);
    const found = lines.findIndex(line => line.includes(symbol));
    if (found < 0) throw new Error('Missing reviewed reference source: ' + relative + ':' + symbol);
    return {path: relative, symbol, start: found + 1, end: Math.min(lines.length - 1, found + length),
        sha256: fileSha(path.join(code, relative)), canonical_source_sha256: sha(lines.join('\n'))};
}
const authoredBindings = authoring.sections;
for (const section of references) {
    const previous = reviewed.get(section.path);
    if (previous?.sections.some(existing => existing.id === section.id && existing.kind === 'workflow')) continue;
    const original = previous?.text || fs.readFileSync(path.join(manual, section.path), 'utf8');
    if (section.authored && !authoredBindings[section.path + '#' + section.id]) {
        // A clean legacy source has not acquired the separately authored prose.
        // Curated implementation alone cannot verify that original prose.
        reviewed.set(section.path, {text: original, fullReplacement: false,
            sections: [...(previous?.sections || []), {id: section.id, status: 'pending',
                reason: 'This section needs the reviewed authored MDX revision before publication',
                kind: 'code-reference', audience: 'public', evidence: [], scenario_ids: [], screenshots: []}]});
        continue;
    }
    const body = authoredSection(manual, code, section.path, section.id, section.title);
    if (section.status === 'verified' && /<Step\b|<Frame\b|<img\b|!\[[^\]]*\]\(/.test(body))
        throw new Error('Text-only reference must not promote unexecuted screenshot content: ' + section.path + '#' + section.id);
    reviewed.set(section.path, {text: replaceSection(original, section.title, body, section.id), fullReplacement: false,
        sections: [...(previous?.sections || []), {...section, body: undefined, kind: 'code-reference', audience: 'public',
            implicit_heading: !/^#{1,6}\s/.test(body.trim()),
            content_sha256: sha(body.trim()), scenario_ids: [], screenshots: []}]});
}
// Validate every recipe before replacing any prior MDX or screenshot.
for (const capture of staged) {
    const target = path.join(manual, capture.path);
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.copyFileSync(path.join(run, 'captures', capture.path), target);
}
for (const [relative, page] of reviewed) fs.writeFileSync(path.join(manual, relative), page.text);
fs.writeFileSync(bindingPath, JSON.stringify(authoring, null, 2) + '\n');
const config = JSON.parse(fs.readFileSync(path.join(manual, 'mint.json')));
const navigation = [...new Set(config.navigation.flatMap(group => group.pages))].sort();
const pages = navigation.map(id => {
    const relative = id + '.mdx';
    if (!fs.existsSync(path.join(manual, relative))) throw new Error('Missing navigation page: ' + relative);
    return {path: relative, sections: reviewed.get(relative)?.sections || [],
        status: reviewed.get(relative)?.fullReplacement && reviewed.get(relative).sections.every(section => section.status === 'verified')
            ? 'verified' : reviewed.has(relative) ? 'partial' : 'pending',
        reason: 'Other existing sections require implementation and workflow verification'};
});
let category = '';
const backlog = [];
for (const line of fs.readFileSync(path.join(manual, 'SCREENSHOTS-NEEDED.md'), 'utf8').split('\n')) {
    if (line.startsWith('## Documentation Pages')) category = 'docs';
    if (line.startsWith('## FAQ Pages')) category = 'faq';
    if (line.startsWith('## Tutorial Pages')) category = 'tutorials';
    const match = line.match(/^\| `([^`]+\.mdx)` \| ([^|]+) \| ([^|]+) \| (.*) \|$/);
    if (!match) continue;
    const relative = category + '/' + match[1];
    backlog.push({page: relative, original_status: match[2].trim(), requested: match[3].trim(), description: match[4].trim(),
        status: reviewed.has(relative) ? 'partial; only recorded workflow checkpoints verified' : 'pending'});
    if (!pages.some(page => page.path === relative)) throw new Error('Screenshot backlog page is absent from navigation: ' + relative);
}
const manifest = {format: 1, metadata: {application_revision: state.application_input.revision,
    reference_date: state.reference_date, evidence_reuse: evidenceReuse,
    manual_revision: state.manual_input.revision, release: state.release, manual_url: state.manual_preview_url || state.manual_url,
    canonical_manual_url: state.manual_url, publication_status: 'local-preview-only',
    code_state: state.application_input.state, uncommitted_code: state.application_input.uncommitted_files,
    manual_state: 'working_tree', manual_input_state: state.manual_input.state,
    uncommitted_manual: state.manual_input.uncommitted_files || {},
    manual_authoring: state.manual_authoring ? {mode: state.manual_authoring.mode,
        input_revision: state.manual_authoring.input_revision, evidence_sha256: state.manual_authoring.evidence_sha256,
        scaffold_sha256: state.manual_authoring.scaffold_sha256} : undefined,
    authored_input_sha256: authoringInputSha, authored_content_sha256: fileSha(bindingPath),
    authoring_preflight: {path: 'authoring-preflight.json', sha256: fileSha(path.join(run, 'authoring-preflight.json')),
        status: preflight.status, missing_sections: missingAuthored.length},
    tooling_hashes: state.tooling_hashes || {}, recipe_version: 'authored-mdx-v1-workflows-v8'},
    pages, screenshot_backlog: backlog, scenarios: reports.map(result => ({id: result.id, status: result.status,
        application_revision: result.application_revision, capture_id: result.capture_id,
        reference_date: result.reference_date, source_hashes: result.source_hashes,
        ...(reused.has(result.id) ? {reuse: {path: evidenceReuse.path, sha256: evidenceReuse.sha256, recipe_id: result.id}} : {}),
        report_path: result.id + '.json', report_sha256: fileSha(path.join(run, result.id + '.json'))}))};
fs.writeFileSync(path.join(run, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
const coverage = {navigation_pages: pages.length, verified_workflows: reports.length,
    fresh_workflows: selected.length, retained_workflows: reused.size,
    evidence_reuse: evidenceReuse, reuse_rejections: reusePlan?.rejected || [],
    unbound_authored_sections: missingAuthored, authoring_preflight_status: preflight.status,
    reviewed_pages: reviewed.size,
    pages_with_verified_sections: pages.filter(page => page.sections.some(section => section.status === 'verified')).length,
    verified_pages: pages.filter(page => page.status === 'verified').length,
    pending_pages: pages.filter(page => page.status !== 'verified').length,
    screenshot_backlog_entries: backlog.length, screenshots_captured: staged.length,
    manual_branch: state.manual_branch, publication_status: 'local-preview-only',
    selection: state.selection || {mode: 'full'}, pending: pages.filter(page => page.status !== 'verified').map(page => page.path)};
fs.writeFileSync(path.join(run, 'coverage.json'), JSON.stringify(coverage, null, 2) + '\n');
console.log(`Verified ${reports.length} workflows across ${reviewed.size} pages; inventoried ${pages.length} navigation pages and ${backlog.length} screenshot entries. Remaining sections are explicit gaps.`);
