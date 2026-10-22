// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/utils/Functions.js": "c9ed79a8214d4e2ceb9d91e2cd3d52f42a32706f96e2890082ba5584c6787147",
  "UI/src/components/buttons/EditButton.svelte": "052f6ca21b3a85dfd713d62d96ccf7c81d8b4af93e15aa109a02e6fcbc74dfe9",
  "UI/src/routes/association/course/instructor/InstructorList.svelte": "1bb5636275f07a54fbde9266959cdfa3518d5f88f1274d513379a345797f60ff",
  "UI/src/routes/association/course/instructor/add/AddInstructor.svelte": "1797eb719e0c25e696388720bd3258f08a0561694be9568759a2228a8ae599b8",
  "UI/src/routes/association/course/instructor/add/sections/Section1.svelte": "991fbfebdbf28eb862ce140436ee8444b3ec16dd2c187bad61cce31600b23a28",
  "UI/src/routes/association/course/instructor/modals/EditModal.svelte": "002185a9e3f93ba4e6260b0f529f7e153494aac6df71d829f6d2320f319b48dc",
  "UI/src/routes/association/course/instructor/info/Instructor.svelte": "1501b33bbe3fc27b751a3d0450038c6b47e78dd8e1c0b781fc43ca1a394fe6a5",
  "UI/src/routes/association/course/instructor/info/modals/AddEditModal.svelte": "19133f13c71dbcf769df0371d32248cff54c7fa2e4291d42c5c28969bd1152e9",
  "BE/application/views/instructor_views.py": "3d957e0a987583744dda1db2d0040eb8b0abdc90b5bf85546debc4a0405d189c",
  "BE/application/serializers/user_serializers.py": "b6c13df61ad39bf22722f3fc69659129fd0cedbbda588e7cbc727989b00e07f6",
  "BE/application/models/user_models.py": "fa9e0b76b80640aedc0e070505c93a75ee5f8c6220a62c07d6fb2e047626c2f6",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "BE/core/settings.py": "947afe0d72ca1b8a11564718893f4ddc1ae9b1d93a5f71d53231baab437c730c"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte","<span class=\"menu-text\">Corsi e Abbonamenti</span>",75],
  ["UI/src/routes.js","'/course/instructor/list':",65],
  ["UI/src/utils/Permissions.js","export const canPerformAction",30],
  ["UI/src/utils/Functions.js","export const getDataFromForm",44],
  ["UI/src/components/buttons/EditButton.svelte","export let popover_text = 'Modifica'",36],
  ["UI/src/routes/association/course/instructor/InstructorList.svelte","const columns =",220],
  ["UI/src/routes/association/course/instructor/add/AddInstructor.svelte","async function createInstructor(data)",142],
  ["UI/src/routes/association/course/instructor/add/sections/Section1.svelte","export let data =",355],
  ["UI/src/routes/association/course/instructor/modals/EditModal.svelte","async function update(data)",126],
  ["UI/src/routes/association/course/instructor/info/Instructor.svelte","async function fetchInfoWidget()",252],
  ["UI/src/routes/association/course/instructor/info/modals/AddEditModal.svelte","if (row.compensation_type == 'hourly')",545],
  ["BE/application/views/instructor_views.py","def instructor_add(request):",30],
  ["BE/application/views/instructor_views.py","def instructor_info(request, uid):",79],
  ["BE/application/views/instructor_views.py","def instructor_hours_list(request, uid):",60],
  ["BE/application/views/instructor_views.py","def instructor_hours_add(request, uid):",22],
  ["BE/application/views/instructor_views.py","def instructor_update(request, uid):",40],
  ["BE/application/serializers/user_serializers.py","class InstructorSerializer(",32],
  ["BE/application/models/user_models.py","class Instructor(models.Model):",72],
  ["BE/application/permissions_registry.py","'instructor/list'",15],
  ["BE/core/settings.py","\"DATE_INPUT_FORMATS\"",2]
];
const sections = [
  {
    "path": "docs/istruttori.mdx",
    "id": "aggiungere-un-istruttore",
    "title": "Aggiungere un Istruttore",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "informazioni-obbligatorie",
    "title": "Informazioni obbligatorie",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "informazioni-anagrafiche",
    "title": "Informazioni anagrafiche",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "informazioni-contratto",
    "title": "Informazioni contratto",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "tariffe-predefinite",
    "title": "Tariffe predefinite",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "account-collaboratore-associato",
    "title": "Account collaboratore associato",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "modificare-un-istruttore",
    "title": "Modificare un Istruttore",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "la-scheda-dell-istruttore",
    "title": "La scheda dell'Istruttore",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "registrare-le-ore-lavorate",
    "title": "Registrare le ore lavorate",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "campi-principali",
    "title": "Campi principali",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "tipi-di-compenso",
    "title": "Tipi di compenso",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "compenso-orario",
    "title": "Compenso orario",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "note-e-salvataggio",
    "title": "Note e salvataggio",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "docs/istruttori.mdx",
    "id": "stato-dei-compensi",
    "title": "Stato dei compensi",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "tutorials/come-impostare-gestire-istruttori.mdx",
    "id": "aggiungere-un-nuovo-istruttore",
    "title": "Aggiungere un nuovo istruttore",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "tutorials/come-impostare-gestire-istruttori.mdx",
    "id": "tipologie-di-compenso",
    "title": "Tipologie di compenso",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "tutorials/come-impostare-gestire-istruttori.mdx",
    "id": "registrare-le-ore-lavorate",
    "title": "Registrare le ore lavorate",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "tutorials/come-impostare-gestire-istruttori.mdx",
    "id": "riepilogo-ore",
    "title": "Riepilogo ore",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  },
  {
    "path": "tutorials/come-impostare-gestire-istruttori.mdx",
    "id": "modificare-i-dati",
    "title": "Modificare i dati",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]
  }
];
export function instructorEditorialContracts() {
    return editorialDescriptors("docs/manuale/instructor-recipes.mjs", sections, contracts, reviewed);
}
