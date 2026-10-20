// Publish the routed identity only after its tenant data and permissions are ready.
// Startup hooks may request the context concurrently; they share one load.
export function createUserContextLoader({fetchProfile, userData, permissions, role, setPermissions}) {
    let pending;
    return function loadUserContext() {
        if (pending) return pending;
        pending = (async () => {
            const profile = await fetchProfile();
            if (profile.error) return profile;
            const accountRole = profile.response.info.role;
            userData.set(profile.response.user_data);
            if (accountRole === 'association') {
                setPermissions(accountRole);
            } else {
                permissions.set([]);
            }
            role.set(accountRole);
            return profile;
        })().finally(() => { pending = null; });
        return pending;
    };
}
