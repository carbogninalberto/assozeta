// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
  "UI/src/store/stores.js": "9aaf9d9ef17cc2187bf76144fd9e8808f7fc801cbcb3bd41fdd991cc5a211ac8",
  "BE/application/views/subscriptions_views.py": "1f55021d86c1df051b8b02be483fd463aae04462866a783572933740a4aa11d7",
  "BE/application/utils/subscriptions_utils.py": "ab922f87b8b1dd491bba41dd86eaabdefd1127c08bdaeb7be4f990657effd22a",
  "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte","<span class=\"menu-text\">Organizzazione</span>",50],
  ["UI/src/routes.js","'/members/list':",18],
  ["UI/src/utils/Permissions.js","export const canPerformAction",30],
  ["UI/src/routes/association/Members/MembersList.svelte","function scheduleSearch()",12],
  ["UI/src/routes/association/Members/MembersList.svelte","bind:value={$subscriptionListFilter.generalSearch}",32],
  ["UI/src/store/stores.js","export const subscriptionListFilterDatatable",22],
  ["BE/application/views/subscriptions_views.py","def subscription_list(request):",65],
  ["BE/application/views/subscriptions_views.py","def _apply_search_filter(",46],
  ["BE/application/utils/subscriptions_utils.py","def smart_search(",24],
  ["BE/application/utils/subscriptions_utils.py","def sqlite_search(",82],
  ["BE/application/models/subscriptions_models.py","class Subscription(",65],
  ["BE/application/permissions_registry.py","('GET', 'subscription/list')",8]
];
const sections = [
  {
    "path": "docs/libro-soci.mdx",
    "id": "introduzione",
    "title": "Introduzione",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11]
  },
  {
    "path": "faq/come-usare-ricerca-filtri-atleti.mdx",
    "id": "la-barra-di-ricerca",
    "title": "La barra di ricerca",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11]
  }
];
export function searchEditorialContracts() {
    return editorialDescriptors("docs/manuale/search-recipes.mjs", sections, contracts, reviewed);
}
