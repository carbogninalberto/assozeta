<script>
    import {
        sessionToken,
        notifications,
        refreshToken,
        expires,
        billingData,
        role,
        userData as userDataStore,
        unreadNotificationsCounter,
        preventBackHistoryUnsavedChanges,
    } from 'store/stores.js';
    import {instanceStatus, loadInstanceConfig, isSelfHostedMode, oemConfig, metaConfig} from 'store/instanceStore.js';
    import DashboardLayout from './layouts/DashboardLayout.svelte';
    import {slide} from 'svelte/transition';
    import {setPermissions} from 'utils/Permissions';
    import {onMount, afterUpdate, beforeUpdate} from 'svelte';
    import Router, {pop} from 'svelte-spa-router';
    import {push} from 'svelte-spa-router';
    import routes from './routes';
    import {apiFetch, originalFetch} from 'utils/ApiMiddleware.js';
    import {location} from 'svelte-spa-router';
    import UpdatesToast from 'components/notify/UpdatesToast.svelte';
    import Portal from 'svelte-portal';
    import {toast, Toaster} from 'svelte-sonner';
    import OnboardingChecklist from 'components/onboarding/onboarding-checklist.svelte';
    import notificationService from 'utils/NotificationService.js';
    import healthService from 'utils/HealthService.js';
    import SetupWizard from './routes/setup/SetupWizard.svelte';
    import AgentChatWidget from 'components/agent/AgentChatWidget.svelte';
    import LoadingOverlay from 'components/loading/LoadingOverlay.svelte';

    sessionToken.useLocalStorage();
    refreshToken.useLocalStorage();
    expires.useLocalStorage();
    userDataStore.useLocalStorage();

    let isLoaded = false;
    let offline = false;
    let ga;
    let refreshingToken = false;
    let isSwitchedUser = false;
    let isLoadingUserData = false;
    let showTestimonial = false;

    // Self-hosted instance configuration state
    let instanceLoading = true;
    let instanceConfigured = true; // Default to true for non-self-hosted mode

    // Dark mode - system preference listener
    let prefersDarkMedia;

    function applyTheme(isDark) {
        if (isDark) {
            document.documentElement.setAttribute('data-theme', 'dark');
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
    }

    function handleSystemPreferenceChange(e) {
        // Only apply system preference if user hasn't set a preference
        if ($userDataStore.dark_mode === undefined || $userDataStore.dark_mode === null) {
            applyTheme(e.matches);
        }
    }

    // Reactive statement for user preference (only when logged in)
    $: {
        if (!$sessionToken || $sessionToken === 'null') {
            // Not logged in - force light mode
            applyTheme(false);
        } else if ($userDataStore.dark_mode !== undefined && $userDataStore.dark_mode !== null) {
            // User has explicitly set a preference
            applyTheme($userDataStore.dark_mode);
        } else if (typeof window !== 'undefined' && window.matchMedia) {
            // Fall back to system preference
            applyTheme(window.matchMedia('(prefers-color-scheme: dark)').matches);
        }
    }

    window.goBack = function (delta) {
        [...Array(delta)].map(async x => {
            console.info('going back', delta);
            // console.info('popping');
            // history.back();
            setTimeout(() => {
                console.info('popping');
                pop();
            }, 30);
        });
    };

    onMount(async () => {
        // Set up system preference listener for dark mode
        if (typeof window !== 'undefined' && window.matchMedia) {
            prefersDarkMedia = window.matchMedia('(prefers-color-scheme: dark)');
            prefersDarkMedia.addEventListener('change', handleSystemPreferenceChange);
        }

        // Check if running in self-hosted mode and load instance config
        if (isSelfHostedMode()) {
            instanceLoading = true;
            instanceConfigured = await loadInstanceConfig();
            instanceLoading = false;

            // If not configured, show setup wizard (don't continue with app initialization)
            if (!instanceConfigured) {
                return;
            }
        } else {
            instanceLoading = false;
            instanceConfigured = true;
        }

        if ($sessionToken && $userDataStore?.requires_welcome && $role != 'athlete' && $location != '/welcome') {
            // $userDataStore.requires_welcome = false;
            push('/welcome');
        }
        // load cookie consent - use runtime config if available, fallback to compile-time
        const cookieConsentEnabled = $oemConfig?.displaySettings?.general?.cookieConsent;
        if (cookieConsentEnabled) {
            window._iub = window._iub || [];
            window._iub.csConfiguration = {
                askConsentAtCookiePolicyUpdate: true,
                consentOnContinuedBrowsing: false,
                countryDetection: true,
                gdprAppliesGlobally: false,
                invalidateConsentWithoutLog: true,
                perPurposeConsent: true,
                siteId: 2671111,
                whitelabel: false,
                cookiePolicyId: 19465245,
                lang: 'it',
                banner: {
                    acceptButtonCaptionColor: '#FFFFFF',
                    acceptButtonColor: '#0073CE',
                    acceptButtonDisplay: true,
                    backgroundColor: '#FFFFFF',
                    brandBackgroundColor: '#FFFFFF',
                    brandTextColor: '#000000',
                    closeButtonRejects: true,
                    customizeButtonCaptionColor: '#4D4D4D',
                    customizeButtonColor: '#DADADA',
                    customizeButtonDisplay: true,
                    explicitWithdrawal: true,
                    fontSize: '12px',
                    listPurposes: true,
                    position: 'float-bottom-right',
                    textColor: '#000000',
                },
            };
        }
        // set the title - use runtime config if available, fallback to compile-time
        const currentMetaConfig = $metaConfig ?? $oemConfig?.meta ?? {};
        document.title = currentMetaConfig?.title || $oemConfig?.name || 'assozeta';
        // set description
        document.description = currentMetaConfig?.description || 'assozeta';
        // apple mobile web app capable
        document
            .querySelector('meta[name="apple-mobile-web-app-capable"]')
            ?.setAttribute('content', currentMetaConfig?.appleMobileWebAppCapable || 'yes');
        // apple mobile web app status bar style
        document
            .querySelector('meta[name="apple-mobile-web-app-status-bar-style"]')
            ?.setAttribute('content', currentMetaConfig?.appleMobileWebAppStatusBarStyle || 'black');
        // apple mobile web app title
        document
            .querySelector('meta[name="apple-mobile-web-app-title"]')
            ?.setAttribute(
                'content',
                currentMetaConfig?.appleMobileWebAppTitle || $oemConfig?.name || 'assozeta'
            );
        // manifest - use dynamic manifest for self-hosted
        const manifestUrl =
            isSelfHostedMode() && instanceConfigured
                ? __bakney.env.API.INSTANCE.MANIFEST
                : currentMetaConfig?.manifest || '/manifest.json';
        document.querySelector('link[rel="manifest"]')?.setAttribute('href', manifestUrl);

        if (localStorage.getItem('sessionToken') == 'null' || localStorage.getItem('sessionToken') == null) {
            localStorage.clear();
            if (
                !$location.includes('/stripe/payment/done') &&
                !$location.includes('/stripe/pay/') &&
                !$location.includes('/stripe/cart-pay') &&
                !$location.includes('/subscribe/') &&
                !$location.includes('/subscribe-family/') &&
                !$location.includes('/subscribe-multiple/') &&
                !$location.includes('/forms/') &&
                !$location.includes('/invite') &&
                !$location.includes('/reset') &&
                !$location.includes('shared-calendar') &&
                !$location.includes('/card')
            )
                push('/login');
        }

        // Initialize WebSocket services for real-time updates
        if (
            $sessionToken &&
            !$location.includes('/stripe/payment/done') &&
            !$location.includes('/stripe/pay') &&
            !$location.includes('/stripe/cart-pay')
        ) {
            // Initialize notification WebSocket (pass token for auth fallback)
            notificationService.init(notifications, unreadNotificationsCounter, $sessionToken);
        }

        // Initialize health WebSocket
        healthService.init(
            health => {
                offline = false;
            },
            () => {
                offline = true;
            }
        );

        document.addEventListener('click', () => {
            document.querySelectorAll('.popover').forEach(popover => popover.remove());
            document.querySelectorAll('.tooltip').forEach(popover => popover.remove());
        });
        await checkPermissions();
        setInterval(checkPermissions, 5000);

        if (
            sessionStorage.getItem('redirectAfterLogin') == 1 &&
            $role == 'athlete' &&
            $sessionToken != null &&
            $sessionToken != 'undefined' &&
            $sessionToken != 'null' &&
            $sessionToken != '' &&
            $sessionToken != '""'
        ) {
            let username = sessionStorage.getItem('sportAssociationUsername');
            push(`/search/profile/${username}/open`);
        }

        window.addEventListener('showTestimonial', () => {
            if ($oemConfig?.displaySettings?.navbar?.showReview) showTestimonial = true;
        });
    });

    function showTestimonialModal() {
        // read again from localStorage
        let userData = JSON.parse(localStorage.getItem('userData'));
        // get or create showTestimonial localstorage key
        let showTestimonialStorage = localStorage.getItem('showTestimonial') == 'true' ? true : false;
        let snoozeTestimonial = localStorage.getItem('snoozeTestimonial') == 'true' ? true : false;
        let shouldShow = Math.random() < 0.15;
        // check if testimonial
        // console.info(userData);
        if (
            !showTestimonialStorage &&
            !snoozeTestimonial &&
            $role == 'association' &&
            !userData?.sport_association?.reviewed &&
            userData?.sport_association?.reviewed != null &&
            shouldShow
        ) {
            setTimeout(() => {
                // read snoozeTestimonial again
                if (localStorage.getItem('snoozeTestimonial') == 'true') return;
                // check if sessionToken is still valid
                if (
                    $sessionToken == null ||
                    $sessionToken == 'null' ||
                    $sessionToken == 'undefined' ||
                    $sessionToken == ''
                )
                    return;
                showTestimonial = true;
            }, 5000);
        }
    }

    beforeUpdate(() => {
        if ($sessionToken && $userDataStore?.requires_welcome && $role != 'athlete' && $location != '/welcome') {
            // $userDataStore.requires_welcome = false;
            push('/welcome');
        }
    });

    afterUpdate(() => {
        if ($location != '/error') {
            checkUserData();
        }
        if ($sessionToken != null && $location != '/login' && $location != '/error') {
            showTestimonialModal();
        }
        // if ($location != '/login' && $location != '/error') checkExpired();

        // check if isSwitchedUser
        if (localStorage.getItem('switched_superuser') == 'true' && localStorage.getItem('USER_ID') != null) {
            isSwitchedUser = true;
        }
    });

    function checkUserData() {
        // Prevent concurrent API calls
        if (isLoadingUserData) return;

        let userDataStr = localStorage.getItem('userData');
        let parsedUserData = null;

        // Safely parse userData
        try {
            parsedUserData = userDataStr && userDataStr !== '{}' ? JSON.parse(userDataStr) : null;
        } catch (e) {
            parsedUserData = null;
        }

        if ($sessionToken) {
            // Valid data exists for current user - skip reload
            if (parsedUserData?.user_id) {
                // Not in switched mode, data is valid
                if (!isSwitchedUser) return;
                // In switched mode, check if data matches the switched user
                if (String(parsedUserData.user_id) === String(localStorage.getItem('USER_ID'))) return;
            }

            // Need to fetch user data
            isLoadingUserData = true;
            apiFetch(__bakney.env.API.PROFILE.INFO).then(res => {
                isLoadingUserData = false;
                if (!res.error) {
                    userDataStore.set(res.response.user_data);
                }
            });
            return;
        }
        if ($location == '/reset') return;
        if (
            $location == '/login' ||
            userDataStr == 'null' ||
            userDataStr == 'undefined' ||
            userDataStr == null ||
            userDataStr == undefined
        ) {
            localStorage.clear();
            if (
                !$location.includes('/stripe/payment/done') &&
                !$location.includes('/stripe/pay/') &&
                !$location.includes('/stripe/cart-pay') &&
                !$location.includes('/subscribe/') &&
                !$location.includes('/subscribe-family/') &&
                !$location.includes('/subscribe-multiple/') &&
                !$location.includes('/forms/') &&
                !$location.includes('/invite') &&
                !$location.includes('shared-calendar') &&
                !$location.includes('/card')
            )
                push('/login');
        }
    }

    async function checkPermissions() {
        let currentPage = localStorage.getItem('currentPage');
        if (
            currentPage &&
            !window.location.href.includes('/stripe') &&
            currentPage != 'login' &&
            $role != 'association' &&
            $role != 'athlete' &&
            $sessionToken != null
        ) {
            console.warn('checking role...', currentPage, $role);
            await apiFetch(__bakney.env.API.BILLING.ACTIVE_PLAN).then(async billingResult => {
                if (!billingResult.error) {
                    billingData.set(billingResult.response.data);
                    await apiFetch(__bakney.env.API.PROFILE.INFO).then(profileResult => {
                        if (!profileResult.error) {
                            const currentRole = profileResult.response.info.role;
                            role.set(currentRole);
                            setPermissions(billingResult.response.data?.active_plan?.billing_type, currentRole);
                        }
                    });
                } else {
                    apiFetch(__bakney.env.API.PROFILE.INFO).then(res => {
                        if (!res.error && res.response.info.role != $role) {
                            role.set(res.response.info.role);
                        }
                    });
                }
            });
        }
    }

    export async function checkExpired() {
        // guard to avoid check when refreshing token
        if (refreshingToken) return;
        if ($sessionToken == null) return;
        if ($location == '/login') return;

        refreshingToken = true;
        let dateTime = new Date();
        let expiringTime = localStorage.getItem('expires') || Date.now() - 100;
        if ($sessionToken && expiringTime && dateTime.getTime() > expiringTime) {
            let res = await apiFetch(__bakney.env.API.OAUTH2.REFRESH_TOKEN, {
                method: 'POST',
                body: JSON.stringify({refresh_token: $refreshToken}),
            });
            let response = res.response;
            if (!res.error) {
                localStorage.setItem('sessionToken', `"${response?.access_token}"`);
                localStorage.setItem('refreshToken', `"${response?.refresh_token}"`);
                localStorage.setItem('expires', Date.now() + parseInt(response.expires_in) * 1000);
            } else {
                localStorage.clear();
                if (
                    !$location.includes('/stripe/payment/done') &&
                    !$location.includes('/stripe/pay') &&
                    !$location.includes('/stripe/cart-pay') &&
                    !$location.includes('/subscribe/') &&
                    !$location.includes('/subscribe-family/') &&
                    !$location.includes('/subscribe-multiple/') &&
                    !$location.includes('/forms/') &&
                    !$location.includes('/invite') &&
                    !$location.includes('/shared-calendar')
                )
                    push('/login');
            }
            refreshingToken = false;
        } else {
            refreshingToken = false;
        }
    }

    // getNotifications is now handled by NotificationService WebSocket
    // See: src/utils/NotificationService.js
</script>

<!-- Show loading state while checking instance configuration -->
{#if instanceLoading}
    <div class="instance-loading d-flex flex-column align-items-center justify-content-center min-vh-100">
        <div class="spinner-border text-primary mb-3" role="status">
            <span class="sr-only">Caricamento...</span>
        </div>
        <p class="text-muted">Caricamento configurazione...</p>
    </div>
    <!-- Show setup wizard if instance is not configured (self-hosted mode) -->
{:else if !instanceConfigured}
    <SetupWizard />
    <!-- Normal app flow -->
{:else if JSON.parse(localStorage.getItem('sessionToken')) !== null && !$location.includes('/card') && !$location.includes('/attendance-scanner-mode') && !$location.includes('/welcome') && !$location.includes('/forms/') && !$location.includes('/email-builder') && !$location.includes('/subscribe/') && !$location.includes('/subscribe-family/') && !$location.includes('/subscribe-multiple/') && !$location.includes('/invite') && !$location.includes('/reset') && !$location.includes('/shared-calendar') && !$location.includes('/stripe/cart-pay') && $location != '/error' && $location != '/login' && localStorage.getItem('sessionToken') != null && localStorage.getItem('currentPage') != 'login' && sessionStorage.getItem('inconsistencies') == null}
    <DashboardLayout routerComponent={Router} {routes} />
    {#if $userDataStore?.onboarding && !($userDataStore?.onboarding?.create_membership && $userDataStore?.onboarding?.view_membership && $userDataStore?.onboarding?.approve_payment && $userDataStore?.onboarding?.download_invoice && $userDataStore?.onboarding?.view_collaborators && $userDataStore?.onboarding?.view_settings)}
        <OnboardingChecklist />
    {/if}
    {#if $role === 'association'}
        <AgentChatWidget />
    {/if}
{:else}
    <Router
        {routes}
        on:routeLoading={() => (isLoaded = false)}
        on:routeLoaded={() => (isLoaded = true)}
        on:conditionsFailed={() => {
            // go to /404
            push('/404');
        }} />
{/if}

{#if offline}
    <div transition:slide={{duration: 350, y: 5}} class="connection-status">Connessione persa, sei offline...</div>
{/if}

{#if isSwitchedUser}
    <Portal target="#portal-elements">
        <div
            transition:slide={{duration: 350, y: 5}}
            class="rounded-lg"
            style="margin-top: 0.5rem; left: 0.5rem; outline: .2rem solid #b463ff;position:fixed;width:40rem;max-width:96vw;display: flex; justify-content: space-between; background: blueviolet; color: #fff; font-weight: 800; padding: 0.25rem 1rem!important; font-size: 10px; box-shadow: 0 0rem 3rem 0rem #00000070;">
            <!-- svelte-ignore a11y-click-events-have-key-events -->
            Sei in modalità superuser, attenzione!
            <!-- svelte-ignore a11y-missing-attribute -->
            <span
                on:click={() => {
                    // Disconnect WebSockets before switching back
                    notificationService.disconnect();
                    healthService.disconnect();

                    // clear all local storage keys except for sessionToken
                    Object.keys(localStorage).forEach(key => {
                        if (key !== 'sessionToken' && key !== 'refreshToken' && key !== 'expires')
                            localStorage.removeItem(key);
                    });

                    // Reset stores to clear in-memory state
                    userDataStore.set({});
                    role.set(null);
                    billingData.set({});
                    isSwitchedUser = false;

                    // Full page reload to ensure clean state
                    window.location.href = '/#/tools/sport-associations-manager';
                    window.location.reload();
                }}
                class="ml-3"
                style="cursor:pointer"><u>esci da questa modalità</u></span>
        </div>
    </Portal>
{/if}

<UpdatesToast />
<LoadingOverlay />

<Toaster
    position="bottom-center"
    expand={false}
    richColors={true}
    offset="20px"
    toastOptions={{
        class: 'shadow-none border-2 rounded-lg mb-0',
        duration: 3000,
    }} />

<svelte:head>
    <!-- Using the variable dynamically in the href attribute -->
    <link rel="apple-touch-icon" href={$metaConfig?.appleTouchIcon || $oemConfig?.meta?.appleTouchIcon} />
</svelte:head>

<style>
    .instance-loading {
        background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%);
    }

    .connection-status {
        position: fixed;
        z-index: 3331017;
        width: 100vw;
        height: 1rem;
        align-items: center;
        display: flex;
        justify-content: center;
        background: #f3293c;
        font-weight: bold;
        padding: 0.5rem;
        color: #fff;
        top: 0;
        font-size: 0.8rem;
        pointer-events: none;
    }

    :global([data-sonner-toaster][data-theme='light']) {
        /* Normal toast */
        --normal-bg: var(--white);
        --normal-border: var(--gray);
        --normal-text: var(--dark);

        /* Success toast */
        --success-bg: color-mix(in srgb, var(--success) 15%, var(--white)) !important;
        --success-border: color-mix(in srgb, var(--success) 23%, var(--white)) !important;
        --success-text: color-mix(in srgb, var(--success) 95%, var(--dark)) !important;

        /* Info toast */
        --info-bg: color-mix(in srgb, var(--info) 10%, var(--white));
        --info-border: var(--info);
        --info-text: color-mix(in srgb, var(--info) 80%, var(--dark));

        /* Warning toast */
        --warning-bg: color-mix(in srgb, var(--warning) 10%, var(--white));
        --warning-border: var(--warning);
        --warning-text: color-mix(in srgb, var(--warning) 80%, var(--dark));

        /* Error toast */
        --error-bg: color-mix(in srgb, var(--danger) 10%, var(--white));
        --error-border: var(--danger);
        --error-text: color-mix(in srgb, var(--danger) 80%, var(--dark));
    }

    :global(.ec .ec-today.ec-button) {
        background-color: var(--ec-button-bg-color) !important;
        border: 1px solid var(--ec-button-border-color) !important;
        border-radius: 0.65rem !important;
        color: var(--ec-button-text-color) !important;
        padding: 0.375rem 0.75rem !important;
        cursor: pointer;
    }
    :global(.ec .ec-today.ec-button:hover) {
        color: var(--primary) !important;
    }
</style>
