// Reviewed MDX supplies prose; chapter registries supply implementation evidence.
import {communicationAuthReferences} from './communication-auth-reference-recipes.mjs';
import {memberAdditionalReferences} from './member-additional-reference-recipes.mjs';
import {coursePaymentAdditionalReferences} from './course-payment-additional-reference-recipes.mjs';
import {organizationAdditionalReferences} from './organization-additional-reference-recipes.mjs';
import {settingsAdditionalReferences} from './settings-additional-reference-recipes.mjs';
import {editorialContracts} from './editorial-contracts.mjs';
import {authoredWorkflows} from './authored-workflows.mjs';

const chapters = [communicationAuthReferences, memberAdditionalReferences,
    coursePaymentAdditionalReferences, organizationAdditionalReferences, settingsAdditionalReferences];

// These bodies contain source-reviewed descriptions and links only. They do
// not claim that a save, recalculation, print or navigation was exercised.
const legacyTextReferences = new Set([
    'docs/carnet.mdx#riepilogo-delle-regole-di-funzionamento',
    'tutorials/come-gestire-registro-presenze-carnet.mdx#conclusione',
    'docs/contabilita-avanzata.mdx#fatture-attive',
    'docs/bacheca.mdx#consigli-utili',
    'docs/calendario.mdx#risorse-correlate',
    'docs/carnet.mdx#risorse-correlate',
    'docs/registro-presenze.mdx#risorse-correlate',
    'docs/bilancio.mdx#risorse-correlate',
    'docs/ricevute.mdx#configurazioni-possibili',
    'docs/contabilita-avanzata.mdx#panoramica',
]);

export function authoredReferenceSections(source) {
    const seen = new Set();
    const sections = chapters.flatMap(chapter => chapter(source)).map(section => {
        const key = section.path + '#' + section.id;
        if (seen.has(key)) throw new Error('Duplicate authored reference section: ' + key);
        seen.add(key);
        return {...section, authored: true};
    });
    // Legacy chapter identities use the same reviewed source contracts. Only
    // a complete, explicit workflow binding makes one an execution candidate.
    const legacy = new Map(editorialContracts().map(section => [section.path + '#' + section.id, section]));
    for (const key of legacyTextReferences) {
        const descriptor = legacy.get(key);
        if (!descriptor || seen.has(key) || descriptor.status !== 'pending' || !descriptor.source_contracts.length)
            throw new Error('Missing reviewed legacy text reference: ' + key);
        const evidence = descriptor.source_contracts.map(contract => {
            const result = source(contract.path, contract.symbol, contract.length);
            if (result.canonical_source_sha256 !== contract.reviewed_sha256)
                throw new Error('Text reference source changed; review required: ' + contract.path);
            return result;
        });
        sections.push({path: descriptor.path, id: descriptor.id, title: descriptor.title,
            status: 'verified', kind: 'code-reference', authored: true, evidence, screenshots: [],
            reason: 'Descrizione e collegamenti revisionati nel codice; nessuna nuova procedura o cattura attestata.'});
        seen.add(key);
    }
    for (const spec of Object.values(authoredWorkflows)) for (const binding of spec.sections) {
        const key = binding.path + '#' + binding.id;
        if (seen.has(key)) continue;
        const descriptor = legacy.get(key);
        // Overview owns these identities in the shared catalogue, including
        // initial setup now bound to the registration workflow.
        if (descriptor?.recipe_module === 'docs/manuale/overview-recipes.mjs') continue;
        if (!descriptor || descriptor.status !== 'pending' || descriptor.title !== binding.title || !descriptor.source_contracts.length)
            throw new Error('Missing reviewed pending legacy procedure: ' + key);
        const evidence = descriptor.source_contracts.map(contract => {
            const result = source(contract.path, contract.symbol, contract.length);
            if (result.canonical_source_sha256 !== contract.reviewed_sha256)
                throw new Error('Legacy procedure source changed; review required: ' + contract.path);
            return result;
        });
        sections.push({path: binding.path, id: binding.id, title: binding.title,
            status: 'pending', reason: descriptor.reason, kind: 'workflow', intent: binding.intent || '',
            evidence, screenshots: [], authored: true});
        seen.add(key);
    }
    return sections;
}
