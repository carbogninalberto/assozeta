// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "261e159d3c922f77a6ab19e5b04cdbdeae4ca09c70f1c0abbf4815b8a51be571",
  "UI/src/routes.js": "65c76943b3751fb41201069b7c74d16ef31ca9fc28c86f45c2081fd3fe8876b4",
  "UI/src/routes/association/course/CourseList.svelte": "d68233a0b121dcbf15b840ea120348b331d017b4dd7ab936da95c46f1c50ad84",
  "UI/src/routes/association/course/add/AddCourse.svelte": "c1e10ddcd4c3f258a6e3f4fe387b77a68a85b0ff74191701e169dfad2d38f4a5",
  "UI/src/routes/association/course/add/sections/Section1.svelte": "f3e456ade95d87006f5a490dfef3ce9dd0e1f49ace814205a4cca049bb7622f7",
  "UI/src/routes/association/course/overview/OverviewCourse.svelte": "5aed27d46e9ce99c75452e3cd766db412daaaeca7ac2084a20581358195c06ce",
  "BE/application/views/course_views.py": "28ce719b7f6897bccde939737866533899649def382f68a47def300a5dbc1c66",
  "BE/application/models/courses_models.py": "58b9f15cc8b2a946469685305b924aa5db38043ba6197c894bd25c9006819671",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "UI/src/routes/association/Members/shared/NavigationTab.svelte": "b00baefb6bd572542cf2b6d2cd74ba67b6bcd87085b923088156617374b9b432",
  "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
  "UI/src/routes/association/Members/MembersListArchive.svelte": "8426ce4abf1e1146aaf85d87a983642170548305820bbdb8590813fe666e3578",
  "UI/src/components/buttons/ArchiveButton.svelte": "1586f271e9651a1126b3f2662d32808de697c4d8b1cccd95150b6371ed32f4bb",
  "BE/application/views/subscriptions_views.py": "5fb701ca27d2ca4bbf9a11b0a6f374cd79f6db6bb556f52e10b441aae06f064c",
  "BE/application/views/payment_views.py": "7d63fd98c253499dbb7d2be9026f1fd2921f89518e7aa854713e29642d88afae"
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
