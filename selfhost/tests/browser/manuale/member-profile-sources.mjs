export const memberProfileSources = [
    'UI/src/components/Sidebar.svelte', 'UI/src/routes.js', 'UI/src/utils/Permissions.js',
    'UI/src/routes/association/Members/MembersList.svelte',
    'UI/src/routes/association/Members/detail/DetailDrawer.svelte',
    'UI/src/routes/association/Members/detail/Detail.svelte',
    'UI/src/routes/association/Members/detail/sections/Info.svelte',
    'UI/src/components/drawer/basic-drawer.svelte',
    'BE/application/views/subscriptions_views.py', 'BE/application/services/subscription_service.py',
    'BE/application/serializers/subscriptions_serializers.py',
    'BE/application/models/subscriptions_models.py', 'BE/application/permissions_registry.py',
];
export const memberMedicalSources = [...memberProfileSources,
    'UI/src/routes/association/Members/detail/sections/Medical.svelte',
    'UI/src/routes/association/Members/detail/sections/modals/AddMedicalCertificate.svelte',
    'UI/src/routes/association/Members/detail/sections/modals/medicalCertificateUpload.js',
    'UI/src/components/formBuilder/preview-blocks/datepicker-input.svelte',
    'UI/src/components/inputs/DateInput.svelte', 'UI/src/shim/dropzone.js',
    'UI/src/utils/ApiMiddleware.js',
];

export async function openGiulia({page, open, expect}) {
    await open('Organizzazione', '/#/members/list');
    const row = page.locator('[data-row]').filter({hasText: 'Giulia Bianchi'});
    await expect(row).toBeVisible();
    await row.getByText('Giulia Bianchi', {exact: true}).first().click();
    const drawer = page.getByRole('dialog', {name: 'Giulia Bianchi', exact: true});
    await expect(drawer.locator('#subscription_form')).toBeVisible();
    return drawer;
}
