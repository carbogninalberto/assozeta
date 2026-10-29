// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/routes/association/course/overview/OverviewCourse.svelte": "5aed27d46e9ce99c75452e3cd766db412daaaeca7ac2084a20581358195c06ce",
  "UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte": "d93d905aada618ee7254ff78c57808f100a32dcdd426ecd64ddc42aba84e1bf3",
  "UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte": "061c496436d114b9ed587e8c9f5c8b83ab94a518a142e5118f0a911f3be6ea93",
  "UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte": "cdb4e69ab6a4b231385aecacdd8c427aa0ca03652c79e50bee154ee3626ea7d7",
  "UI/src/routes/association/course/overview/components/Calendar.svelte": "5cdbdd7e450b7ccbcc87eee2349be0b156e9bdb2b79f79f750da99656fb6224e",
  "UI/src/routes/association/course/overview/components/modals/AddCalendarEvent.svelte": "8731b27625a0804f17e0eb1960e1daaa609a54befd29c2a9277f056caedf6c57",
  "UI/src/utils/eventCalendar.js": "fb9858b1170a32edd2562e71f05059ecadecf59e6bddc2c830df09114aa9aa4e",
  "UI/src/routes/association/course/overview/components/Registry.svelte": "c6940274a3eba0b6cc14a28493622aeaa988b7c95c8b974aec3ee22c94b3ee11",
  "UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte": "2dc6d14c9f8ee7b5251af66b84488197472ca714a59d66c455e4c511c7cccd7c",
  "UI/src/routes/association/course/carnet/CarnetList.svelte": "2db1b30b6d9750f9a605e8cb5e150d35341f8e4fb64975c4f82bf3dc744fe8a7",
  "UI/src/routes/association/course/carnet/add/AddCarnet.svelte": "50d83cb2bb0b1c3d32001d41dbceaa959ac214681cf4263da14f32bab7232f74",
  "UI/src/routes/association/course/carnet/add/sections/Section1.svelte": "2b0e02708ea64a0074510839aa89981afa1b772f2fd87694cfa249b1ed61510d",
  "UI/src/routes/association/course/carnet/add/sections/Section2.svelte": "ff2cf863264b2cf0d5656ac2061f8407a5d18c9a0c314fdb75811f6674631965",
  "UI/src/routes/association/course/carnet/detail/CarnetDetail.svelte": "c6b9df096fda3c920fcabd52cbb53733072e6b6059637db47421755409395407",
  "UI/src/routes/association/course/carnet/detail/sections/Info.svelte": "6326b6ff03fc46c26d120fce5a0198c7b392b84d43d398d8a81805320f86c2f7",
  "UI/src/routes/association/course/carnet/detail/sections/Usage.svelte": "569092d44ead6627058eecaf5c0f7bb391dc47f16be54cde5808ca9769c4be4d",
  "UI/src/routes/association/course/carnet/detail/modals/AddCarnetModal.svelte": "00dcea9bfea001c1b12209d79d485448640826173e5bf33da8fc886c7e50857f",
  "UI/src/routes/accounting/payment/PaymentList.svelte": "31d3e103a34980df77ffe1178198789c0411f819f3ade64baba89ab0dcf3870a",
  "BE/application/urls.py": "b475e7ed8c26a891854c4021d7308aefb192d2a26df8b3ee916a33f88e7cc359",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "BE/application/views/course_views.py": "5d32e168d9af6100c280b0d0043ca381742f7c2cc300e94f4edd1fd89cb9fc98",
  // Reviewed: documented carnet workflows are unchanged.
  "BE/application/views/carnet_views.py": "709e3ae74f5ceeeb682899b7c95e371d573a94ebc5c4bce19fa3a485d017c0c6",
  "BE/application/views/attendee_views.py": "fb716a7de4aa8dc5a8db52ca6f6ec05494be54bc00d8358a49ff3e425d7625e3",
  "BE/application/views/payment_views.py": "23bc385b80772d57f9eb32739860ed287fbe7106c0168a9e584221ca13beeb71",
  "BE/application/utils/subscriptions_utils.py": "ab922f87b8b1dd491bba41dd86eaabdefd1127c08bdaeb7be4f990657effd22a",
  "BE/application/utils/attendance_utils.py": "f86f4c8294a50d56c5fe5b26582810acec9fcd0cd5fbd192a4ff511d72bb0a22",
  "BE/application/serializers/courses_serializers.py": "45dc937b0da921f8466e4c21d22bedb8e74a52bd62b66f41f60e3c42b0320632",
  "BE/application/serializers/carnet_serializers.py": "50c7c38f09e9f60146c418c1816172dfb66310106a25faac6583fc2633146b7c",
  "BE/application/models/courses_models.py": "6a9cd0b923ab5b6a9dd120bcb7f81c1dac30914657fcd14b3fdb9e9c427d78c6",
  "BE/application/models/carnet_models.py": "3e3cb2cfb3d2be09cc5db6b97b43e2a2f391d91ccb61dfb38c99bf0ec7913713",
  "BE/application/models/attendee_models.py": "5f7e748de06d17bd2d8eb1bbfa4c9d69eb9d3af6d6ec63bfd0e919b3af5f58f4",
  "BE/application/models/payment_models.py": "9dbcab8698adbde447a9c4f6eb52143409cf197fde3ce90d0aa8da56a1f8ebda",
  "BE/application/models/user_models.py": "fa9e0b76b80640aedc0e070505c93a75ee5f8c6220a62c07d6fb2e047626c2f6",
  "BE/application/tasks.py": "ee7d03286be8cc3e6fec84861496020ac40d2dfe56ec02102fb75c0170d35ebc"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte","<span class=\"menu-text\">Corsi e Abbonamenti</span>",75],
  ["UI/src/routes.js","'/course/carnet/list':",75],
  ["UI/src/utils/Permissions.js","export const canPerformAction",75],
  ["UI/src/routes/association/course/overview/OverviewCourse.svelte","params.page === 'attendance'",75],
  ["UI/src/routes/association/course/overview/components/CourseNavigationTab.svelte","title: 'Presenze'",75],
  ["UI/src/routes/association/course/overview/partials/course-subscriptions-list.svelte","aria-label=\"Aggiungi tesserato\"",75],
  ["UI/src/routes/association/course/overview/modals/AddEditCourseSubscriptionModal.svelte","async function create(formData)",75],
  ["UI/src/routes/association/course/overview/components/Calendar.svelte","async function saveCalendarDirectly(updateEvents)",75],
  ["UI/src/routes/association/course/overview/components/modals/AddCalendarEvent.svelte","id=\"form_add_calendar_event\"",75],
  ["UI/src/utils/eventCalendar.js","export function serializeCalendarEvent(event)",75],
  ["UI/src/routes/association/course/overview/components/Registry.svelte","data-target=\"#attendance-day-",75],
  ["UI/src/routes/association/course/overview/components/modals/MarkAttendees.svelte","async function updateData(idx, checked)",75],
  ["UI/src/routes/association/course/carnet/CarnetList.svelte","field: 'public'",75],
  ["UI/src/routes/association/course/carnet/add/AddCarnet.svelte","async function createCarnet()",75],
  ["UI/src/routes/association/course/carnet/add/sections/Section1.svelte","name=\"fee\"",75],
  ["UI/src/routes/association/course/carnet/add/sections/Section2.svelte","id=\"bkn_select2_athletes\"",75],
  ["UI/src/routes/association/course/carnet/detail/CarnetDetail.svelte","async function updateData()",75],
  ["UI/src/routes/association/course/carnet/detail/sections/Info.svelte","bind:checked={info.public}",75],
  ["UI/src/routes/association/course/carnet/detail/sections/Usage.svelte","function assignCarnet(",75],
  ["UI/src/routes/association/course/carnet/detail/modals/AddCarnetModal.svelte","async function update(formData)",75],
  ["UI/src/routes/accounting/payment/PaymentList.svelte","window.markAsPaid =",75],
  ["BE/application/urls.py","path(r'course/<str:uid>/attendees/<str:attendance_day_uid>/update'",75],
  ["BE/application/permissions_registry.py","'course/*/attendees/*/update'",75],
  ["BE/application/views/course_views.py","class CourseSubscriptionViewSet(",75],
  ["BE/application/views/carnet_views.py","def carnet_assign(request, uid, uid_subscription):",85],
  ["BE/application/views/attendee_views.py","def attendees_update(request, uid, attendance_day_uid):",135],
  ["BE/application/views/payment_views.py","def payment_approve(request, uid):",75],
  ["BE/application/utils/subscriptions_utils.py","def add_course_subscription(",75],
  ["BE/application/utils/attendance_utils.py","def map_unlinked_attendance(",75],
  ["BE/application/serializers/courses_serializers.py","class CourseSubscriptionOverviewSerializer(",75],
  ["BE/application/serializers/carnet_serializers.py","class CarnetAddSerializer(",75],
  ["BE/application/models/courses_models.py","class CourseSubscription(models.Model):",75],
  ["BE/application/models/carnet_models.py","class CarnetSubscription(models.Model):",75],
  ["BE/application/models/attendee_models.py","class AttendanceDay(models.Model):",75],
  ["BE/application/models/payment_models.py","class Payment(GroupModelMixin):",75],
  ["BE/application/models/user_models.py","auto_mark_attendance =",75],
  ["BE/application/tasks.py","def auto_mark_attendance():",75]
];
const sections = [
  {
    "path": "docs/carnet.mdx",
    "id": "cos-e-un-carnet",
    "title": "Cos'è un carnet?",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "come-funziona-un-carnet",
    "title": "Come funziona un carnet?",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "logica-di-scalamento-lezioni",
    "title": "Logica di scalamento lezioni",
    "status": "pending",
    "reason": "Multiple carnet priority and unpaid selected carnet need browser coverage",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "carnet-pubblici-e-privati",
    "title": "Carnet pubblici e privati",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "registro-presenze-automatico-con-carnet",
    "title": "Registro presenze automatico con carnet",
    "status": "pending",
    "reason": "Automatic worker and athlete UI are not exercised",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "blocco-per-assenza-prevista",
    "title": "Blocco per assenza prevista",
    "status": "pending",
    "reason": "Expected absence needs athlete role and actual worker execution",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "creare-e-gestire-un-carnet",
    "title": "Creare e gestire un carnet",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "assegnare-un-carnet-ad-un-atleta",
    "title": "Assegnare un carnet ad un atleta",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "assegnare-un-corso-ad-un-carnet",
    "title": "Assegnare un corso ad un carnet",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "ricaricare-un-carnet",
    "title": "Ricaricare un carnet",
    "status": "pending",
    "reason": "Top-up has reviewed implementation but no captured workflow",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "riepilogo-delle-regole-di-funzionamento",
    "title": "Riepilogo delle regole di funzionamento",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/carnet.mdx",
    "id": "risorse-correlate",
    "title": "Risorse correlate",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "introduzione",
    "title": "Introduzione",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "segnare-le-presenze-manualmente",
    "title": "Segnare le presenze manualmente",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "presenze-automatiche-pro-teams",
    "title": "Presenze automatiche (Pro/Teams)",
    "status": "pending",
    "reason": "Automatic attendance is outside this manual scenario",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "come-funziona",
    "title": "Come funziona",
    "status": "pending",
    "reason": "Actual automatic worker execution is unverified",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "segnalazione-assenze-da-parte-degli-atleti",
    "title": "Segnalazione assenze da parte degli atleti",
    "status": "pending",
    "reason": "Athlete absence UI has no captured evidence",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "attivare-le-presenze-automatiche",
    "title": "Attivare le presenze automatiche",
    "status": "pending",
    "reason": "Settings activation and real worker results need separate coverage",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "interazione-con-i-carnet",
    "title": "Interazione con i carnet",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "scalamento-alla-presenza",
    "title": "Scalamento alla presenza",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "restituzione-alla-rimozione",
    "title": "Restituzione alla rimozione",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "casi-particolari-con-i-carnet",
    "title": "Casi particolari con i carnet",
    "status": "pending",
    "reason": "Unpaid, exhausted, disabled, multiple and no-carnet variants are not captured",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "eliminare-una-lezione-dal-registro",
    "title": "Eliminare una lezione dal registro",
    "status": "pending",
    "reason": "Date deletion and usage implications need their own scenario",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "sincronizzazione-con-il-calendario",
    "title": "Sincronizzazione con il calendario",
    "status": "pending",
    "reason": "Calendar modification and removal synchronization are not exercised",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "come-funziona-la-sincronizzazione",
    "title": "Come funziona la sincronizzazione",
    "status": "pending",
    "reason": "Calendar edit/delete and previous attendance preservation need evidence",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "consultare-lo-storico-presenze-di-un-atleta",
    "title": "Consultare lo storico presenze di un atleta",
    "status": "pending",
    "reason": "Member attendance history and statistics need a separate UI workflow",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/registro-presenze.mdx",
    "id": "risorse-correlate",
    "title": "Risorse correlate",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "indice",
    "title": "Indice",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "visualizzare-il-registro-presenze",
    "title": "Visualizzare il registro presenze",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "segnare-le-presenze-manualmente",
    "title": "Segnare le presenze manualmente",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "presenze-automatiche-pro-teams",
    "title": "Presenze automatiche (Pro/Teams)",
    "status": "pending",
    "reason": "Automatic worker is not exercised",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "come-funziona",
    "title": "Come funziona",
    "status": "pending",
    "reason": "Automatic scheduling and expected absence handling are unverified",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "come-si-attivano-le-presenze-automatiche",
    "title": "Come si attivano le presenze automatiche",
    "status": "pending",
    "reason": "Calendar publishing alone must not imply automatic attendance activation",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "come-funzionano-i-carnet-con-le-presenze",
    "title": "Come funzionano i carnet con le presenze",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "scalamento-delle-lezioni",
    "title": "Scalamento delle lezioni",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "ripristino-delle-lezioni",
    "title": "Ripristino delle lezioni",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "registro-lezioni-nel-carnet",
    "title": "Registro lezioni nel carnet",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "gestire-le-lezioni-perse-e-le-correzioni",
    "title": "Gestire le lezioni perse e le correzioni",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "correggere-una-presenza-errata",
    "title": "Correggere una presenza errata",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "aggiungere-una-presenza-mancante",
    "title": "Aggiungere una presenza mancante",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "consigli-per-la-gestione-dei-carnet",
    "title": "Consigli per la gestione dei carnet",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "controllare-le-lezioni-rimanenti",
    "title": "Controllare le lezioni rimanenti",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "assegnare-un-nuovo-carnet",
    "title": "Assegnare un nuovo carnet",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "modificare-le-lezioni-rimanenti",
    "title": "Modificare le lezioni rimanenti",
    "status": "pending",
    "reason": "Manual balance editor and bounds need dedicated browser evidence",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "tutorials/come-gestire-registro-presenze-carnet.mdx",
    "id": "conclusione",
    "title": "Conclusione",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/corsi.mdx",
    "id": "corso-con-pagamento-per-lezione",
    "title": "Corso con pagamento per lezione",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/corsi.mdx",
    "id": "calendario-delle-lezioni",
    "title": "Calendario delle lezioni",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/corsi.mdx",
    "id": "gestire-le-lezioni",
    "title": "Gestire le lezioni",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/corsi.mdx",
    "id": "lezione-singola",
    "title": "Lezione singola",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/corsi.mdx",
    "id": "pubblicare-il-calendario",
    "title": "Pubblicare il calendario",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  },
  {
    "path": "docs/corsi.mdx",
    "id": "aggiungere-o-rimuovere-atleti",
    "title": "Aggiungere o rimuovere atleti",
    "status": "pending",
    "reason": "Richiede la prova browser reale di questa sezione.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36]
  }
];
export function attendanceCarnetEditorialContracts() {
    return editorialDescriptors("docs/manuale/attendance-carnet-recipes.mjs", sections, contracts, reviewed);
}
