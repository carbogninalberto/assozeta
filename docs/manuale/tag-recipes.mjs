// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
  "UI/src/components/tables/BKNDatatable.svelte": "a236f1526b3033905925d3354b4361907c2f3e96699efc1e59cc1e927c6b9692",
  "BE/application/views/subscriptions_views.py": "1f55021d86c1df051b8b02be483fd463aae04462866a783572933740a4aa11d7",
  "BE/application/services/subscription_service.py": "95a5be71f2a72859d80e4f19dbcbcb2c8e61d43180152e7258cffe7ce772201d",
  "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "BE/application/urls.py": "b475e7ed8c26a891854c4021d7308aefb192d2a26df8b3ee916a33f88e7cc359"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte","<span class=\"menu-text\">Organizzazione</span>",50],
  ["UI/src/routes.js","'/members/list':",18],
  ["UI/src/utils/Permissions.js","export const canPerformAction",30],
  ["UI/src/routes/association/Members/MembersList.svelte","async function createTag()",25],
  ["UI/src/routes/association/Members/MembersList.svelte","async function assignTag()",110],
  ["UI/src/components/tables/BKNDatatable.svelte","data-row={rowIndex}",50],
  ["BE/application/views/subscriptions_views.py","def subscription_tags_add(",25],
  ["BE/application/views/subscriptions_views.py","def subscription_tags_assign(",20],
  ["BE/application/views/subscriptions_views.py","def subscription_tags_unassign(",20],
  ["BE/application/services/subscription_service.py","def create_tag(",28],
  ["BE/application/services/subscription_service.py","def assign_tag_to_subscription(",42],
  ["BE/application/services/subscription_service.py","def unassign_tag_from_subscription(",40],
  ["BE/application/models/subscriptions_models.py","class Tags(",18],
  ["BE/application/permissions_registry.py","'subscription/tags/add'",6],
  ["BE/application/urls.py","path(r'subscription/tags/list'",6]
];
const sections = [
  {
    "path": "tutorials/come-assegnare-i-tag-agli-atleti.mdx",
    "id": "indice",
    "title": "Indice",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14]
  },
  {
    "path": "tutorials/come-assegnare-i-tag-agli-atleti.mdx",
    "id": "creare-un-nuovo-tag",
    "title": "Creare un nuovo tag",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14]
  },
  {
    "path": "tutorials/come-assegnare-i-tag-agli-atleti.mdx",
    "id": "seleziona-gli-atleti",
    "title": "Seleziona gli atleti",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14]
  },
  {
    "path": "tutorials/come-assegnare-i-tag-agli-atleti.mdx",
    "id": "assegna-tag",
    "title": "Assegna Tag",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14]
  },
  {
    "path": "tutorials/come-assegnare-i-tag-agli-atleti.mdx",
    "id": "modificare-e-o-eliminare-uno-o-piu-tag",
    "title": "Modificare e/o eliminare uno o più tag",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14]
  }
];
export function tagEditorialContracts() {
    return editorialDescriptors("docs/manuale/tag-recipes.mjs", sections, contracts, reviewed);
}
