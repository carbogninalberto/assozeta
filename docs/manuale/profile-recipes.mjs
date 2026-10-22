// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
  "UI/src/routes/association/Members/detail/DetailDrawer.svelte": "27863e56e04a266fa1184d72397972b66de35c1e9d097fc9bab29dfe0b493f18",
  "UI/src/routes/association/Members/detail/Detail.svelte": "f7b31cc364221cb6c1acff533dec9ea286a926bbc0fc6a70075e4b5593874bb6",
  "UI/src/routes/association/Members/detail/sections/Info.svelte": "720d401c86e498d7efdba75321ce399fe2788033cf453690b3c6bf6869d5cff7",
  "BE/application/views/subscriptions_views.py": "1f55021d86c1df051b8b02be483fd463aae04462866a783572933740a4aa11d7",
  "BE/application/services/subscription_service.py": "95a5be71f2a72859d80e4f19dbcbcb2c8e61d43180152e7258cffe7ce772201d",
  "BE/application/serializers/subscriptions_serializers.py": "1e53f7d66701653856b6d5701f5bd80a6d5febb06872a27abc9a50615859f451",
  "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "UI/src/routes/association/Members/detail/sections/Medical.svelte": "79d036985cf8fd39eccb9885255b1ccfb9baa4dfa5c657a0bc3d0efcee07e150",
  "UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte": "95803afa6d9e428067e18c4f560c5e70bffa01a57360f0ee8eac746bd2dcf3e8",
  "UI/src/routes/association/Members/detail/sections/modals/medicalCertificateUpload.js": "8b78ae7aaf29c309c5eaf6d8f2ed00078a085beba54ef7316675a585f2a83f21"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte","<span class=\"menu-text\">Organizzazione</span>",50],
  ["UI/src/routes.js","'/members/list':",18],
  ["UI/src/utils/Permissions.js","export const canPerformAction",30],
  ["UI/src/routes/association/Members/MembersList.svelte","let basicDrawer = new DetailDrawer({",20],
  ["UI/src/routes/association/Members/detail/DetailDrawer.svelte","<BasicDrawer",7],
  ["UI/src/routes/association/Members/detail/Detail.svelte","function switchTab(tab)",42],
  ["UI/src/routes/association/Members/detail/Detail.svelte","class=\"nav nav-tabs",122],
  ["UI/src/routes/association/Members/detail/sections/Info.svelte","function updateInfo()",93],
  ["UI/src/routes/association/Members/detail/sections/Info.svelte","subscription_number: {",12],
  ["UI/src/routes/association/Members/detail/sections/Info.svelte","canPerformAction('association.members.update')",12],
  ["UI/src/routes/association/Members/detail/sections/Info.svelte","Informazioni Iscrizione",213],
  ["BE/application/views/subscriptions_views.py","def subscription_update(request, uid):",49],
  ["BE/application/services/subscription_service.py","def update_subscription(subscription, data):",46],
  ["BE/application/serializers/subscriptions_serializers.py","class SubscriptionInfoSerializer(",43],
  ["BE/application/models/subscriptions_models.py","subscription_number =",14],
  ["BE/application/permissions_registry.py","'subscription/*/update'",14],
  ["UI/src/routes/association/Members/detail/sections/Medical.svelte","async function updateCertificateSettings(e)",23],
  ["UI/src/routes/association/Members/detail/sections/Medical.svelte","Informazioni certificato medico",151],
  ["UI/src/routes/association/Members/detail/sections/Medical.svelte","Nuovo Certificato",9],
  ["UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte","async function handleSubmit(e)",47],
  ["UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte","async function receiveFile(file)",50],
  ["UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte","async function initializeDropzone(",34],
  ["UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte","Trascina o premi per caricare",75],
  ["UI/src/routes/association/Members/detail/sections/modals/medicalCertificateUpload.js","export function validateCertificateFile(file)",9],
  ["UI/src/routes/association/Members/detail/sections/modals/medicalCertificateUpload.js","export async function uploadCertificate(",10],
  ["BE/application/services/subscription_service.py","def upload_medical_certificate(",48],
  ["BE/application/services/subscription_service.py","def attach_certificate_to_subscription(",33],
  ["BE/application/views/subscriptions_views.py","def subscription_medical_certificate_upload(",45],
  ["BE/application/views/subscriptions_views.py","def subscription_medical_certificate_set_certificate_expiration(",93],
  ["BE/application/views/subscriptions_views.py","def subscription_medical_certificate_edit(",27],
  ["BE/application/permissions_registry.py","'subscription/*/medical-certificate/upload'",5]
];
const sections = [
  {
    "path": "docs/libro-soci.mdx",
    "id": "anagrafica-smart",
    "title": "Anagrafica Smart",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15]
  },
  {
    "path": "tutorials/come-gestire-certificati-medici.mdx",
    "id": "dal-profilo-dell-atleta",
    "title": "Dal profilo dell'atleta",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30]
  },
  {
    "path": "tutorials/come-gestire-certificati-medici.mdx",
    "id": "impostare-la-data-di-scadenza",
    "title": "Impostare la data di scadenza",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30]
  },
  {
    "path": "tutorials/come-gestire-certificati-medici.mdx",
    "id": "tieni-traccia-dei-certificati-agonistici",
    "title": "Tieni traccia dei certificati agonistici",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30]
  },
  {
    "path": "tutorials/come-gestire-certificati-medici.mdx",
    "id": "consigli-per-una-gestione-efficiente",
    "title": "Consigli per una gestione efficiente",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30]
  }
];
export function profileEditorialContracts() {
    return editorialDescriptors("docs/manuale/profile-recipes.mjs", sections, contracts, reviewed);
}
