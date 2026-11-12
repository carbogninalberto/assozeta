// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "261e159d3c922f77a6ab19e5b04cdbdeae4ca09c70f1c0abbf4815b8a51be571",
  "UI/src/routes.js": "65c76943b3751fb41201069b7c74d16ef31ca9fc28c86f45c2081fd3fe8876b4",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
  "UI/src/store/stores.js": "9aaf9d9ef17cc2187bf76144fd9e8808f7fc801cbcb3bd41fdd991cc5a211ac8",
  "BE/application/views/subscriptions_views.py": "5fb701ca27d2ca4bbf9a11b0a6f374cd79f6db6bb556f52e10b441aae06f064c",
  "BE/application/utils/subscriptions_utils.py": "2befe90fee8d12947e43ce809973792bc84a5600d768bc4ba9b4a7f6709ea2ef",
  "BE/application/models/subscriptions_models.py": "348705bf0908ccd489167eb26c4975dc51ddfd77d82d7699f7cc1dbf69c17c7c",
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
