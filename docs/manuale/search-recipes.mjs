const searchSourceContracts = [
    ['UI/src/components/Sidebar.svelte', '<span class="menu-text">Organizzazione</span>', 50],
    ['UI/src/routes.js', "'/members/list':", 18],
    ['UI/src/utils/Permissions.js', 'export const canPerformAction', 30],
    ['UI/src/routes/association/Members/MembersList.svelte', 'function scheduleSearch()', 12],
    ['UI/src/routes/association/Members/MembersList.svelte', 'bind:value={$subscriptionListFilter.generalSearch}', 32],
    ['UI/src/store/stores.js', 'export const subscriptionListFilterDatatable', 22],
    ['BE/application/views/subscriptions_views.py', 'def subscription_list(request):', 65],
    ['BE/application/views/subscriptions_views.py', 'def _apply_search_filter(', 46],
    ['BE/application/utils/subscriptions_utils.py', 'def smart_search(', 24],
    ['BE/application/utils/subscriptions_utils.py', 'def sqlite_search(', 82],
    ['BE/application/models/subscriptions_models.py', 'class Subscription(', 65],
    ['BE/application/permissions_registry.py', "('GET', 'subscription/list')", 8],
];
const searchEvidence = (report, source) => searchSourceContracts.map(args => source(report, ...args));
const listBody = `## Introduzione

In Assozeta apri **Organizzazione → Iscrizioni** per consultare l'elenco dei tesserati.
Ogni riga mostra il nome della persona e le informazioni dell'iscrizione.

<Frame>![Elenco delle iscrizioni dell'associazione](/images/faq/ricerca-filtri/1.png)</Frame>

Per trovare una persona, usa il campo **Cerca...**. [Leggi come cercare e cancellare la ricerca](/faq/come-usare-ricerca-filtri-atleti#la-barra-di-ricerca).`;
const searchBody = `# La barra di ricerca

<Steps>
  <Step title="Apri le iscrizioni">
    Apri **Organizzazione → Iscrizioni**. Sopra l'elenco trovi il campo **Cerca...**.
    <Frame>![Elenco prima della ricerca](/images/faq/ricerca-filtri/1.png)</Frame>
  </Step>
  <Step title="Cerca un nome">
    Scrivi il nome della persona, ad esempio **Giulia**. L'elenco si aggiorna mostrando le iscrizioni che corrispondono alla ricerca.
    <Frame>![Risultato della ricerca per nome](/images/faq/ricerca-filtri/2.png)</Frame>
  </Step>
  <Step title="Controlla i risultati">
    Se nessuna persona corrisponde al testo inserito, l'elenco non mostra righe.
    <Frame>![Ricerca senza corrispondenze](/images/faq/ricerca-filtri/3.png)</Frame>
  </Step>
  <Step title="Cancella la ricerca">
    Premi la **X** dentro il campo di ricerca per svuotarlo e vedere nuovamente l'elenco.
    <Frame>![Elenco ripristinato dopo la ricerca](/images/faq/ricerca-filtri/4.png)</Frame>
  </Step>
</Steps>`;

export function searchDraftPages() {
    return [{path: 'docs/libro-soci.mdx', title: 'Introduzione', id: 'introduzione', body: listBody},
        {path: 'faq/come-usare-ricerca-filtri-atleti.mdx', title: 'La barra di ricerca', id: 'la-barra-di-ricerca', body: searchBody}];
}
export function searchPages(id, report, source) {
    if (id !== 'members-search') return [];
    const evidence = searchEvidence(report, source);
    return searchDraftPages().map(page => ({...page, evidence,
        screenshots: page.id === 'introduzione' ? report.screenshots.slice(0, 1) : report.screenshots}));
}

// Discovery baseline only; this metadata cannot grant source or workflow verification.
export const searchReviewedSources = Object.freeze({
    "UI/src/components/Sidebar.svelte": "9f8344602ae269b5dbbf90dfd6f75e5154f3d5774ad544ac41105b74af2e333c",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854",
    "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
    "UI/src/store/stores.js": "9aaf9d9ef17cc2187bf76144fd9e8808f7fc801cbcb3bd41fdd991cc5a211ac8",
    "BE/application/views/subscriptions_views.py": "d7022114e515be2da57e83b59fbe8042b06a273b61db7030839b31c1297022dc",
    "BE/application/utils/subscriptions_utils.py": "ab922f87b8b1dd491bba41dd86eaabdefd1127c08bdaeb7be4f990657effd22a",
    "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa"
});

// Pure discovery never calls verified builders or fabricates source evidence.
const editorialDescriptor = (page, refs, reviewed, recipe_module, status = page.status, reason = page.reason) => ({
    path: page.path, id: page.id, title: page.title,
    status: ['unsupported', 'needs_external_verification'].includes(status) ? status : 'pending',
    reason: reason || 'Editorial draft metadata only; implementation and workflow proof remain required.',
    recipe_module, verified: false,
    source_contracts: refs.map(([path, symbol, length]) => ({path, symbol, length, reviewed_sha256: reviewed[path]})),
});

export function searchEditorialContracts() {
    const module = "docs/manuale/search-recipes.mjs";
    return searchDraftPages().map(page => editorialDescriptor(page, searchSourceContracts, searchReviewedSources, module));
}
