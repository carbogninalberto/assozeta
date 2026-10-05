// Reviewed implementation targets; this module never starts a browser.
const common = [
    ['UI/src/components/Sidebar.svelte', 'href="/#/profile"', 35],
    ['UI/src/routes.js', "'/connected-collaborators':", 46],
    ['UI/src/utils/Permissions.js', 'export const setPermissions', 58],
    ['UI/src/routes/profile/ProfileMenu.svelte', 'Informazioni Account', 63],
    ['UI/src/routes/profile/sections/Account.svelte', 'const updateAccountInformation', 107],
    ['BE/application/views/profile_views.py', 'def profile_update(request):', 138],
    ['BE/application/views/profile_views.py', 'def profile_info(request):', 49],
    ['BE/application/serializers/auth_serializers.py', 'class UserAuthUpdateSerializer(', 21],
    ['BE/application/models/user_models.py', 'class User(AbstractUser):', 139],
    ['BE/application/models/user_models.py', 'class SportAssociation(models.Model):', 121],
    ['BE/application/permissions_registry.py', "'profile/update':", 21],
];
export const organizationAccessSourceContracts = {
    'organization-settings': [...common,
        ['UI/src/routes/profile/sections/Account.svelte', '>Denominazione</label>', 176],
        ['UI/src/routes/profile/sections/Settings.svelte', 'async function updateSettings()', 69],
        ['UI/src/routes/profile/sections/Settings.svelte', '>Anno fiscale</label>', 332],
        ['BE/application/views/profile_views.py', 'def profile_settings(request):', 117],
        ['BE/application/serializers/auth_serializers.py', 'class UserSettingsSerializer(', 43],
        ['BE/application/utils/api_utils.py', 'def get_range_from_year_and_starting_date(', 62],
        ['BE/application/models/subscriptions_models.py', 'class Subscription(GroupModelMixin):', 190],
        ['BE/application/views/subscriptions_views.py', 'def subscription_list(request):', 75],
        ['BE/application/serializers/subscriptions_serializers.py', 'class SubscriptionFastOptimizedSerializer(', 162],
    ],
    'collaborator-permissions': [...common,
        ['UI/src/routes/connectedCollaborators/ConnectedCollaborators.svelte', 'const columns =', 109],
        ['UI/src/routes/connectedCollaborators/ConnectedCollaborators.svelte', 'id="addCollaboratorModal"', 91],
        ['UI/src/routes/connectedCollaborators/CollaboratorActions.svelte', 'function remove()', 77],
        ['UI/src/routes/connectedCollaborators/modals/EditModal.svelte', 'async function update()', 121],
        ['UI/src/components/PermissionsComponent.svelte', 'let collaboratorPermissionsMap =', 129],
        ['UI/src/components/PermissionsComponent.svelte', 'function updatePermissions(permissions)', 100],
        ['BE/application/views/collaborator_views.py', 'def collaborators_list(request):', 38],
        ['BE/application/views/collaborator_views.py', 'def collaborators_add(request):', 79],
        ['BE/application/views/collaborator_views.py', 'def collaborators_update(request, uid):', 48],
        ['BE/application/serializers/collaborators_serializers.py', 'class CollaboratorSerializer(', 36],
        ['BE/application/permissions_registry.py', 'def check_collaborator_permission(request):', 61],
        ['BE/instance/permissions.py', 'def is_instance_administrator(', 21],
    ],
};
export const organizationSettingsSources = [...new Set(organizationAccessSourceContracts['organization-settings'].map(([file]) => file))];
export const collaboratorPermissionsSources = [...new Set(organizationAccessSourceContracts['collaborator-permissions'].map(([file]) => file))];

// Refresh the current session from the actual authenticated profile before
// reloading. Actor initialization preserves hydrated sessions, so accumulating
// init scripts would replay older snapshots on every subsequent navigation.
export async function reloadOrganizationProfile({page, api, context, expect}) {
    const response = await api('profile/info');
    expect(response.status()).toBe(200);
    const data = (await response.json()).user_data;
    await page.evaluate(user => localStorage.setItem('userData', JSON.stringify(user)), data);
    await page.reload();
    return data;
}
