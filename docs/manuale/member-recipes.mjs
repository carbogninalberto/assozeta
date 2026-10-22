// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/utils/enumUtils.js": "75b14f5fad15f456e7ad15ae691978698c5ef973bfb1ead9105c48fe815dd811",
  "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
  "UI/src/routes/association/Members/add/AddMember.svelte": "61ccbc7d9274ec1d929e84e89008708838ee108c8266ca6370a23220f1534ce4",
  "UI/src/routes/association/Members/add/sections/Section1.svelte": "f4c6741669d9daeea510dc527de7609a7d8b77b131b5294f89c469140e16d7ca",
  "UI/src/routes/association/Members/add/sections/Section2.svelte": "e18a2e868b99abdd02742ade333eeecdd3432f8b3b1c2b38a66e92f3274a8ca3",
  "UI/src/components/buttons/GenerateTaxCodeButton.svelte": "f57750d056ce12d21cda56441a618afc7da047b2802514b4b51fc69113f564c1",
  "UI/src/routes/association/Members/add/sections/Section3.svelte": "432de3a3765b310628f44046a3848f7a14ba8c066670d5530474bdf09993e323",
  "UI/src/components/signature/SmoothSignature.svelte": "c451cf360e2c9bc68340407e1169ce3b6f91d07ae364932e7fe5253c354e4ff6",
  "UI/src/routes/association/Members/add/sections/Section4.svelte": "645c70a5c555ba94b378df755549fa8455d6e4ee64fbb1cb108a3ffad3da5863",
  "UI/src/routes/association/Members/add/sections/Section6.svelte": "97c429b3fe14f919e8c877251eb7bd9d794afdf0da88b61c74758ddace4d5a46",
  "BE/application/views/subscriptions_views.py": "1f55021d86c1df051b8b02be483fd463aae04462866a783572933740a4aa11d7",
  "BE/application/utils/subscriptions_utils.py": "ab922f87b8b1dd491bba41dd86eaabdefd1127c08bdaeb7be4f990657effd22a",
  "BE/application/serializers/subscriptions_serializers.py": "1e53f7d66701653856b6d5701f5bd80a6d5febb06872a27abc9a50615859f451",
  "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "UI/src/components/buttons/ApproveButton.svelte": "4531bd12ab2764ec6069c516c8797660122deea749f26516ea0851dad58f4f0f",
  "UI/src/routes/accounting/payment/PaymentList.svelte": "31d3e103a34980df77ffe1178198789c0411f819f3ade64baba89ab0dcf3870a",
  "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34",
  "BE/application/signals.py": "e72989b5e66c51b5a480f3f6a8f86a6cac47747421fd3970aa83949b1fbed49e"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte","<span class=\"menu-text\">Organizzazione</span>",50],
  ["UI/src/routes.js","'/members/add':",18],
  ["UI/src/utils/Permissions.js","export const canPerformAction",30],
  ["UI/src/utils/enumUtils.js","export const MembersListType",18],
  ["UI/src/routes/association/Members/MembersList.svelte","canPerformAction('association.members.create')",12],
  ["UI/src/routes/association/Members/add/AddMember.svelte","newUserAccount.set({",55],
  ["UI/src/routes/association/Members/add/AddMember.svelte","text: 'Hai inserito i dati corretti?",33],
  ["UI/src/routes/association/Members/add/sections/Section1.svelte","Informazioni del Profilo",257],
  ["UI/src/routes/association/Members/add/sections/Section2.svelte","Inserisci le informazioni dell'Associato",182],
  ["UI/src/components/buttons/GenerateTaxCodeButton.svelte","async function calculateTaxCode()",29],
  ["UI/src/routes/association/Members/add/sections/Section3.svelte","Firma del documento",65],
  ["UI/src/components/signature/SmoothSignature.svelte","toDataURL(",20],
  ["UI/src/routes/association/Members/add/sections/Section4.svelte","Certificato Medico",63],
  ["UI/src/routes/association/Members/add/sections/Section6.svelte","Controlla le Informazioni",260],
  ["BE/application/views/subscriptions_views.py","def subscription_add(request):",46],
  ["BE/application/views/subscriptions_views.py","def subscription_calculate_tax_code(request):",35],
  ["BE/application/utils/subscriptions_utils.py","def _create_subscription_impl(",520],
  ["BE/application/serializers/subscriptions_serializers.py","class SubscriptionOptimizedSerializer(",220],
  ["BE/application/models/subscriptions_models.py","class Subscription(",65],
  ["BE/application/permissions_registry.py","'subscription/add'",10],
  ["UI/src/routes.js","'/members/list':",18],
  ["UI/src/routes/association/Members/MembersList.svelte","const statusTextDictionary =",9],
  ["UI/src/routes/association/Members/MembersList.svelte","let disableApproval =",35],
  ["UI/src/routes/association/Members/MembersList.svelte","window.acceptSubscription =",66],
  ["UI/src/components/buttons/ApproveButton.svelte","export let disabled =",31],
  ["UI/src/routes/accounting/payment/PaymentList.svelte","window.markAsPaid =",111],
  ["BE/application/views/subscriptions_views.py","def subscription_approve(request, uid):",39],
  ["BE/application/views/payment_views.py","def payment_list(",69],
  ["BE/application/signals.py","def subscription_approved_callback(",11],
  ["BE/application/permissions_registry.py","'subscription/*/approve'",8]
];
const sections = [
  {
    "path": "faq/come-si-crea-un-socio.mdx",
    "id": "creare-un-socio-socio-tesserato-o-tesserato",
    "title": "Creare un socio, socio tesserato o tesserato",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/libro-soci.mdx",
    "id": "aggiungi-un-socio",
    "title": "Aggiungi un Socio",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/libro-soci.mdx",
    "id": "1-informazioni-profilo",
    "title": "1. Informazioni Profilo",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/libro-soci.mdx",
    "id": "2-informazioni-anagrafiche",
    "title": "2. Informazioni Anagrafiche",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/libro-soci.mdx",
    "id": "3-firma-del-documento",
    "title": "3. Firma del Documento",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/libro-soci.mdx",
    "id": "4-certificato-medico",
    "title": "4. Certificato Medico",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/libro-soci.mdx",
    "id": "5-riepilogo-e-creazione",
    "title": "5. Riepilogo e Creazione",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "faq/come-calcolare-generare-codici-fiscali.mdx",
    "id": "come-funziona",
    "title": "Come funziona",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/libro-soci.mdx",
    "id": "ciclo-di-vita-dell-iscrizione",
    "title": "Ciclo di vita dell'iscrizione",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,20,2,21,22,23,24,25,26,18,17,27,28,29]
  }
];
export function memberEditorialContracts() {
    return editorialDescriptors("docs/manuale/member-recipes.mjs", sections, contracts, reviewed);
}
