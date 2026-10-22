// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/routes/association/course/CourseList.svelte": "d68233a0b121dcbf15b840ea120348b331d017b4dd7ab936da95c46f1c50ad84",
  "UI/src/routes/association/course/add/AddCourse.svelte": "c1e10ddcd4c3f258a6e3f4fe387b77a68a85b0ff74191701e169dfad2d38f4a5",
  "UI/src/routes/association/course/add/sections/Section1.svelte": "f3e456ade95d87006f5a490dfef3ce9dd0e1f49ace814205a4cca049bb7622f7",
  "UI/src/routes/association/course/overview/OverviewCourse.svelte": "5aed27d46e9ce99c75452e3cd766db412daaaeca7ac2084a20581358195c06ce",
  "BE/application/views/course_views.py": "5d32e168d9af6100c280b0d0043ca381742f7c2cc300e94f4edd1fd89cb9fc98",
  "BE/application/models/courses_models.py": "6a9cd0b923ab5b6a9dd120bcb7f81c1dac30914657fcd14b3fdb9e9c427d78c6",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "UI/src/routes/association/Members/shared/NavigationTab.svelte": "b00baefb6bd572542cf2b6d2cd74ba67b6bcd87085b923088156617374b9b432",
  "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
  "UI/src/routes/association/Members/MembersListArchive.svelte": "8426ce4abf1e1146aaf85d87a983642170548305820bbdb8590813fe666e3578",
  "UI/src/components/buttons/ArchiveButton.svelte": "1586f271e9651a1126b3f2662d32808de697c4d8b1cccd95150b6371ed32f4bb",
  "BE/application/views/subscriptions_views.py": "1f55021d86c1df051b8b02be483fd463aae04462866a783572933740a4aa11d7",
  "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte","<span class=\"menu-text\">Attività</span>",45],
  ["UI/src/routes.js","'/course/list':",19],
  ["UI/src/routes/association/course/CourseList.svelte","title: 'Aggiungi corso o abbonamento'",18],
  ["UI/src/routes/association/course/add/AddCourse.svelte","function validateForm()",41],
  ["UI/src/routes/association/course/add/AddCourse.svelte","async function createCourse()",58],
  ["UI/src/routes/association/course/add/sections/Section1.svelte","placeholder=\"Titolo Corso\"",25],
  ["UI/src/routes/association/course/add/sections/Section1.svelte","label: 'Quota attività'",22],
  ["UI/src/routes/association/course/overview/OverviewCourse.svelte","async function updateData()",40],
  ["UI/src/routes/association/course/overview/OverviewCourse.svelte","{editingInfo ? 'Annulla' : 'Modifica'}",18],
  ["BE/application/views/course_views.py","def course_add(",111],
  ["BE/application/views/course_views.py","def course_update(",93],
  ["BE/application/models/courses_models.py","class Course(",56],
  ["BE/application/permissions_registry.py","'course/add'",8],
  ["UI/src/routes.js","'/members/archive':",20],
  ["UI/src/routes/association/Members/shared/NavigationTab.svelte","title: 'Archivio'",8],
  ["UI/src/routes/association/Members/MembersList.svelte","async function archiveSelected()",64],
  ["UI/src/routes/association/Members/MembersList.svelte","window.archiveSubscription = async",60],
  ["UI/src/routes/association/Members/MembersListArchive.svelte","text: \"Vuoi spostare l'atleta nel libro soci?\"",51],
  ["UI/src/components/buttons/ArchiveButton.svelte","export let popover_text",26],
  ["BE/application/views/subscriptions_views.py","def subscription_archive(",88],
  ["BE/application/views/payment_views.py","def payment_list(",69],
  ["BE/application/permissions_registry.py","'subscription/*/archive'",8]
];
const sections = [
  {
    "path": "docs/corsi.mdx",
    "id": "creare-un-corso",
    "title": "Creare un corso",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12]
  },
  {
    "path": "docs/corsi.mdx",
    "id": "modificare-le-informazioni-del-corso",
    "title": "Modificare le informazioni del corso",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12]
  },
  {
    "path": "faq/come-archiviare-dati.mdx",
    "id": "cosa-significa-archiviare",
    "title": "Cosa significa archiviare",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [13,14,15,16,17,18,19,20,21]
  },
  {
    "path": "faq/come-archiviare-dati.mdx",
    "id": "archiviazione-manuale",
    "title": "Archiviazione manuale",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [13,14,15,16,17,18,19,20,21]
  },
  {
    "path": "faq/come-archiviare-dati.mdx",
    "id": "consultare-i-dati-archiviati",
    "title": "Consultare i dati archiviati",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [13,14,15,16,17,18,19,20,21]
  },
  {
    "path": "faq/come-archiviare-dati.mdx",
    "id": "ripristinare-i-dati-archiviati",
    "title": "Ripristinare i dati archiviati",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [13,14,15,16,17,18,19,20,21]
  },
  {
    "path": "faq/come-archiviare-dati.mdx",
    "id": "cosa-viene-archiviato",
    "title": "Cosa viene archiviato",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [13,14,15,16,17,18,19,20,21]
  },
  {
    "path": "docs/archivio.mdx",
    "id": "introduzione",
    "title": "Introduzione",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [13,14,15,16,17,18,19,20,21]
  },
  {
    "path": "docs/archivio.mdx",
    "id": "ripristinare-un-elemento-dall-archivio",
    "title": "Ripristinare un elemento dall'archivio",
    "status": "pending",
    "reason": "Procedura in attesa di esecuzione e revisione delle fonti.",
    "contracts": [13,14,15,16,17,18,19,20,21]
  }
];
export function contentEditorialContracts() {
    return editorialDescriptors("docs/manuale/content-recipes.mjs", sections, contracts, reviewed);
}
