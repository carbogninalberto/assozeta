// Profile, association and mapped-owner table mutations commit with their audit indexes;
// any index exception rolls back the mutation. Locked reads and selected columns preserve
// concurrent changes. Successful settings and registration workflows remain unchanged.
// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "UI/src/routes.js": "955b24fdb6a39ee1e4a59b37bc94ca10d3245c03af469e10114e3c369d706a59",
  "UI/src/routes/dashboard/Dashboard.svelte": "d5cd8e48b8fb6f07c310735ae87169911c145daae237a5e84ec21d1876e41f9a",
  "UI/src/components/modals/WidgetModal.svelte": "49dcfaaf9fbb33e2b3503b9d6dd1bfdc64551a251fa97a9a32729638b1ea525a",
  "UI/src/components/widgets/StaffBoard.svelte": "1e22ec4c29067df662454be5e7acccc81388ae2717b86a0ab742818be0b6ffc0",
  "UI/src/utils/Permissions.js": "bf5966b4e5f67d53a7e59e84433e144bfeb5cbbea8a1842f13c10376f8405644",
  "BE/application/views/statistic_views.py": "9a97f4e8fac492aff785b6636165a4583510473d057616fd9f2f1e0a9b0693de",
  "BE/application/views/profile_views.py": "2aa3a223244222df909cb3a77eea10e4a93a5fea7f8b4533b977eb33d87d40d4",
  "BE/application/serializers/auth_serializers.py": "9285271db47ebe8dce875986175892991e868ff207f4d4f0d29fe69051aaf56a",
  "BE/application/models/user_models.py": "fa9e0b76b80640aedc0e070505c93a75ee5f8c6220a62c07d6fb2e047626c2f6",
  "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
  "BE/application/models/payment_models.py": "9dbcab8698adbde447a9c4f6eb52143409cf197fde3ce90d0aa8da56a1f8ebda",
  "BE/application/models/courses_models.py": "6a9cd0b923ab5b6a9dd120bcb7f81c1dac30914657fcd14b3fdb9e9c427d78c6",
  "BE/application/models/carnet_models.py": "3e3cb2cfb3d2be09cc5db6b97b43e2a2f391d91ccb61dfb38c99bf0ec7913713",
  "BE/application/utils/api_utils.py": "5db286fc73e92356b476514f4d6561a3300b36668ee99831ca1b197835a5cabf",
  "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
  "BE/application/impersonation.py": "04337e8ece4eb8027f396392835170e56ff176a06b2565b70c87455417b5035e",
  "UI/src/components/widgets/Associates.svelte": "5bb400b1d4716eaa0a8fb48186f328ecedddf2b0ab2aab66893ba426acf2eeab",
  "UI/src/components/widgets/Payments.svelte": "4e3e6fe698b9208613e9a2ee270294b601ab5a5b89abb7ba3a48c7eec86328b5",
  "UI/src/components/widgets/BestCourses.svelte": "345edb15c8c27c746c2bb2d88da65293d257d59f12a2761ea19d4549c2e0cdb0",
  "UI/src/components/widgets/Subscriptions.svelte": "275ee58e414aa192eb400c03fe47bb34c9e2c5eb7a2376f36e413f1f93c95b25",
  "UI/src/components/widgets/TodayLessons.svelte": "f326520f681e642ab0c8fceb7ca2df82169dfdfde8c1fae67da54c1a2ca6ea6e",
  "UI/src/components/widgets/ExpiringCarnets.svelte": "4bcea1d25d2ff49845207240e59b3bb768a9ea3a0f8683aca42cc0b06fe7e534",
  "UI/src/components/widgets/SubscriptionsToApprove.svelte": "643903b6af944a5e63ca9f12e5b449e637af6125e870ed3a6c7472c5501a8d8a",
  "UI/src/components/widgets/ExpiringMedicalCertificates.svelte": "92b85d8bcd047a6fd097096a0b455244540408feddaac6eaff318477ad5e0592",
  "UI/src/components/widgets/IncomeAndExpenses.svelte": "d7c5a167a21ce0ad992cf4ebd50db81165766b6e305cf1d4e6db352a2dce41de",
  "UI/src/components/widgets/ExpiredPayments.svelte": "c27713e3023614df7e997cf10f1b53d740cda7a3aa5af9d8a84d12311f2f552e",
  "UI/src/components/widgets/ExpiredMedicalCertificates.svelte": "9ef5000617081a07a048cc6a502b283fca7b860f6385404c6b3ee4ac21709615"
};
const contracts = [
  ["UI/src/routes.js","'/': wrap({",37],
  ["UI/src/routes/dashboard/Dashboard.svelte","let componentsMap = {",132],
  ["UI/src/routes/dashboard/Dashboard.svelte","use:dndzone={{",137],
  ["UI/src/components/modals/WidgetModal.svelte","async function addWidget()",114],
  ["UI/src/components/widgets/StaffBoard.svelte","const canRead =",78],
  ["UI/src/utils/Permissions.js","export const canPerformAction",32],
  ["BE/application/views/statistic_views.py","def statistic_dashboard_layout(request):",19],
  ["BE/application/views/profile_views.py","def profile_info(request):",45],
  ["BE/application/serializers/auth_serializers.py","'dashboard_layout',",4],
  ["BE/application/models/user_models.py","dashboard_layout =",2],
  ["BE/application/models/subscriptions_models.py","class Subscription(GroupModelMixin):",101],
  ["BE/application/models/payment_models.py","class PaymentManager(",91],
  ["BE/application/models/courses_models.py","class CourseManager(",74],
  ["BE/application/models/courses_models.py","class CourseSubscription(models.Model):",43],
  ["BE/application/models/carnet_models.py","class CarnetSubscription(models.Model):",20],
  ["BE/application/utils/api_utils.py","def get_range_from_year_and_starting_date(",67],
  ["BE/application/permissions_registry.py","'statistic/dashboard/layout'",2],
  ["BE/application/impersonation.py","def resolve_request_identity(",62],
  ["BE/application/impersonation.py","def acting_user(request):",3],
  ["UI/src/components/widgets/Associates.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","elif widget == 'associates':",100],
  ["UI/src/components/widgets/Payments.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","elif widget == 'payments':",100],
  ["UI/src/components/widgets/BestCourses.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","elif widget == 'bestcourses':",100],
  ["UI/src/components/widgets/Subscriptions.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","if widget == 'subscriptions':",100],
  ["UI/src/components/widgets/TodayLessons.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","elif widget == 'todaylessons':",100],
  ["UI/src/components/widgets/ExpiringCarnets.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","elif widget == 'expiringcarnets':",100],
  ["UI/src/components/widgets/SubscriptionsToApprove.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","elif widget == 'subscriptionstoapprove':",100],
  ["UI/src/components/widgets/ExpiringMedicalCertificates.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","elif widget == 'expiringmedicalcertificates':",100],
  ["UI/src/components/widgets/IncomeAndExpenses.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","elif widget == 'incomeAndExpenses':",100],
  ["UI/src/components/widgets/ExpiredPayments.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","elif widget == 'expiredPayments':",100],
  ["UI/src/components/widgets/ExpiredMedicalCertificates.svelte","<script>",250],
  ["BE/application/views/statistic_views.py","elif widget == 'expiredmedicalcertificates':",100]
];
const sections = [
  {
    "path": "docs/bacheca.mdx",
    "id": "introduzione",
    "title": "Introduzione",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "personalizzare-la-bacheca",
    "title": "Personalizzare la bacheca",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "riordinare-i-widget",
    "title": "Riordinare i widget",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "ridimensionare-i-widget",
    "title": "Ridimensionare i widget",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "aggiungere-un-widget",
    "title": "Aggiungere un widget",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "rimuovere-un-widget",
    "title": "Rimuovere un widget",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "ripristinare-il-layout-predefinito",
    "title": "Ripristinare il layout predefinito",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "widget-disponibili",
    "title": "Widget disponibili",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "soci",
    "title": "Soci",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "pagamenti-incassati",
    "title": "Pagamenti incassati",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,21,22]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "migliori-3-corsi-per-iscritti",
    "title": "Migliori 3 corsi per iscritti",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,23,24]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "iscrizioni-anno-corrente",
    "title": "Iscrizioni anno corrente",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,25,26]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "lezioni-di-oggi",
    "title": "Lezioni di oggi",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,27,28]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "carnet-in-esaurimento",
    "title": "Carnet in esaurimento",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,29,30]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "iscrizioni-da-approvare",
    "title": "Iscrizioni da approvare",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,31,32]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "certificati-medici-in-scadenza",
    "title": "Certificati medici in scadenza",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,33,34]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "entrate-e-uscite",
    "title": "Entrate e uscite",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,35,36]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "pagamenti-scaduti",
    "title": "Pagamenti scaduti",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,37,38]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "certificati-medici-scaduti",
    "title": "Certificati medici scaduti",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,39,40]
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "consigli-utili",
    "title": "Consigli utili",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18]
  }
];
export function dashboardEditorialContracts() {
    return editorialDescriptors("docs/manuale/dashboard-recipes.mjs", sections, contracts, reviewed);
}
