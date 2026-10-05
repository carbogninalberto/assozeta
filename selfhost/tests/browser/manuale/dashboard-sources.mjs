// Dependency import only; it never launches a browser or declares evidence run.
export const dashboardWorkflowSources = [
    'UI/src/routes.js', 'UI/src/routes/dashboard/Dashboard.svelte',
    'UI/src/routes/dashboard/EmptyDasbhoard.svelte', 'UI/src/components/modals/WidgetModal.svelte',
    'UI/src/utils/Permissions.js', 'UI/src/utils/userContext.js',
    'UI/src/components/widgets/Associates.svelte', 'UI/src/components/widgets/Payments.svelte',
    'UI/src/components/widgets/BestCourses.svelte', 'UI/src/components/widgets/Subscriptions.svelte',
    'UI/src/components/widgets/TodayLessons.svelte', 'UI/src/components/widgets/ExpiringCarnets.svelte',
    'UI/src/components/widgets/SubscriptionsToApprove.svelte',
    'UI/src/components/widgets/ExpiringMedicalCertificates.svelte',
    'UI/src/components/widgets/IncomeAndExpenses.svelte', 'UI/src/components/widgets/ExpiredPayments.svelte',
    'UI/src/components/widgets/ExpiredMedicalCertificates.svelte', 'UI/src/components/widgets/StaffBoard.svelte',
    'BE/application/views/statistic_views.py', 'BE/application/views/profile_views.py',
    'BE/application/serializers/auth_serializers.py', 'BE/application/models/user_models.py',
    'BE/application/models/subscriptions_models.py', 'BE/application/models/payment_models.py',
    'BE/application/models/courses_models.py', 'BE/application/models/carnet_models.py',
    'BE/application/utils/api_utils.py', 'BE/application/permissions_registry.py',
    'BE/application/impersonation.py', 'BE/core/middleware.py',
];
