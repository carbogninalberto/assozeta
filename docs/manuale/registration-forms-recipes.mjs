// Profile source re-reviewed: profile, association and audit writes now commit atomically;
// locked reads and selected columns preserve concurrent changes. Successful settings
// and registration workflows remain unchanged; failed saves leave no partial profile.
// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/routes/association/Members/subscription/Template.svelte": "e01e73eb4435b7ea9ecf35971dd59182c4b6752fd404921e44a0770a64682903",
  "UI/src/routes/association/Members/subscription/partials/TypesModule.svelte": "f83c5113b5bf8ce41420ceafe5a8311802284cb4c9e4ca2891e94c0cca5924bd",
  "UI/src/routes/association/Members/subscription/partials/QuoteIscrizione.svelte": "c9d41719a3ec8cff5fd5d7896564d7a6c471f8904cae7d3765ceb36ae2d0351c",
  "UI/src/routes/association/Members/subscription/partials/MultipleQuotesSubscription.svelte": "bd2fb5fd7c6806d6790f4c6bd45f8da7d0d509b2914060a2dd32beedac349fe9",
  "UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte": "93b6aa5457512d0c4a7180de0c495e18f77875138e0cbc64a93da15867b579d9",
  "UI/src/routes/association/Members/subscription/partials/AdditionalFields.svelte": "cb891aa575f32dcd3dde1e51c7d45dec9c2527875c96d7beb3f110b933617872",
  "UI/src/components/formBuilder/composer-sidebar.svelte": "a18c05c665ae9b954989076a54a38ba5198ac4e52265fca5ec80cdc3e819492f",
  "UI/src/components/formBuilder/composer-preview.svelte": "9ca8a42453e4dddf78eb261e731c55b008c86f052a0da0bb8dda91b745b6d829",
  "UI/src/components/formBuilder/inputs.js": "e6cebe776d4449644dcd0e63cfa2217ad57278f0de6e3becaa5a9d5152aee08e",
  "UI/src/components/formBuilder/preview-blocks/text-input.svelte": "5d6a0020e819ef413c5c3e407a664957526378312c059ca4410a659b27df6ba1",
  "UI/src/routes/association/Members/MembersBook.svelte": "e123edd1de7d765f5a9b98d9ad019940ed2de36c40df3d78d1e605205849ae7d",
  "UI/src/components/modals/ShareModuleSubscriptionLink.svelte": "999bf9e16be7b8389a0181cd7f5c45f383dcb9c690925b653b9bf7d3333261ea",
  "UI/src/routes/association/Members/subscription/share/link-share-header.svelte": "384f60f3b95d3d1acaecf7ede4d2d01d9d29763740a31c84d0c87994a1a30246",
  "UI/src/routes/subscribe/Subscribe.svelte": "378e90166b90d806ad9b1eface40d2cd9125c08cb35b248868ac6e9bb3b1e51b",
  "UI/src/routes/subscribe/wizard/Step0.svelte": "3350b71dd2dd2b3cfe1790f9e91ca4d2bf5e70bd94247c5ab59b49965a9d1edf",
  "UI/src/routes/subscribe/wizard/Step1.svelte": "50bbcd39de0d80293f59f6a0953a2498efd488ebf062582ae6b964c4b9a4c4f5",
  "UI/src/routes/subscribe/wizard/partials/additional-fields.svelte": "25eacb675ca1f76cf2ceae6af39126b8e32032acc6e395a414b087d838d311b3",
  "UI/src/routes/subscribe/wizard/Step2.svelte": "871d4ff3b52672efd93b0348c3b587d355aeaa31edd60696d777e1b5db35a128",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "BE/application/urls.py": "b475e7ed8c26a891854c4021d7308aefb192d2a26df8b3ee916a33f88e7cc359",
  "BE/application/views/profile_views.py": "66719e85b6e918943fd7246f7ec15c39e98b77f1ab32b8b443f9a3de60b94697",
  "BE/application/views/search_views.py": "0cc8b4378133ef496ae8d193ad0904456dc92f394f8a04aebf9a5e3d58e1e380",
  "BE/application/serializers/auth_serializers.py": "9285271db47ebe8dce875986175892991e868ff207f4d4f0d29fe69051aaf56a",
  "BE/application/models/user_models.py": "fa9e0b76b80640aedc0e070505c93a75ee5f8c6220a62c07d6fb2e047626c2f6",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "UI/src/routes/profile/sections/Settings.svelte": "aed683b4bbb10c7a226a6eed573d1d748defc4d8baa7c13fd121e833b6561901",
  "UI/src/routes/association/archive/TemplatesList.svelte": "2fb5b32a1326a36787f24bca61ad8a456f420c90742d8da30938fa3d72d6f173",
  "UI/src/routes/association/archive/detail/partials/template-form.svelte": "83348b084cf65b145bbd366c0f388abe6cdb8156064e07708346dc72d6141cad",
  "UI/src/routes/association/Members/detail/sections/Cloud.svelte": "dc79dccd5dd5c90a20903493c5483d8cb84eab327f33fc7ee3f47512e46d8896",
  "UI/src/routes/association/archive/modals/GenerateFromTemplateModal.svelte": "d68a581779ef24b65f2ed0afa0de99ee880079d1b7a6cab218f05b5dfa064c81",
  "BE/application/views/archive_views.py": "4507583ea5811c7a0287ea0e83456f6a5b1707efe38bb950cedb23a0efcfae96",
  "BE/docmanager/views/printing_views.py": "f9127869836c46408d9e5f48fc16733c6fce66efedb2e2fa7f96344095e01753"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte",">Modulo Iscrizioni",7],
  ["UI/src/routes.js","'/members/subscription/template':",20],
  ["UI/src/routes.js","'/subscribe/:sportAssociationUsername?':",8],
  ["UI/src/routes/association/Members/subscription/Template.svelte","const updateAccountInformation",96],
  ["UI/src/routes/association/Members/subscription/Template.svelte","Anteprima Modulo Iscrizioni",22],
  ["UI/src/routes/association/Members/subscription/partials/TypesModule.svelte","function updateVisibleOptions()",35],
  ["UI/src/routes/association/Members/subscription/partials/QuoteIscrizione.svelte","Associa quote iscrizione",140],
  ["UI/src/routes/association/Members/subscription/partials/MultipleQuotesSubscription.svelte","<script>",270],
  ["UI/src/routes/association/Members/subscription/partials/ModuleSections.svelte","Sezioni aggiuntive",127],
  ["UI/src/routes/association/Members/subscription/partials/AdditionalFields.svelte","<script>",45],
  ["UI/src/components/formBuilder/composer-sidebar.svelte","function pushElement(element)",45],
  ["UI/src/components/formBuilder/composer-preview.svelte","<script>",105],
  ["UI/src/components/formBuilder/inputs.js","export const InputTypes =",160],
  ["UI/src/components/formBuilder/preview-blocks/text-input.svelte","<script>",180],
  ["UI/src/routes/association/Members/MembersBook.svelte","Condividi link iscrizioni",12],
  ["UI/src/components/modals/ShareModuleSubscriptionLink.svelte","<script>",245],
  ["UI/src/routes/association/Members/subscription/share/link-share-header.svelte","<script>",62],
  ["UI/src/routes/subscribe/Subscribe.svelte","async function loadData()",45],
  ["UI/src/routes/subscribe/wizard/Step0.svelte","enabled_for?.length == 1",245],
  ["UI/src/routes/subscribe/wizard/Step1.svelte","<AdditionalFields",25],
  ["UI/src/routes/subscribe/wizard/partials/additional-fields.svelte","<script>",25],
  ["UI/src/routes/subscribe/wizard/Step2.svelte","Accetto le seguenti clausole",70],
  ["UI/src/utils/Permissions.js","export const canPerformAction",32],
  ["BE/application/urls.py","path(r'profile/update/subscription/template'",2],
  ["BE/application/views/profile_views.py","def profile_update_subscription_template(request):",140],
  ["BE/application/views/profile_views.py","def profile_info(request):",49],
  ["BE/application/views/search_views.py","def search_profile(request, username):",60],
  ["BE/application/serializers/auth_serializers.py","class SportAssociationSerializer(",68],
  ["BE/application/serializers/auth_serializers.py","class SportAssociationSearchProfileSerializer(",29],
  ["BE/application/models/user_models.py","class SportAssociation(models.Model):",100],
  ["BE/application/permissions_registry.py","'profile/update/subscription/template':",2],
  ["UI/src/routes/profile/sections/Settings.svelte",">Anno fiscale</label>",315],
  ["UI/src/routes/association/archive/TemplatesList.svelte","Modulistica",38],
  ["UI/src/routes/association/archive/detail/partials/template-form.svelte","async function create()",232],
  ["UI/src/routes/association/Members/detail/sections/Cloud.svelte","Genera da Modello",16],
  ["UI/src/routes/association/archive/modals/GenerateFromTemplateModal.svelte","async function create()",113],
  ["BE/application/views/archive_views.py","class SportAssociationModuleTemplatesViewSet(",100],
  ["BE/docmanager/views/printing_views.py","def document_template(request, uid):",58]
];
const sections = [
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "indice",
    "title": "Indice",
    "status": "pending",
    "reason": "L’indice comprende argomenti che richiedono prove separate.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "configurare-l-anno-sportivo-l-anno-fiscale-e-le-causali-per-i-pagamenti",
    "title": "Configurare l'anno sportivo, l'anno fiscale e le causali per i pagamenti",
    "status": "pending",
    "reason": "La variante richiede una propria prova browser e controlli dello stato salvato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "causali-predefinite",
    "title": "Causali predefinite",
    "status": "pending",
    "reason": "Le causali e la loro applicazione a nuovi pagamenti non sono esercitate.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "durata-di-iscrizioni-e-tesseramenti",
    "title": "Durata di iscrizioni e Tesseramenti",
    "status": "pending",
    "reason": "La variante richiede una propria prova browser e controlli dello stato salvato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "configurare-il-modulo-d-iscrizione",
    "title": "Configurare il modulo d'iscrizione",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "cosa-scegliere-per-le-iscrizioni",
    "title": "Cosa scegliere per le iscrizioni?",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "impostare-le-quote",
    "title": "Impostare le quote",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "impostare-quote-multiple",
    "title": "Impostare quote multiple",
    "status": "pending",
    "reason": "Non sono creati piani multipli, quote parziali o pagamenti successivi.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "visualizzare-un-anteprima-del-modulo-d-iscrizione-digitale",
    "title": "Visualizzare un'anteprima del modulo d'iscrizione digitale",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "personalizzare-il-modulo-d-iscrizione",
    "title": "Personalizzare il modulo d'iscrizione",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "sezioni-del-modulo-d-iscrizione",
    "title": "Sezioni del modulo d'iscrizione",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "campi-aggiuntivi",
    "title": "Campi aggiuntivi",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "creare-un-modulo-completamente-personalizzato",
    "title": "Creare un modulo completamente personalizzato",
    "status": "pending",
    "reason": "Manca una prova browser e PDF dedicata ai modelli dell’Archivio.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "come-assegnare-un-modulo-personalizzato",
    "title": "Come assegnare un modulo personalizzato?",
    "status": "pending",
    "reason": "Generazione del PDF, contenuto finale e associazione alla persona non dimostrati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "tutorials/come-creare-moduli-iscrizione-personalizzati.mdx",
    "id": "conclusione",
    "title": "Conclusione",
    "status": "pending",
    "reason": "Il riepilogo comprende varianti ancora da verificare.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "faq/come-condividere-il-link-iscrizioni.mdx",
    "id": "condividere-il-link-per-le-iscrizioni",
    "title": "Condividere il link per le iscrizioni",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "faq/come-condividere-il-link-iscrizioni.mdx",
    "id": "dove-trovo-il-link-di-iscrizione",
    "title": "Dove trovo il link di iscrizione?",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "faq/come-condividere-il-link-iscrizioni.mdx",
    "id": "modalita-di-condivisione",
    "title": "Modalità di condivisione",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "faq/come-condividere-il-link-iscrizioni.mdx",
    "id": "1-copia-il-link",
    "title": "1. Copia il link",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "faq/come-condividere-il-link-iscrizioni.mdx",
    "id": "2-whatsapp",
    "title": "2. WhatsApp",
    "status": "needs_external_verification",
    "reason": "Servizio WhatsApp, invio e consegna non esercitati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "faq/come-condividere-il-link-iscrizioni.mdx",
    "id": "3-email",
    "title": "3. Email",
    "status": "needs_external_verification",
    "reason": "Client email, destinatari effettivi, invio e consegna non esercitati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "faq/come-condividere-il-link-iscrizioni.mdx",
    "id": "4-qr-code",
    "title": "4. QR Code",
    "status": "pending",
    "reason": "Mancano stampa e scansione reali; il download diretto come immagine descritto nel vecchio testo non è dimostrato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  },
  {
    "path": "faq/come-condividere-il-link-iscrizioni.mdx",
    "id": "cosa-vede-chi-apre-il-link",
    "title": "Cosa vede chi apre il link?",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37]
  }
];
export function registrationFormsEditorialContracts() {
    return editorialDescriptors("docs/manuale/registration-forms-recipes.mjs", sections, contracts, reviewed);
}
