// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "261e159d3c922f77a6ab19e5b04cdbdeae4ca09c70f1c0abbf4815b8a51be571",
  "UI/src/routes.js": "65c76943b3751fb41201069b7c74d16ef31ca9fc28c86f45c2081fd3fe8876b4",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
  "UI/src/components/tables/BKNDatatable.svelte": "a236f1526b3033905925d3354b4361907c2f3e96699efc1e59cc1e927c6b9692",
  "BE/application/views/subscriptions_views.py": "5fb701ca27d2ca4bbf9a11b0a6f374cd79f6db6bb556f52e10b441aae06f064c",
  "BE/application/services/subscription_service.py": "dabb1369f276602e5f95694843c61c94e63b947133b908d3edcb7d77fec01d82",
  "BE/application/models/subscriptions_models.py": "348705bf0908ccd489167eb26c4975dc51ddfd77d82d7699f7cc1dbf69c17c7c",
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
