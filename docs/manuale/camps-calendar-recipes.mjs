// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/components/Sidebar.svelte": "da92d11c5ca4d82b3e923cb221c29ee348f0f4df57c79df38d2af9bc33bc487a",
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "UI/src/utils/Functions.js": "c9ed79a8214d4e2ceb9d91e2cd3d52f42a32706f96e2890082ba5584c6787147",
  "UI/src/routes/association/course/campsAndRetreats/CampsAndRetreatsList.svelte": "22150f2bff9e259b3c9ac25e30f3727d3bfa41fb70d5cacf2cfca4ec3bc626f6",
  "UI/src/routes/association/course/campsAndRetreats/modals/AddModal.svelte": "061914f717490b6266becc60010d682806eb14e1e1a101df51a3ba5549f1427d",
  "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsOverview.svelte": "4c2acaf4a470d6f58d978075cc48ec9385ecc90d42efa689516432fa0777cb33",
  "UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsPeriodOverview.svelte": "547f651a018373db48da0397d21185fc7f3bb0a160cfa996dc5c75935ed98563",
  "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddModal.svelte": "8f1ff99f429affb76796f67f67e6b1e2af7c2a612a7cadef80073afec8f90b2c",
  "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-periods-form.svelte": "a50bef6baae85c88823eca243160fb248e58752c3ab250e1eba854307e60f5ed",
  "UI/src/components/inputs/DateRangePicker.svelte": "d54685f975199b3acd301e0f26ea5f13fe3f9c6f22d11da187efeb7a782c0687",
  "UI/src/components/inputs/DateRangeCalendar.svelte": "0f1347e4bcc861917f53f0d118c9c35f8207172644a9191b0f9ef6a50440b281",
  "UI/src/routes/association/course/campsAndRetreats/overview/modals/AddServiceModal.svelte": "7ebb41be26d34c7fe84b5a60915ade6460ba12212e55f6610d7f0630d5e1dfca",
  "UI/src/routes/association/course/campsAndRetreats/overview/modals/EditServiceModal.svelte": "2a153566f8486a9bb1e088e5e9a87c0ec808eeb386a318168c2bf846b4f7857a",
  "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-services-form.svelte": "1eb7e2a5a959b13c6b5fc94c1e0001f5c3d84d86367e421abee704a006c94d78",
  "UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-subscriptions-form.svelte": "4d9a4e38e4c0ed3c2751c8c22a77c614007867d9af8a89d2860752cc23b9abf2",
  "UI/src/routes/association/course/campsAndRetreats/overview/modals/components/PeriodAndServiceSelector.svelte": "045c64688e145e887b0d6025067c6162c87100607cc66d497cbd4c92ac53ef97",
  "UI/src/routes/forms/CampsAndRetreatsForm.svelte": "2b01ff6aae3fa5d67ee621f693c3d00e6a19e741c32b3c1eeaf81911f764caa9",
  "BE/application/views/camp_and_retreats_views.py": "9cceb73c7d4587564b990fb95ac8c038ac32a828527e9b6c3479903e9fff10f2",
  "BE/application/serializers/camps_and_retreats_serializers.py": "1376005299ec37442d6599ab272ebf1fb649274c994ee4330ce64f0bb5d1b3c2",
  "BE/application/utils/camps_and_retreats_utils.py": "1ecc605be1970691270a2f296f7b6185cf31258f09630244db83352362c04ce1",
  "BE/application/models/courses_models.py": "6a9cd0b923ab5b6a9dd120bcb7f81c1dac30914657fcd14b3fdb9e9c427d78c6",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "UI/src/routes/calendar/Calendar.svelte": "4ba491bb4384adef00abe3c50996f8eac3302a43e64ad07df8d3c3977ebba63e",
  "UI/src/routes/calendar/modals/AddCalendarEvent.svelte": "764483c367969b378edbbf2b0348e6d6fe5626d53de98782fbfd79a1b9b5db6f",
  "UI/src/routes/association/course/overview/OverviewCourse.svelte": "5aed27d46e9ce99c75452e3cd766db412daaaeca7ac2084a20581358195c06ce",
  "UI/src/routes/association/course/overview/components/Calendar.svelte": "5cdbdd7e450b7ccbcc87eee2349be0b156e9bdb2b79f79f750da99656fb6224e",
  "UI/src/routes/calendar/SharedCalendar.svelte": "e227f0c66c62b3fe02e546509939453fc7bd8dbe5e4e5cd07fb99affdc0e5a42",
  "BE/application/views/attendee_views.py": "fb716a7de4aa8dc5a8db52ca6f6ec05494be54bc00d8358a49ff3e425d7625e3",
  "BE/application/utils/global_calendar.py": "eac4ba8a31d10419af31d18fd36d972ba85a6d3f7251193a56aeda97ec717b47",
  "BE/application/models/attendee_models.py": "5f7e748de06d17bd2d8eb1bbfa4c9d69eb9d3af6d6ec63bfd0e919b3af5f58f4",
  "UI/src/routes/profile/sections/Integrations.svelte": "939a23d1aa9a1e73f98387275cee630504a8c863fa2e901d5ad683fe470ad6ae",
  "BE/application/views/google_views.py": "a5caee899a34f44ec77f9ffaaca0960d68dd11f4bfb23745907d26f16b5c2ae9",
  "BE/application/urls.py": "b475e7ed8c26a891854c4021d7308aefb192d2a26df8b3ee916a33f88e7cc359"
};
const contracts = [
  ["UI/src/components/Sidebar.svelte","href=\"/#/course/camps-and-retreats/list\"",28],
  ["UI/src/routes.js","'/course/camps-and-retreats/list':",60],
  ["UI/src/routes.js","'/calendar':",26],
  ["UI/src/utils/Permissions.js","export const setPermissions",58],
  ["UI/src/utils/Functions.js","export const getDataFromForm",28],
  ["UI/src/routes/association/course/campsAndRetreats/CampsAndRetreatsList.svelte","<BKNDatatable",115],
  ["UI/src/routes/association/course/campsAndRetreats/modals/AddModal.svelte","async function create(data)",48],
  ["UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsOverview.svelte","Condividi il link",110],
  ["UI/src/routes/association/course/campsAndRetreats/overview/CampsAndRetreatsPeriodOverview.svelte","async function update(formData)",170],
  ["UI/src/routes/association/course/campsAndRetreats/overview/modals/AddModal.svelte","async function create(formData)",46],
  ["UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-periods-form.svelte","<form id=\"camps_and_retreats_periods_form\"",92],
  ["UI/src/components/inputs/DateRangePicker.svelte","function applyPanel()",47],
  ["UI/src/components/inputs/DateRangeCalendar.svelte","<div class=\"date-range-calendar\">",67],
  ["UI/src/routes/association/course/campsAndRetreats/overview/modals/AddServiceModal.svelte","async function create(formData)",88],
  ["UI/src/routes/association/course/campsAndRetreats/overview/modals/EditServiceModal.svelte","async function update(formData)",124],
  ["UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-services-form.svelte","async function fetchCategories()",111],
  ["UI/src/routes/association/course/campsAndRetreats/overview/modals/camps-and-retreats-subscriptions-form.svelte","function fetchSubscriptions()",98],
  ["UI/src/routes/association/course/campsAndRetreats/overview/modals/components/PeriodAndServiceSelector.svelte","export let paidPeriods",185],
  ["UI/src/routes/forms/CampsAndRetreatsForm.svelte","<script>",180],
  ["BE/application/views/camp_and_retreats_views.py","def camps_and_retreats_add(request):",240],
  ["BE/application/views/camp_and_retreats_views.py","def camps_and_retreats_periods_services_update(request, uid):",83],
  ["BE/application/views/camp_and_retreats_views.py","def camps_and_retreats_subscriptions_add(request, uid):",104],
  ["BE/application/serializers/camps_and_retreats_serializers.py","class CampsAndRetreatsPeriodSerializer(",42],
  ["BE/application/utils/camps_and_retreats_utils.py","def generate_payment_for_periods(subscription):",50],
  ["BE/application/models/courses_models.py","class CampsAndRetreats(models.Model):",62],
  ["BE/application/permissions_registry.py","'camps-and-retreats/list':",19],
  ["BE/application/permissions_registry.py","('GET', 'calendar/events'):",12],
  ["UI/src/routes/calendar/Calendar.svelte","async function saveCalendarDirectly(",47],
  ["UI/src/routes/calendar/Calendar.svelte","buttonText: function (texts)",98],
  ["UI/src/routes/calendar/Calendar.svelte","eventClick: async function (info)",179],
  ["UI/src/routes/calendar/Calendar.svelte","Nuovo evento",98],
  ["UI/src/routes/calendar/modals/AddCalendarEvent.svelte","let selectedCourse",147],
  ["UI/src/routes/calendar/modals/AddCalendarEvent.svelte",">Promemoria</label>",155],
  ["UI/src/routes/association/course/overview/OverviewCourse.svelte","async function syncGoogleCalendar()",45],
  ["UI/src/routes/association/course/overview/OverviewCourse.svelte","Calendario delle lezioni",112],
  ["UI/src/routes/association/course/overview/components/Calendar.svelte","async function saveCalendarDirectly(",44],
  ["UI/src/routes/association/course/overview/components/Calendar.svelte","Condividi calendario",97],
  ["UI/src/routes/calendar/SharedCalendar.svelte","<script>",160],
  ["BE/application/views/attendee_views.py","def calendar(request, uid):",53],
  ["BE/application/views/attendee_views.py","def full_events_calendar(request):",107],
  ["BE/application/views/attendee_views.py","def full_events_calendar_update(request):",78],
  ["BE/application/views/attendee_views.py","def full_events_calendar_export(request):",61],
  ["BE/application/utils/global_calendar.py","def mutate_events(request, existing):",84],
  ["BE/application/models/attendee_models.py","class GlobalCalendarEvents(models.Model):",36],
  ["UI/src/routes/profile/sections/Integrations.svelte","Connetti a Google",21],
  ["BE/application/views/google_views.py","def google_calendar_config(request):",104],
  ["BE/application/views/google_views.py","def google_calendar_export_course(request, course_id):",153],
  ["BE/application/urls.py","path(r'calendar/events'",5],
  ["BE/application/urls.py","path(r'camps-and-retreats/list'",18]
];
const sections = [
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "cos-e-un-camp-o-ritiro",
    "title": "Cos'è un Camp o Ritiro",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "creare-un-camp-o-ritiro",
    "title": "Creare un Camp o Ritiro",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "gestire-i-periodi",
    "title": "Gestire i periodi",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "aggiungere-un-periodo",
    "title": "Aggiungere un periodo",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "modificare-un-periodo",
    "title": "Modificare un periodo",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "gestire-i-servizi",
    "title": "Gestire i servizi",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "aggiungere-un-servizio",
    "title": "Aggiungere un servizio",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "modificare-o-eliminare-un-servizio",
    "title": "Modificare o eliminare un servizio",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "iscrivere-gli-atleti",
    "title": "Iscrivere gli atleti",
    "status": "pending",
    "reason": "Procedura locale non eseguita: occorre una prova dedicata con salvataggio e rilettura dei dati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "aggiungere-un-iscritto-manualmente",
    "title": "Aggiungere un iscritto manualmente",
    "status": "pending",
    "reason": "Procedura locale non eseguita: occorre una prova dedicata con salvataggio e rilettura dei dati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "modulo-di-iscrizione-pubblico",
    "title": "Modulo di iscrizione pubblico",
    "status": "pending",
    "reason": "Procedura locale non eseguita: occorre una prova dedicata con salvataggio e rilettura dei dati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "modificare-un-iscrizione",
    "title": "Modificare un'iscrizione",
    "status": "pending",
    "reason": "Procedura locale non eseguita: occorre una prova dedicata con salvataggio e rilettura dei dati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "rimuovere-un-iscrizione",
    "title": "Rimuovere un'iscrizione",
    "status": "pending",
    "reason": "Procedura locale non eseguita: occorre una prova dedicata con salvataggio e rilettura dei dati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "gestire-i-pagamenti",
    "title": "Gestire i pagamenti",
    "status": "pending",
    "reason": "Procedura locale non eseguita: occorre una prova dedicata con salvataggio e rilettura dei dati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/camp-e-ritiri.mdx",
    "id": "flusso-di-lavoro-consigliato",
    "title": "Flusso di lavoro consigliato",
    "status": "pending",
    "reason": "Procedura locale non eseguita: occorre una prova dedicata con salvataggio e rilettura dei dati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "il-calendario-generale",
    "title": "Il calendario generale",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "modalita-di-visualizzazione",
    "title": "Modalità di visualizzazione",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "vista-mese",
    "title": "Vista Mese",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "vista-settimana",
    "title": "Vista Settimana",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "vista-giorno",
    "title": "Vista Giorno",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "vista-lista",
    "title": "Vista Lista",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "navigare-nel-calendario",
    "title": "Navigare nel calendario",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "creare-un-evento-o-promemoria",
    "title": "Creare un evento o promemoria",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "modificare-o-eliminare-un-evento",
    "title": "Modificare o eliminare un evento",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "gestire-le-presenze-dal-calendario",
    "title": "Gestire le presenze dal calendario",
    "status": "pending",
    "reason": "Procedura locale non eseguita: occorre una prova dedicata con salvataggio e rilettura dei dati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "stampare-il-calendario",
    "title": "Stampare il calendario",
    "status": "pending",
    "reason": "Procedura locale non eseguita: occorre una prova dedicata con salvataggio e rilettura dei dati.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "esportare-il-calendario",
    "title": "Esportare il calendario",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "condivisione-del-calendario-con-gli-atleti",
    "title": "Condivisione del calendario con gli atleti",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "link-diretto",
    "title": "Link diretto",
    "status": "pending",
    "reason": "In attesa della prova reale completa.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "condivisione-tramite-whatsapp",
    "title": "Condivisione tramite WhatsApp",
    "status": "needs_external_verification",
    "reason": "Il controllo locale è visibile, ma l’operazione nel servizio esterno non è stata eseguita.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "condivisione-tramite-email",
    "title": "Condivisione tramite Email",
    "status": "needs_external_verification",
    "reason": "Il controllo locale è visibile, ma l’operazione nel servizio esterno non è stata eseguita.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "incorporare-il-calendario-nel-tuo-sito-web",
    "title": "Incorporare il calendario nel tuo sito web",
    "status": "needs_external_verification",
    "reason": "Il controllo locale è visibile, ma l’operazione nel servizio esterno non è stata eseguita.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "integrazione-con-google-calendar",
    "title": "Integrazione con Google Calendar",
    "status": "needs_external_verification",
    "reason": "Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "collegare-il-tuo-account-google",
    "title": "Collegare il tuo account Google",
    "status": "needs_external_verification",
    "reason": "Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "sincronizzare-un-corso-con-google-calendar",
    "title": "Sincronizzare un corso con Google Calendar",
    "status": "needs_external_verification",
    "reason": "Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "disconnettere-google-calendar",
    "title": "Disconnettere Google Calendar",
    "status": "needs_external_verification",
    "reason": "Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "docs/calendario.mdx",
    "id": "risorse-correlate",
    "title": "Risorse correlate",
    "status": "pending",
    "reason": "Rinvio alla guida Google: il collegamento editoriale non esegue una connessione esterna.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "faq/come-collegare-google-calendar.mdx",
    "id": "collegare-google-calendar",
    "title": "Collegare Google Calendar",
    "status": "needs_external_verification",
    "reason": "Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "faq/come-collegare-google-calendar.mdx",
    "id": "cosa-serve-per-iniziare",
    "title": "Cosa serve per iniziare?",
    "status": "needs_external_verification",
    "reason": "Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "faq/come-collegare-google-calendar.mdx",
    "id": "come-collegare-il-tuo-account-google",
    "title": "Come collegare il tuo account Google",
    "status": "needs_external_verification",
    "reason": "Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "faq/come-collegare-google-calendar.mdx",
    "id": "come-sincronizzare-un-corso",
    "title": "Come sincronizzare un corso",
    "status": "needs_external_verification",
    "reason": "Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "faq/come-collegare-google-calendar.mdx",
    "id": "cosa-viene-sincronizzato",
    "title": "Cosa viene sincronizzato",
    "status": "needs_external_verification",
    "reason": "Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  },
  {
    "path": "faq/come-collegare-google-calendar.mdx",
    "id": "come-disconnettere-google-calendar",
    "title": "Come disconnettere Google Calendar",
    "status": "needs_external_verification",
    "reason": "Collegamento OAuth, gestione dei token, esportazione e revoca presso Google richiedono una verifica esterna; nessun account è stato collegato.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48]
  }
];
export function campsCalendarEditorialContracts() {
    return editorialDescriptors("docs/manuale/camps-calendar-recipes.mjs", sections, contracts, reviewed);
}
