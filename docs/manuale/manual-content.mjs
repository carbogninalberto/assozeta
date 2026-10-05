// Publishing reads prose from the manual checkout. Recipes only supply evidence.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const sha = value => crypto.createHash('sha256').update(value).digest('hex');
export const normalizeDraftImages = body => body.replace(/(\/images\/[^\s"')]+)\.placeholder\.svg/g, '$1.png');

// Only these machine-added draft notices describe the capture being completed.
// Other notes may describe untested delivery, timing or access limitations.
const draftNotices = new Set([
    'Procedura basata sui comandi presenti nell’applicazione, ancora da verificare in browser e dopo la riapertura.',
    'Procedura preparata dalle sorgenti dell’applicazione. Esecuzione completa, persistenza e schermate reali devono ancora essere verificate nell’istanza dimostrativa.',
    'Bozza in attesa di prova: i passaggi e le immagini previste richiedono la verifica del flusso reale prima della pubblicazione.',
    'Bozza in attesa di prova: questa sezione deve essere confermata con una procedura reale prima della pubblicazione.',
    'Bozza in attesa di prova: questa sezione deve essere confermata dal flusso reale prima di essere considerata verificata.',
    'Bozza in attesa di prova: i passaggi e le immagini previste devono essere verificati con una cattura del flusso reale prima della pubblicazione.',
    'Bozza in attesa di prova: questi passaggi e le immagini devono essere confermati dalla cattura del flusso reale prima della pubblicazione.',
    'Procedura in stesura: salvataggio, ricaricamento e schermate reali devono ancora essere verificati nell’istanza dimostrativa.',
    'Bozza in attesa di prova: il contenuto e le immagini previste devono essere confermati dal flusso reale prima della pubblicazione.',
    'Questa procedura è descritta dai comandi presenti, ma attende la prova completa nell’applicazione. Le immagini segnaposto devono essere sostituite con schermate reali prima della pubblicazione.',
    'Procedura ancora da verificare in browser, compresi risultato e riapertura. I comandi descritti sono presenti nell’applicazione.',
    'Procedura basata sulle sorgenti dell’applicazione; l’esecuzione completa e le schermate reali devono ancora essere verificate nell’istanza dimostrativa.',
]);

export function materializeCapturedSection(body, spec = {}, screenshots = []) {
    const capture = checkpoint => {
        const matches = screenshots.filter(image => image.checkpoint === checkpoint);
        if (matches.length !== 1) throw new Error('Missing or ambiguous captured checkpoint: ' + checkpoint);
        return matches[0];
    };
    for (const mapping of spec.images || []) {
        const target = '/' + capture(mapping.checkpoint).path;
        if (body.includes(mapping.from)) body = body.split(mapping.from).join(target);
        else if (!body.includes(target)) throw new Error('Missing reviewed image slot: ' + mapping.from);
    }
    for (const insertion of spec.insert_images || []) {
        const image = capture(insertion.checkpoint);
        const target = '/' + image.path;
        let matches = 0;
        body = body.replace(/<Step\b([^>]*)>([\s\S]*?)<\/Step>/g, (whole, attributes, contents) => {
            const titleMatch = attributes.match(/\btitle=(?:"([^"]+)"|'([^']+)')/);
            const title = titleMatch?.[1] ?? titleMatch?.[2];
            if (title !== insertion.step) return whole;
            matches++;
            if (normalizeDraftImages(contents).includes(target)) return whole;
            const caption = String(image.caption || image.checkpoint).replace(/[\[\]\r\n]/g, ' ');
            return `<Step${attributes}>\n${contents.trim()}\n\n<Frame>![${caption}](${target})</Frame>\n</Step>`;
        });
        if (matches !== 1) throw new Error('Missing or ambiguous authored step: ' + insertion.step);
    }
    body = normalizeDraftImages(body).replace(/<Note>\s*([\s\S]*?)\s*<\/Note>/g,
        (whole, contents) => draftNotices.has(contents.trim()) ? '' : whole).trim();
    for (const pending of spec.remove_draft_text || []) {
        if (typeof pending !== 'string' || !pending.trim()) throw new Error('Invalid pending editorial text');
        body = body.split(pending).join('').trim();
    }
    body = body.replace(/!\[([^\]]*)\]\((\/images\/[^)]+)\)/g, (whole, alt, target) => {
        if (!/^Segnaposto\b/i.test(alt)) return whole;
        const image = screenshots.find(item => '/' + item.path === target);
        if (!image) return whole;
        const caption = image.caption || alt.replace(/^Segnaposto\s+(?:dell['’]|della\s+|del\s+|di\s+)/i, '');
        return '![' + caption.replace(/[\[\]\r\n]/g, ' ') + '](' + target + ')';
    });
    const bound = new Set(screenshots.map(image => '/' + image.path));
    for (const match of body.matchAll(/!\[[^\]]*\]\((\/images\/[^)]+)\)/g))
        if (!bound.has(match[1])) throw new Error('Uncaptured image in reviewed guide: ' + match[1]);
    return body;
}

// A complete authored procedure supersedes older, narrower capture recipes.
// Unselected complete procedures remain gaps; partial captures cannot replace them.
export function authoredPublicationOwners(workflows) {
    const owners = new Map();
    for (const [scenario, workflow] of Object.entries(workflows)) {
        for (const section of workflow.sections || []) {
            const key = section.path + '#' + section.id;
            if (owners.has(key)) throw new Error('Multiple authored workflow owners: ' + key);
            owners.set(key, {scenario, section});
        }
    }
    return owners;
}
export function mayPublishAuthoredSection(owners, scenario, key, specification) {
    const owner = owners.get(key);
    return !owner || (owner.scenario === scenario && owner.section === specification);
}

// Called only after a report's full workflow, source and capture checks passed.
// Legacy capture candidates do not establish the semantics of rewritten prose.
export function editorialWorkflowPublication(key, status, kind, binding, {editedKeys = new Set(), sections = []} = {}) {
    if (status !== 'verified' || kind !== 'workflow' ||
        !(editedKeys.has(key) || binding?.editorial_generation?.requires_implementation_review === true) ||
        sections.some(section => section.path + '#' + section.id === key))
        return {status};
    return {status: 'pending', reason: 'Automatic editorial changes require a complete reviewed procedure binding before publication'};
}

export function publicationBinding(binding, body, {captureId, scenarioId, reportSha256}) {
    if (!captureId || !scenarioId || !/^[a-f0-9]{64}$/.test(reportSha256))
        throw new Error('A publication binding requires compatible capture provenance');
    return {...binding, content_source: 'authored-mdx',
        editorial_content_sha256: binding.editorial_content_sha256 || binding.content_sha256,
        content_sha256: sha(body.trim()), materialization: {format: 1,
            capture_id: captureId, scenario_id: scenarioId, report_sha256: reportSha256}};
}

function sectionRange(mdx, id, title) {
    const headings = [...mdx.matchAll(/^#{1,6}[ \t]+(.+?)[ \t]*$/gm)];
    const matches = headings.map((heading, index) => ({heading, index})).filter(item => item.heading[1] === title);
    if (matches.length === 1) {
        const {heading, index} = matches[0];
        return [heading.index, headings[index + 1]?.index ?? mdx.length];
    }
    if (!matches.length && id === 'introduzione' && title === 'Introduzione') {
        const frontmatter = mdx.match(/^---\s*\n[\s\S]*?\n---\s*\n/);
        const start = frontmatter?.[0].length ?? 0;
        const end = headings[0]?.index ?? mdx.length;
        if (mdx.slice(start, end).trim()) return [start, end];
    }
    throw new Error('Missing or ambiguous authored section: ' + title);
}

export function replaceAuthoredSection(original, title, replacement, id) {
    const [start, end] = sectionRange(original, id, title);
    return original.slice(0, start) + replacement.trim() + '\n\n' + original.slice(end);
}

function owned(root, relative) {
    const destination = path.resolve(root, relative);
    if (path.isAbsolute(relative) || !destination.startsWith(path.resolve(root) + path.sep))
        throw new Error('Unowned manual content path: ' + relative);
    const real = fs.realpathSync(destination);
    if (!real.startsWith(fs.realpathSync(root) + path.sep)) throw new Error('Manual content escapes checkout');
    return destination;
}

export function authoredSection(manualRoot, codeRoot, relative, id, title, {captured = false} = {}) {
    const sidecarPath = path.join(manualRoot, '.manuale-evidence.json');
    if (!fs.existsSync(sidecarPath)) throw new Error('Prepare reviewed MDX with manuale-scaffold.py apply-drafts before generation');
    const sidecar = JSON.parse(fs.readFileSync(owned(manualRoot, '.manuale-evidence.json'), 'utf8'));
    if (sidecar.format !== 1 || sidecar.purpose !== 'reviewed-content' || sidecar.verified !== false)
        throw new Error('Invalid reviewed manual content bindings');
    const binding = sidecar.sections[relative + '#' + id];
    if (!binding || binding.title !== title || !binding.recipe_module || !binding.recipe_sha256)
        throw new Error('Manual section has no reviewed content binding: ' + relative + '#' + id);
    if (sha(fs.readFileSync(owned(codeRoot, binding.recipe_module))) !== binding.recipe_sha256)
        throw new Error('Review bindings after recipe changes: ' + binding.recipe_module);
    const mdx = fs.readFileSync(owned(manualRoot, relative), 'utf8');
    const [start, end] = sectionRange(mdx, id, title);
    const body = mdx.slice(start, end).trim();
    const materialized = normalizeDraftImages(body);
    if (sha(materialized) !== binding.content_sha256) throw new Error('Review changed manual prose before publication: ' + relative + '#' + id);
    return captured ? materialized : body;
}
