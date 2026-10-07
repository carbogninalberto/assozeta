// Source dependencies only; importing this module never launches a browser.
export const memberCreationSources = [
    'UI/src/components/Sidebar.svelte', 'UI/src/routes.js', 'UI/src/utils/Permissions.js',
    'UI/src/utils/enumUtils.js', 'UI/src/store/stores.js',
    'UI/src/routes/association/Members/MembersList.svelte',
    'UI/src/routes/association/Members/add/AddMember.svelte',
    ...[1, 2, 3, 4, 6].map(number => `UI/src/routes/association/Members/add/sections/Section${number}.svelte`),
    'UI/src/components/signature/SmoothSignature.svelte',
    'UI/src/components/buttons/GenerateTaxCodeButton.svelte',
    'BE/application/views/subscriptions_views.py', 'BE/application/utils/subscriptions_utils.py',
    'BE/application/serializers/user_serializers.py', 'BE/application/serializers/subscriptions_serializers.py',
    'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py',
    'BE/application/models/payment_models.py', 'BE/application/permissions_registry.py',
];

export const memberApprovalSources = [...memberCreationSources,
    'UI/src/routes/association/Members/MembersBook.svelte',
    'UI/src/components/buttons/ApproveButton.svelte',
    'UI/src/routes/accounting/payment/PaymentList.svelte',
    'BE/application/views/payment_views.py', 'BE/application/signals.py',
];
