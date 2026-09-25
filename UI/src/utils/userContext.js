// Publish the routed identity only after its tenant data and permissions are ready.
// Startup hooks may request the context concurrently; they share one load.
export function createUserContextLoader({fetchProfile, fetchBilling, userData, billingData, permissions, role, setPermissions}) {
    let pending;
    return function loadUserContext() {
        if (pending) return pending;
        pending = (async () => {
            const profile = await fetchProfile();
            if (profile.error) return profile;
            const accountRole = profile.response.info.role;
            let billing;
            if (accountRole === 'association') {
                billing = await fetchBilling();
                if (billing.error) return billing;
            }
            userData.set(profile.response.user_data);
            if (billing) {
                billingData.set(billing.response.data);
                setPermissions(billing.response.data?.active_plan?.billing_type, accountRole);
            } else {
                billingData.set(null);
                permissions.set([]);
            }
            role.set(accountRole);
            return profile;
        })().finally(() => { pending = null; });
        return pending;
    };
}
