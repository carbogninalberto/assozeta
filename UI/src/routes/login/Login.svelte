<script>
    import {Capacitor} from '@capacitor/core';
    import {Eye, EyeOff} from 'lucide-svelte';
    import * as jose from 'jose';
    import {fade, scale} from 'svelte/transition';
    import {querystring} from 'svelte-spa-router';
    import {setPermissions} from 'utils/Permissions';
    import {
        refreshToken,
        sessionToken,
        expires,
        role,
        currentPage,
        userData,
        tablesSettings,
        billingData,
    } from 'store/stores.js';
    import Section2 from './sections/Section2.svelte';
    import {getBase64FromUrl} from 'utils/Functions.js';
    import {onMount} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware';
	import { UiApp, UiUtil } from 'shim/ui.js';
    import {oauthConfig, oemConfig} from 'store/instanceStore.js';
    // import {ga} from '@beyonk/svelte-google-analytics';
    // import ApiMiddleware from 'utils/ApiMiddelware.js';
    sessionToken.useLocalStorage();
    refreshToken.useLocalStorage();
    expires.useLocalStorage();
    role.useLocalStorage();
    currentPage.useLocalStorage();
    userData.useLocalStorage();
    billingData.useLocalStorage();
    tablesSettings.useLocalStorage();

    let selectedAccountType;
    let agreeCheckbox = false;
    let ageCheckbox = false;

    let currentShown = 1;

    let userInfo = {
        first_name: null,
        last_name: null,
        username: null,
        email: null,
        password: null,
        sport_association: false,
        denomination: null,
        tax_code: null,
    };

    let email;

    let loginInfo = {
        username: null,
        password: null,
    };

    let singleSignOnData = {
        onboarding: false,
        token: null,
        backend: null,
        email: null,
        name: null,
        username: null,
        picture: null,
    };

    let validationForgot;
    let validationLogin;
    let validationSignUp;
    let validationAssociationSignUp;
    let brandLogo;
    let privacyPolicyUrl;
    let termsAndConditionsUrl;

    let testimonials = [
        {
            denomination: 'A.S.D. VILLAGE GYM',
            score: 4,
            text: 'Ottimo servizio, sempre disponibili ad assisterci nelle varie esigenze in tempi brevi.',
            image: '/static/images/testimonials/asd-village-gym.jpeg',
        },
        {
            denomination: 'A.S.D ARCIERI ROCCA DI SAN QUIRICO 1983',
            score: 5,
            text: 'Programma molto semplice perfetto per la nostra associazione, i ragazzi che lavorano sul software sono molto disponibile e competenti.',
            image: '/static/images/testimonials/arcieri-san-quirico.jpeg',
        },
        {
            denomination: 'PLANET PADEL ASD',
            score: 5,
            text: 'Ottimo gestionale Soci. Ragazzi molto disponibili.',
            image: '/static/images/testimonials/planet-padel.jpeg',
        },
    ];

    function getRandomTestimonial() {
        return testimonials[0];
    }

    $: brandLogo = $oemConfig?.logo || '';
    $: privacyPolicyUrl = $oemConfig?.privacyPolicyUrl || '';
    $: termsAndConditionsUrl =
        $oemConfig?.displaySettings?.login?.termsAndConditionsUrl || $oemConfig?.termsOfServiceUrl || '';

    onMount(() => {
        var urlParams;
        localStorage.clear();
        (window.onpopstate = function () {
            var match,
                pl = /\+/g, // Regex for replacing addition symbol with a space
                search = /([^&=]+)=?([^&]*)/g,
                decode = function (s) {
                    return decodeURIComponent(s.replace(pl, ' '));
                };

            urlParams = {};
            while ((match = search.exec($querystring))) urlParams[decode(match[1])] = decode(match[2]);

            if (urlParams.page) {
                if (urlParams.page == 'signup') {
                    currentShown = 2;
                } else if (urlParams.page == 'login') {
                    currentShown = 1;
                } else if (urlParams.page == 'forgot') {
                    currentShown = 3;
                    email = urlParams?.email_reset || null;
                } else if (urlParams.page == 'signup_athlete') {
                    currentShown = 4;
                } else if (urlParams.page == 'signup_association') {
                    currentShown = 5;
                }
            }

            if (urlParams.type) {
                if (urlParams.type == 'association') {
                    selectedAccountType = 'signup_association';
                }
            }

            if (urlParams.email) {
                userInfo.email = urlParams.email;
                let partialOnBoarding = JSON.parse(localStorage.getItem('partialOnBoarding')) || {};
                partialOnBoarding.email = urlParams.email;
                localStorage.setItem('partialOnBoarding', JSON.stringify(partialOnBoarding));
                apiFetch(__bakney.env.API.OAUTH2.PARTIAL_SIGNUP, {
                    method: 'POST',
                    body: JSON.stringify({
                        email: urlParams.email,
                    }),
                });
            }

            if (urlParams.billing) {
                if (urlParams.type == 'association') {
                    selectedAccountType = 'signup_association';
                }
            }
        })();
        setTimeout(initSignleSingOn, 500);
    });

    function gtag_report_conversion() {
        // var callback = function () {
        //     if (typeof url != 'undefined') {
        //         window.location = url;
        //     }
        // };
        // gtag('event', 'conversion', {
        //     send_to: 'AW-985878500/JH0rCL2-mpQYEOSfjdYD',
        //     value: 1.0,
        //     currency: 'EUR',
        //     event_callback: callback,
        // });

        // var callback = function () {
        //     if (typeof url != 'undefined') {
        //         window.location = url;
        //     }
        // };

        // gtag('event', 'conversion', {
        //     send_to: 'AW-985878500/rum0COXDyr8ZEOSfjdYD',
        //     value: 299.0,
        //     currency: 'EUR',
        //     event_callback: callback,
        // });
        return false;
    }

    function resetData() {
        userInfo = {
            user_id: null,
            first_name: null,
            last_name: null,
            username: null,
            email: null,
            password: null,
            sport_association: false,
            denomination: null,
            tax_code: null,
        };

        loginInfo = {
            username: null,
            password: null,
        };
        email = null;
    }

    // toggle view of pages
    let toggleView = id => {
        switch (id) {
            case 'login':
                currentShown = 1;
                resetData();
                setTimeout(() => {
                    initSignleSingOn();
                }, 600);
                break;
            case 'signup':
                // gtag_report_conversion();
                if ($oemConfig?.displaySettings?.login?.allowOnlyAthletes) {
                    currentShown = currentShown == 4 ? 1 : 4;
                } else {
                    currentShown = 2;
                }
                resetData();
                break;
            case 'reset':
                currentShown = 3;
                resetData();
                break;
            case 'signup_athlete':
                currentShown = 4;
                break;
            case 'signup_association':
                currentShown = 5;
                break;
            default:
                currentShown = 1;
                break;
        }
    };

    function initSignleSingOn() {
        if (!$oemConfig?.displaySettings?.login?.allowOauthLogin || !$oauthConfig?.googleClientId) return;
        google.accounts.id.initialize({
            client_id: $oauthConfig.googleClientId,
            callback: data => {
                // console.info('data', data);
                singleSignOn(data.credential, 'google-oauth2');
            },
        });
        google.accounts.id.renderButton(
            document.getElementById('login-with-google'),
            {theme: 'outline', size: 'large'} // customization attributes
        );
        google.accounts.id.prompt(); // also display the One Tap dialog

        const state = Math.random().toString(36);
        const nonce = Math.random().toString(36);

        // LOGIN WITH APPLE
        // TODO: support the callback via URL and not via callback
        // Apple login can be enabled when the runtime OAuth configuration includes an Apple client id.
    }

    const handleLoginValidation = function () {
        validationLogin?.destroy();

        // Init form validation rules. For more info check the FormValidation plugin's official documentation:https://formvalidation.io/
        validationLogin = FormValidation.formValidation(UiUtil.getById('bkn_login_signin_form'), {
            fields: {
                username_login: {
                    validators: {
                        notEmpty: {
                            message: 'Email obbligatoria',
                        },
                    },
                },
                password_login: {
                    validators: {
                        notEmpty: {
                            message: 'Password obbligatoria',
                        },
                    },
                },
            },
            plugins: {
                trigger: new FormValidation.plugins.Trigger(),
                submitButton: new FormValidation.plugins.SubmitButton(),
                //defaultSubmit: new FormValidation.plugins.DefaultSubmit(), // Uncomment this line to enable normal button submit after form validation
                bootstrap: new FormValidation.plugins.Bootstrap(),
            },
        });

        validationLogin.validate().then(function (status) {
            if (status == 'Valid') {
                login();
            } else {
                swal.fire({
                    text: 'Per favore, inserisci tutti i dati e riprova.',
                    icon: 'error',
                    buttonsStyling: false,
                    confirmButtonText: 'Ok, capito!',
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-light-primary',
                    },
                }).then(function () {
                    UiUtil.scrollTop();
                });
            }
        });
    };

    var handleSignUpForm = function (e) {
        validationSignUp?.destroy();

        // Init form validation rules. For more info check the FormValidation plugin's official documentation:https://formvalidation.io/
        validationSignUp = FormValidation.formValidation(UiUtil.getById('bkn_athlete_signup_form'), {
            fields: {
                nome: {
                    validators: {
                        notEmpty: {
                            message: 'Nome obbligatorio',
                        },
                    },
                },
                cognome: {
                    validators: {
                        notEmpty: {
                            message: 'Cognome obbligatorio',
                        },
                    },
                },
                username: {
                    validators: {
                        notEmpty: {
                            message: 'Username obbligatorio',
                        },
                        regexp: {
                            regexp: /^[\w.+-]+$/m,
                            message: 'Lo username può contenere solo lettere, numeri, punti e trattini bassi',
                        },
                        stringLength: {
                            max: 150,
                            message: 'Lo username non può superare i 150 caratteri',
                        },
                        remote: {
                            message: 'Lo username è già stato utilizzato.',
                            method: 'GET',
                            url: __bakney.env.API.OAUTH2.CHECK.USERNAME,
                        },
                    },
                },
                email: {
                    validators: {
                        notEmpty: {
                            message: 'Email obbligatoria',
                        },
                        emailAddress: {
                            message: 'Non è un indirizzo email valido',
                        },
                        stringLength: {
                            max: 255,
                            message: "L'email non può superare i 255 caratteri",
                        },
                        remote: {
                            message: "L'indirizzo email è già stato utilizzato per creare un utente.",
                            method: 'GET',
                            url: __bakney.env.API.OAUTH2.CHECK.EMAIL,
                        },
                    },
                },
                password: {
                    validators: {
                        regexp: {
                            regexp: /^(?=.*[A-Z])(?=.*[!@#$&\.\-\_*])(?=.*[0-9]).{10,}$/m,
                            message:
                                'La password deve contenere almeno:<br>- 1 lettera maiuscola<br>- 1 carattere speciale !@#$&-_*.<br>- 1 numero<br>- avere più di 10 caratteri',
                        },
                    },
                },
                agree: {
                    validators: {
                        notEmpty: {
                            message: 'Devi accettare i termini e condizioni',
                        },
                    },
                },
                age: {
                    validators: {
                        notEmpty: {
                            message: 'Devi essere maggiorenne per poterti registrare',
                        },
                    },
                },
            },
            plugins: {
                trigger: new FormValidation.plugins.Trigger(),
                bootstrap: new FormValidation.plugins.Bootstrap(),
            },
        });

        validationSignUp.validate().then(function (status) {
            if (status == 'Valid') {
                signup();
            } else {
                swal.fire({
                    text: 'Scusa, ho individuato degli errori, per favore riprova.',
                    icon: 'error',
                    buttonsStyling: false,
                    confirmButtonText: 'Ok, capito!',
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-light-primary',
                    },
                });
            }
        });
    };

    var handleAssociationSignUpForm = function (e) {
        // gtag_report_conversion();

        validationAssociationSignUp?.destroy();

        // Init form validation rules. For more info check the FormValidation plugin's official documentation:https://formvalidation.io/
        validationAssociationSignUp = FormValidation.formValidation(
            UiUtil.getById('bkn_login_association_signup_form'),
            {
                fields: {
                    nome: {
                        validators: {
                            notEmpty: {
                                message: 'Nome obbligatorio',
                            },
                        },
                    },
                    cognome: {
                        validators: {
                            notEmpty: {
                                message: 'Cognome obbligatorio',
                            },
                        },
                    },
                    username: {
                        validators: {
                            notEmpty: {
                                message: 'Username obbligatorio',
                            },
                            regexp: {
                                regexp: /^[\w.+-]+$/m,
                                message: 'Lo username può contenere solo lettere, numeri, punti e trattini bassi',
                            },
                            stringLength: {
                                max: 150,
                                message: 'Lo username non può superare i 150 caratteri',
                            },
                            remote: {
                                message: 'Lo username è già stato utilizzato.',
                                method: 'GET',
                                url: __bakney.env.API.OAUTH2.CHECK.USERNAME,
                            },
                        },
                    },
                    email: {
                        validators: {
                            notEmpty: {
                                message: 'Email obbligatoria',
                            },
                            emailAddress: {
                                message: 'Non è un indirizzo email valido',
                            },
                            stringLength: {
                                max: 255,
                                message: "L'email non può superare i 255 caratteri",
                            },
                            remote: {
                                message: "L'indirizzo email è già stato utilizzato per creare un utente.",
                                method: 'GET',
                                url: __bakney.env.API.OAUTH2.CHECK.EMAIL,
                            },
                        },
                    },
                    password: {
                        validators: {
                            regexp: {
                                regexp: /^(?=.*[A-Z])(?=.*[!@#$&\.\-\_*])(?=.*[0-9]).{10,}$/m,
                                message:
                                    'La password deve contenere almeno:<br>- 1 lettera maiuscola<br>- 1 carattere speciale !@#$&-_*.<br>- 1 numero<br>- avere più di 10 caratteri',
                            },
                        },
                    },
                    denomination: {
                        validators: {
                            notEmpty: {
                                message: 'Nome Associazione obbligatorio',
                            },
                        },
                    },
                    tax_code: {
                        validators: {
                            notEmpty: {
                                message: 'Codice Fiscale/P.IVA obbligatorio/a',
                            },
                            stringLength: {
                                max: 11,
                                message:
                                    'Codice Fiscale/P.IVA non può superare le 11 cifre. Controlla di non aver inserito spazi',
                            },
                        },
                    },
                    agree: {
                        validators: {
                            notEmpty: {
                                message: 'Devi accettare i termini e condizioni',
                            },
                        },
                    },
                    age: {
                        validators: {
                            notEmpty: {
                                message: 'Devi essere maggiorenne per poterti registrare',
                            },
                        },
                    },
                },
                plugins: {
                    trigger: new FormValidation.plugins.Trigger(),
                    bootstrap: new FormValidation.plugins.Bootstrap(),
                },
            }
        );

        validationAssociationSignUp.validate().then(function (status) {
            if (status == 'Valid') {
                userInfo.sport_association = true;
                signup();
            } else {
                swal.fire({
                    text: 'Scusa, ho individuato degli errori, per favore riprova.',
                    icon: 'error',
                    buttonsStyling: false,
                    confirmButtonText: 'Ok, capito!',
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-light-primary',
                    },
                }).then(function () {
                    UiUtil.scrollTop();
                });
            }
        });
    };

    async function signup(data = {}) {
        localStorage.clear();
        UiApp.blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Registrazione in corso...',
        });

        let body = {
            response: null,
            user_id: String(userInfo.user_id),
            first_name: String(userInfo.first_name).trim().toUpperCase(),
            last_name: String(userInfo.last_name).trim().toUpperCase(),
            username: String(userInfo.username).trim().toUpperCase().replace(/\s/g, ''), // clean from trailing & heading spaces
            email: String(userInfo.email).trim().toUpperCase(),
            password: userInfo.password,
            sport_association: userInfo.sport_association,
            denomination: String(userInfo.denomination || '')
                .trim()
                .toUpperCase(),
            tax_code: String(userInfo.tax_code || '')
                .trim()
                .toUpperCase(),
        };

        const signupMethod = data?.token && data?.backend ? data.backend : 'credentials';
        const accountType = userInfo.sport_association ? 'association' : 'athlete';

        if (data?.token && data?.backend) {
            (body.response = data?.response), (body.token = data?.token);
            body.backend = data?.backend;
            body.username = String(data.username).trim().toLowerCase();
            body.email = String(data.email).trim().toLowerCase();
            body.first_name = String(data.name).split(' ')[0].trim().toLowerCase();
            body.last_name = String(data.name).split(' ')[1].trim().toLowerCase();
        }

        if (sessionStorage.getItem('collaboratorToken')) {
            body.collaboratorToken = sessionStorage.getItem('collaboratorToken');
        }

        const url = __bakney.env.API.OAUTH2.SIGNUP;
        const res = await window.fetch(url, {
            method: 'POST',
            headers: {
                Accept: 'application/json',
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(body),
        });

        const response = await res.json();
        let result = JSON.stringify(response);

        // spinner stop
        UiApp.unblockPage();

        if (res.status == 200) {
            swal.fire({
                text: 'Iscrizione avvenuta con successo!',
                icon: 'success',
                buttonsStyling: false,
                confirmButtonText: 'Continua',
                customClass: {
                    confirmButton: 'btn font-weight-bold btn-light-primary',
                },
            }).then(async function () {
                let collaboratorToken = sessionStorage.getItem('collaboratorToken');
                let collaborator = collaboratorToken && collaboratorToken.length > 0 ? true : false;
                sessionStorage.removeItem('collaboratorToken');
                sessionToken.set(response.access_token);
                refreshToken.set(response.refresh_token);
                expires.set(Date.now() + parseInt(response.expires_in) * 1000);
                role.set(response.role);
                userData.set(response.user_data);
                tablesSettings.set(response.tables_settings);
                currentPage.set('dashboard');

                await apiFetch(__bakney.env.API.BILLING.ACTIVE_PLAN).then(async res => {
                    if (!res.error) {
                        billingData.set(res.response.data);
                        await apiFetch(__bakney.env.API.PROFILE.INFO).then(res => {
                            if (!res.error) {
                                role.set(res.response.info.role);
                                setPermissions($billingData?.active_plan?.billing_type, $role);
                            }
                        });
                    } else {
                        apiFetch(__bakney.env.API.PROFILE.INFO).then(res => {
                            if (!res.error && res.response.info.role != $role) {
                                role.set(res.response.info.role);
                            }
                        });
                    }

                    if (!collaborator && response.role != 'athlete' && location.href != '/welcome')
                        location.href = '/#/welcome';
                    else location.href = '/';
                });
                // location.replace('/');
                // replace('/');
            });
        } else {
            // TODO: manage specifically the error

            swal.fire({
                text: 'Scusa, ho individuato degli errori, controlla di non aver già utilizzato questa email e riprova.',
                icon: 'error',
                buttonsStyling: false,
                confirmButtonText: 'Ok, capito!',
                customClass: {
                    confirmButton: 'btn font-weight-bold btn-light-primary',
                },
            }).then(function () {
                UiUtil.scrollTop();
            });
        }
    }

    async function reset() {
        localStorage.clear();
        validationForgot?.destroy();

        // Init form validation rules. For more info check the FormValidation plugin's official documentation:https://formvalidation.io/
        validationForgot = FormValidation.formValidation(UiUtil.getById('bkn_login_forgot_form'), {
            fields: {
                email_forgot: {
                    validators: {
                        notEmpty: {
                            message: 'Email obbligatoria',
                        },
                        emailAddress: {
                            message: 'Non è un indirizzo email valido',
                        },
                    },
                },
            },
            plugins: {
                trigger: new FormValidation.plugins.Trigger(),
                bootstrap: new FormValidation.plugins.Bootstrap(),
            },
        });

        validationForgot.validate().then(async function (status) {
            if (status == 'Valid') {
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Inoltro richiesta...',
                });

                const url = __bakney.env.API.OAUTH2.RESET;
                const res = await window.fetch(url, {
                    method: 'POST',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        email: email,
                    }),
                });

                const response = await res.json();
                let result = JSON.stringify(response);

                // spinner stop
                UiApp.unblockPage();

            swal.fire({
                    text: "Se l'email che hai inserito è corretta riceverai un'email!",
                    icon: 'info',
                    buttonsStyling: false,
                    confirmButtonText: 'Ok, capito!',
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-light-primary',
                    },
                }).then(function () {
                    UiUtil.scrollTop();
                    toggleView('login');
                });
            } else {
                swal.fire({
                    text: 'Scusa, ho individuato degli errori, per favore riprova.',
                    icon: 'error',
                    buttonsStyling: false,
                    confirmButtonText: 'Ok, capito!',
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-light-primary',
                    },
                });
            }
        });
    }

    async function handlePostLoginOperations(response, loginMethod = 'credentials') {
    }

    async function login(data = {otp: null, backend: null, token: null, username: null}) {
        localStorage.clear();
        // extracting data from the object parameter
        const {otp, backend, token, username} = data;

        // clear local storage
        localStorage.clear();

        UiApp.blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Accesso in corso...',
        });

        let payload = {};
        if (backend && token && username) {
            payload = {
                backend: backend,
                username: username,
                token: token,
            };
        } else {
            payload = {
                username: String(loginInfo.username).trim(),
                password: loginInfo.password,
            };
        }

        if (otp) payload['otp'] = otp;

        const url = __bakney.env.API.OAUTH2.LOGIN;
        const res = await window.fetch(url, {
            method: 'POST',
            headers: {
                Accept: 'application/json',
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });

        const response = await res.json();
        let result = JSON.stringify(response);

        // spinner stop
        UiApp.unblockPage();

        if (res.status == 200) {
            const loginMethod = backend ? backend : 'credentials';
            await handlePostLoginOperations(response, loginMethod);
            /*
            swal.fire({
                text: "Credenziali corrette!",
                icon: "success",
                buttonsStyling: false,
                confirmButtonText: "Ok, capito!",
                customClass: {
                    confirmButton: "btn font-weight-bold btn-light-primary"
                }
            }).then(function() { */
            sessionToken.set(response.access_token);
            refreshToken.set(response.refresh_token);
            expires.set(Date.now() + parseInt(response.expires_in) * 1000); // conversion to UNIX epoch
            role.set(response.role);
            userData.set(response.user_data);
            tablesSettings.set(response.tables_settings);
            currentPage.set('dashboard');
            // location.replace('/');

            // before redirect check if need to do stripe onboarding or show onboarded page
            let check_onboarding = JSON.parse(sessionStorage.getItem('check_onboarding') || false);
            let check_onboarded = JSON.parse(sessionStorage.getItem('check_onboarded') || false);
            sessionStorage.removeItem('check_onboarding');
            sessionStorage.removeItem('check_onboarded');

            // must check inconsistencies for associate tutors
            let redirectToUpdateTutors = false;
            // let resInco = await apiFetch(__bakney.env.API.CHECK_INCONSISTENCIES, {method: 'GET'});
            // if (!resInco.error && resInco.response.inconsistencies) {
            //     redirectToUpdateTutors = true;
            //     sessionStorage.setItem('inconsistencies', JSON.stringify(resInco.response.missing_tutors));
            // }
            setTimeout(async () => {
                if (redirectToUpdateTutors) {
                    location.href = '/#/update-tutors';
                } else if (check_onboarding && !check_onboarded) {
                    location.href = '/#/stripe/onboarding';
                } else if (check_onboarded && !check_onboarding) {
                    location.href = '/#/stripe/onboarded';
                } else {
                    if ($role == 'association')
                        await apiFetch(__bakney.env.API.BILLING.ACTIVE_PLAN).then(res => {
                            if (!res.error) {
                                billingData.set(res.response.data);
                                setPermissions($billingData?.active_plan?.billing_type, $role);
                            }
                        });
                    location.href = '/';
                }
            }, 1000);
            //});
        } else if (res.status == 401 && response.msg == 'OTP code required.') {
            const {value: OTPCode} = await Swal.fire({
                title: 'Autenticazione a Due Fattori',
                input: 'text',
                inputLabel: 'Inserisci il codice OTP.',
                showCancelButton: false,
                inputValidator: value => {
                    if (!value || String(value || '').length != 6 || !/^\d+$/.test(value)) {
                        return 'Devi inserire un codice OTP Valido';
                    }
                },
            });

            if (OTPCode) {
                data.otp = OTPCode;
                login(data);
            }
        } else {
            swal.fire({
                text: 'Scusa, ho individuato degli errori, riprova.',
                icon: 'error',
                buttonsStyling: false,
                confirmButtonText: 'Ok, capito!',
                customClass: {
                    confirmButton: 'btn font-weight-bold btn-light-primary',
                },
            }).then(function () {
                UiUtil.scrollTop();
            });
        }
    }

    const togglePasswordInput = (id, type) => {
        if (type == 'loginPassword') {
            loginInfo.password_visibility = !loginInfo.password_visibility;
            document.getElementById(id).type = loginInfo.password_visibility ? 'text' : 'password';
        } else if (type == 'userPassword') {
            userInfo.password_visibility = !userInfo.password_visibility;
            document.getElementById(id).type = userInfo.password_visibility ? 'text' : 'password';
        }
    };

    async function singleSignOn(e, backend = 'facebook') {
        localStorage.clear();
        // alert("ok " + backend)
        if (backend === 'facebook') {
            console.warn('Not implemented yet');
            // FB.getLoginStatus(function (response) {
            //     FB.login(
            //         function (response) {
            //             if (response.authResponse) {
            //                 FB.api('/me?fields=email,name,picture', async function (responseInfo) {
            //                     const res = await apiFetch(
            //                         `${__bakney.env.API.OAUTH2.CHECK.EMAIL}?email=${responseInfo.email}`
            //                     );
            //                     if (!res?.response?.valid && res?.response?.exception === 'email already taken.') {
            //                         login({
            //                             backend: backend,
            //                             token: response.authResponse.accessToken,
            //                             email: responseInfo.email,
            //                         });
            //                     } else if (res?.response?.valid && res?.response?.data?.msg === 'email is available.') {
            //                         singleSignOnData.onboarding = true;
            //                         singleSignOnData.backend = backend;
            //                         singleSignOnData.token = response.authResponse.accessToken;
            //                         singleSignOnData.email = responseInfo.email;
            //                         singleSignOnData.name = responseInfo.name;
            //                         singleSignOnData.username = responseInfo.name.replace(/\s/g, '').toLowerCase();
            //                         singleSignOnData.picture = getBase64FromUrl(responseInfo.picture.data.url);
            //                         currentShown = 2;
            //                     }

            //                 });
            //             } else {
            //                 console.warn('User cancelled login or did not fully authorize.');
            //             }
            //         },
            //         {scope: 'public_profile,email', return_scopes: true}
            //     );
            // });
        } else if (backend === 'google-oauth2') {
            const responseInfo = jose.decodeJwt(e);
            const res = await apiFetch(`${__bakney.env.API.OAUTH2.CHECK.EMAIL}?email=${responseInfo.email}`);
            if (!res?.response?.valid && res?.response?.exception === 'email already taken.') {
                login({
                    backend: backend,
                    token: e,
                    username: responseInfo.email,
                });
            } else {
                singleSignOnData.response = responseInfo;
                singleSignOnData.onboarding = true;
                singleSignOnData.backend = backend;
                singleSignOnData.token = e;
                singleSignOnData.email = responseInfo.email;
                singleSignOnData.name = responseInfo.name;
                singleSignOnData.username = responseInfo.name.replace(/\s/g, '').toLowerCase();
                singleSignOnData.picture = getBase64FromUrl(responseInfo.picture);
                currentShown = 2;
            }
        } else {
            console.warn(`[WARNING] Backend "${backend}" is not recognized`);
        }
    }
</script>

{#if $currentPage == 'login'}
    <div class="d-flex flex-column flex-root">
        <!--begin::Login-->
        <div
            class="login login-1 login-signin-on d-flex flex-column flex-lg-row flex-column-fluid bg-white"
            id="bkn_login">
            <!--begin::Content-->
            <div
                class="login-content flex-row-fluid d-flex flex-column justify-content-center position-relative overflow-hidden py-6 mx-auto"
                style=" max-width: 100%;">
                <!--begin::Content body-->
                <div class="row h-100 px-8 px-md-0">
                    <!-- <div class="col-12 col-sm-6"> -->
                    <div
                        class="col-12 {$oemConfig?.displaySettings?.login?.showTestimonials
                            ? 'col-md-6'
                            : ''} bg-white">
                        <div class="d-flex flex-column-fluid flex-center h-100 bg-white">
                            <!--begin::Signin-->

                            <div class="login-form login-signin form-40" class:no-display={currentShown != 1}>
                                <!--begin::Form-->
                                <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
                                <form
                                    class="form"
                                    novalidate="novalidate"
                                    id="bkn_login_signin_form"
                                    autocomplete="off"
                                    on:keyup={e => e.key === 'Enter' && handleLoginValidation()}>
                                    {#if currentShown == 1}
                                        <div in:scale={{duration: 100, start: 0.95}}>
                                            <div class="pb-13 pt-lg-0 pt-1 text-center">
                                                <img id="logo" class="h-40px" src={brandLogo} alt="logo" />
                                            </div>

                                            <!--begin::Title-->
                                            <div class="pb-13 pt-lg-0 pt-5 text-center">
                                                <h3 class="font-weight-bolder text-dark font-size-h4 font-size-h1-lg">
                                                    Benvenuto su {$oemConfig?.name || 'assozeta'}.
                                                </h3>
                                                <span class="text-muted font-weight-bold font-size-h4"
                                                    >Sei nuovo qui?
                                                    <!-- svelte-ignore a11y-invalid-attribute -->
                                                    <a
                                                        href="javascript:;"
                                                        on:click|preventDefault={() => {
                                                            toggleView('signup');
                                                        }}
                                                        id="bkn_login_signup"
                                                        class="text-primary font-weight-bolder">Crea un Account</a
                                                    ></span>
                                            </div>
                                            <!--begin::Form group-->
                                            <div class="form-group">
                                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                                <label class="font-size-h6 font-weight-bolder text-dark"
                                                    >Email / Username</label>
                                                <input
                                                    tabindex="0"
                                                    bind:value={loginInfo.username}
                                                    class="form-control form-control-solid h-auto p-6 rounded-lg"
                                                    type="text"
                                                    name="username_login"
                                                    autocomplete="off"
                                                    placeholder="Inserisci email / username" />
                                            </div>
                                            <!--end::Form group-->
                                            <!--begin::Form group-->
                                            <div class="form-group">
                                                <div class="d-flex justify-content-between mt-n5">
                                                    <!-- svelte-ignore a11y-label-has-associated-control -->
                                                    <label class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                        >Password</label>
                                                    <a
                                                        tabindex="-1"
                                                        href="/"
                                                        class="text-primary font-size-h6 font-weight-bolder text-hover-primary pt-5"
                                                        id="bkn_login_forgot"
                                                        on:click|preventDefault={() => toggleView('reset')}
                                                        >Password Dimenticata ?</a>
                                                </div>
                                                <div class="input-group input-group-solid">
                                                    <input
                                                        tabindex="0"
                                                        name="password_login"
                                                        id="login-password"
                                                        placeholder="Inserisci la password..."
                                                        autocomplete="off"
                                                        class="form-control form-control-solid h-auto p-6 rounded-lg"
                                                        type="password"
                                                        bind:value={loginInfo.password} />
                                                    <div
                                                        class="input-group-append"
                                                        style="cursor:pointer"
                                                        on:click={() =>
                                                            togglePasswordInput('login-password', 'loginPassword')}>
                                                        <span class="input-group-text">
                                                            {#if loginInfo.password_visibility}
                                                                <EyeOff size={16} />
                                                            {:else}
                                                                <Eye size={16} />
                                                            {/if}
                                                        </span>
                                                    </div>
                                                </div>
                                                <!-- <input bind:value={loginInfo.password} class="form-control form-control-solid h-auto p-6 rounded-lg" type="password" name="password" autocomplete="off" /> -->
                                            </div>
                                            <!--end::Form group-->
                                            <!--begin::Action-->
                                            <div class="pb-lg-0 pb-5">
                                                <button
                                                    type="button"
                                                    id="bkn_login_signin_submit"
                                                    class="btn btn-primary font-weight-bolder font-size-h6 px-8 py-4 w-100 my-3 mr-3"
                                                    on:click|preventDefault={() => handleLoginValidation()}>
                                                    Accedi
                                                </button>
                                            </div>
                                            {#if $oemConfig?.displaySettings?.login?.allowOauthLogin && $oauthConfig?.googleClientId && !Capacitor.isNative}
                                                <!--end::Action-->
                                                <!-- {#if __bakney.env.DEPLOY_ENV != 'production'} -->
                                                <div
                                                    class="py-4 px-4 d-flex justify-content-center text-dark-75 font-weight-bolder font-size-h6">
                                                    oppure
                                                </div>
                                                <!--begin::Title-->
                                                <div
                                                    class="form-group d-flex align-items-center justify-content-center text-muted font-weight-bold" style="gap: 10px;">
                                                    <!-- LOGIN WITH FACEBOOK -->
                                                    <!-- btn-hover-transparent-primary  -->
                                                    <!-- <button
                                                        type="button"
                                                        class="btn btn-light-primary mr-0 mr-md-2 font-weight-bolder px-8 py-4 my-3 font-weight-bolder font-size-h6 d-flex align-items-center justify-content-center w-100"
                                                        on:click|preventDefault={e => singleSignOn(e, 'facebook')}>
                                                        <span class="svg-icon svg-icon-md">
                                                            <img
                                                                src="/static/f_logo_RGB-Blue_72.png"
                                                                width="20"
                                                                height="20" />
                                                        </span>Accedi con Facebook
                                                    </button> -->
                                                    <!-- LOGIN WITH GOOGLE -->
                                                    <div id="login-with-google" />
                                                    <!-- LOGIN WITH APPLE -->
                                                     <!-- TODO: support the callback via URL and not via callback -->
                                                    <!-- <div id="appleid-signin" style="height: 3.5rem;" data-color="white" data-border="true" data-type="sign in"></div> -->
                                                </div>
                                            {/if}
                                            <!-- {/if} -->
                                        </div>
                                    {/if}
                                </form>
                                <!--end::Form-->
                            </div>
                            <!--end::Signin-->
                            <!--begin::Signup Athlete-->
                            <div class="login-form login-signup form-40" class:no-display={currentShown != 2}>
                                <!--begin::Form-->
                                <div>
                                    <div class="pb-13 pt-lg-0 pt-1 text-center">
                                        <img id="logo" class="h-40px" src={brandLogo} alt="logo" />
                                    </div>
                                    {#if currentShown == 2}
                                        <Section2 bind:selectedAccountType />
                                    {/if}
                                    <!--end::Form group-->
                                    <!--begin::Form group-->
                                    <div class="form-group d-flex flex-wrap pb-lg-0 pb-3 mt-15">
                                        <button
                                            type="button"
                                            id="bkn_login_signup_cancel"
                                            class="btn btn-sm btn-light-primary font-weight-bolder font-size-sm px-8 py-4 my-3 mr-4"
                                            on:click|preventDefault={() => toggleView('login')}>Indietro</button>
                                        <button
                                            type="button"
                                            class="btn btn-sm btn-primary font-weight-bolder font-size-sm px-8 py-4 my-3"
                                            on:click|preventDefault={() => {
                                                sessionStorage.removeItem('collaboratorToken');
                                                toggleView(selectedAccountType);
                                            }}>
                                            Crea un'{selectedAccountType == 'signup_association'
                                                ? 'associazione'
                                                : 'atleta'}
                                        </button>
                                    </div>
                                    <!--end::Form group-->
                                </div>
                                <!--end::Form-->
                            </div>
                            <!--end::Signup Athlete-->
                            <!--begin::Signup Athlete-->

                            <div class="login-form login-signup form-50" class:no-display={currentShown != 4}>
                                <!--begin::Form-->
                                <form
                                    class="form"
                                    novalidate="novalidate"
                                    id="bkn_athlete_signup_form"
                                    autocomplete="off">
                                    {#if currentShown == 4}
                                        <div in:scale={{duration: 100, start: 0.95}}>
                                            <div class="pt-lg-0 pt-1 pb-5">
                                                <img id="logo" class="h-40px" src={brandLogo} alt="logo" />
                                            </div>

                                            {#if singleSignOnData.onboarding}
                                                <div class="pt-lg-0 pt-5 pb-5">
                                                    <h3
                                                        class="font-weight-bolder text-dark font-size-h1 font-size-h1-lg">
                                                        Crea un account {sessionStorage.getItem('collaboratorToken')
                                                            ? 'collaboratore'
                                                            : 'atleta'}
                                                    </h3>
                                                    <p class="text-muted font-weight-bold font-size-sm">
                                                        Stai creando un account per <b>{singleSignOnData.name}</b> accetta
                                                        i termini e condizioni.
                                                    </p>
                                                </div>
                                            {:else}
                                                <!--begin::Title-->
                                                <div class="pt-lg-0 pt-5 pb-5">
                                                    <h3
                                                        class="font-weight-bolder text-dark font-size-h1 font-size-h1-lg">
                                                        Crea un account {sessionStorage.getItem('collaboratorToken')
                                                            ? 'collaboratore'
                                                            : 'atleta'}
                                                    </h3>
                                                    <p class="text-muted font-weight-bold font-size-sm">
                                                        Compila il modulo con i tuoi dati per creare un account.
                                                    </p>
                                                </div>
                                                <!--end::Title-->
                                                <h3 class="font-weight-bolder font-size-h3 font-size-h3-lg">
                                                    Informazioni Personali
                                                </h3>
                                                <div class="row p-0 m-0">
                                                    <!--begin::Form group-->
                                                    <div class="form-group col-12 col-lg-6 pl-0 m-0">
                                                        <div class="d-flex justify-content-between mt-n5">
                                                            <!-- svelte-ignore a11y-label-has-associated-control -->
                                                            <label
                                                                class="font-size-h6 font-weight-bolder text-dark-75 pt-5"
                                                                >Nome</label>
                                                        </div>
                                                        <input
                                                            bind:value={userInfo.first_name}
                                                            class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                            type="text"
                                                            placeholder="Nome"
                                                            name="nome"
                                                            autocomplete="off" />
                                                    </div>
                                                    <!--end::Form group-->
                                                    <!--begin::Form group-->
                                                    <div class="form-group col-12 col-lg-6 pl-0 m-0">
                                                        <div class="d-flex justify-content-between mt-n5">
                                                            <!-- svelte-ignore a11y-label-has-associated-control -->
                                                            <label
                                                                class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                                >Cognome</label>
                                                        </div>
                                                        <input
                                                            bind:value={userInfo.last_name}
                                                            class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                            type="text"
                                                            placeholder="Cognome"
                                                            name="cognome"
                                                            autocomplete="off" />
                                                    </div>
                                                    <!--end::Form group-->
                                                </div>
                                                <h3 class="font-weight-bolder font-size-h3 font-size-h3-lg mt-10">
                                                    Account
                                                </h3>
                                                <div class="row p-0 m-0">
                                                    <!--begin::Form group-->
                                                    <div class="form-group col-12 col-lg-6 mb-5 pl-0">
                                                        <div class="d-flex justify-content-between mt-n5">
                                                            <!-- svelte-ignore a11y-label-has-associated-control -->
                                                            <label
                                                                class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                                >Username</label>
                                                        </div>
                                                        <input
                                                            bind:value={userInfo.username}
                                                            class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                            type="text"
                                                            placeholder="Username"
                                                            name="username"
                                                            autocomplete="off" />
                                                    </div>
                                                    <!--end::Form group-->
                                                    <!--begin::Form group-->
                                                    <div class="form-group col-12 col-lg-6 mb-5 pl-0">
                                                        <div class="d-flex justify-content-between mt-n5">
                                                            <!-- svelte-ignore a11y-label-has-associated-control -->
                                                            <label
                                                                class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                                >Email</label>
                                                        </div>
                                                        <input
                                                            bind:value={userInfo.email}
                                                            class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                            type="email"
                                                            placeholder="Email"
                                                            name="email"
                                                            autocomplete="off" />
                                                    </div>
                                                    <!--end::Form group-->
                                                </div>
                                                <!--begin::Form group-->
                                                <div class="form-group pr-5">
                                                    <div class="d-flex justify-content-between mt-n5">
                                                        <!-- svelte-ignore a11y-label-has-associated-control -->
                                                        <label class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                            >Password</label>
                                                    </div>
                                                    <div class="input-group input-group-solid">
                                                        <input
                                                            bind:value={userInfo.password}
                                                            class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                            type="password"
                                                            placeholder="Password"
                                                            name="password"
                                                            autocomplete="off"
                                                            id="signin-password-athlete" />
                                                        <div
                                                            class="input-group-append"
                                                            style="cursor:pointer"
                                                            on:click={() =>
                                                                togglePasswordInput(
                                                                    'signin-password-athlete',
                                                                    'userPassword'
                                                                )}>
                                                            <span class="input-group-text">
                                                                {#if userInfo.password_visibility}
                                                                    <EyeOff size={16} />
                                                                {:else}
                                                                    <Eye size={16} />
                                                                {/if}
                                                            </span>
                                                        </div>
                                                    </div>
                                                </div>
                                            {/if}
                                            <!--end::Form group-->
                                            <!--begin::Form group-->
                                            <div class="form-group align-items-center">
                                                <label class="checkbox mb-0" style="font-size: 1rem !important;">
                                                    <input type="checkbox" name="agree" bind:checked={agreeCheckbox} />
                                                    <span />
                                                    <div class="pl-2">
                                                        Accetto i
                                                        {#if termsAndConditionsUrl}
                                                            <a
                                                                class="ml-1"
                                                                href={termsAndConditionsUrl}
                                                                target="_blank"
                                                                rel="noopener noreferrer">termini e condizioni</a>
                                                        {:else}
                                                            <span class="ml-1">termini e condizioni del servizio</span>
                                                        {/if}
                                                    </div>
                                                </label>
                                            </div>
                                            <div class="form-group align-items-center">
                                                <label class="checkbox mb-0" style="font-size: 1rem !important;">
                                                    <input type="checkbox" name="age" bind:checked={ageCheckbox} />
                                                    <span />
                                                    <div class="pl-2">Dichiario di aver compiuto 18 anni</div>
                                                </label>
                                            </div>

                                            <!--end::Form group-->
                                            <!--begin::Form group-->
                                            <div class="form-group d-flex flex-wrap pb-lg-0 pb-3">
                                                {#if !sessionStorage.getItem('collaboratorToken')}
                                                    <button
                                                        type="button"
                                                        id="bkn_login_signup_cancel"
                                                        class="btn btn-sm btn-light-primary font-weight-bolder font-size-sm px-8 py-4 my-3 mr-4"
                                                        on:click|preventDefault={() => toggleView('signup')}
                                                        >Indietro</button>
                                                {/if}
                                                <button
                                                    type="button"
                                                    disabled={!agreeCheckbox && !ageCheckbox}
                                                    on:click|preventDefault={() => {
                                                        if (singleSignOnData.onboarding) {
                                                            signup({
                                                                backend: singleSignOnData.backend,
                                                                token: singleSignOnData.token,
                                                                email: singleSignOnData.email,
                                                                name: singleSignOnData.name,
                                                                username: singleSignOnData.username,
                                                                picture: singleSignOnData.picture,
                                                                response: singleSignOnData.response,
                                                                is_sport_association:
                                                                    selectedAccountType != 'signup_athlete',
                                                            });
                                                        } else {
                                                            handleSignUpForm();
                                                        }
                                                    }}
                                                    class="btn btn-sm btn-primary font-weight-bolder font-size-sm px-8 py-4 my-3"
                                                    >Conferma e Accedi</button>
                                            </div>
                                            <!--end::Form group-->
                                        </div>
                                    {/if}
                                </form>
                                <!--end::Form-->
                            </div>
                            <!--end::Signup Athlete-->
                            <!--end::Signup Athlete-->
                            <!--begin::Signup Sport Association-->
                            <div class="login-form login-signup form-50" class:no-display={currentShown != 5}>
                                <!--begin::Form-->
                                <form
                                    class="form"
                                    novalidate="novalidate"
                                    id="bkn_login_association_signup_form"
                                    autocomplete="off">
                                    {#if currentShown == 5}
                                        <div in:scale={{duration: 100, start: 0.95}}>
                                            <div class="pt-lg-0 pt-1 pb-5">
                                                <img id="logo" class="h-40px" src={brandLogo} alt="logo" />
                                            </div>
                                            <!--begin::Title-->
                                            <div class="pt-lg-0 pt-5 pb-5">
                                                <h3 class="font-weight-bolder text-dark font-size-h1 font-size-h1-lg">
                                                    Crea un account
                                                </h3>
                                                <p class="text-muted font-weight-bold font-size-sm">
                                                    Compila il modulo con i tuoi dati per creare un account
                                                    associazione.
                                                </p>
                                            </div>
                                            {#if !singleSignOnData.onboarding}
                                                <!--end::Title-->
                                                <h3 class="font-weight-bolder font-size-h3 font-size-h3-lg">
                                                    Informazioni associazione
                                                </h3>
                                                <div class="row p-0 m-0">
                                                    <!--begin::Form group-->
                                                    <div class="form-group col-12 col-lg-6 mb-5 pl-0">
                                                        <div class="d-flex justify-content-between mt-n5">
                                                            <!-- svelte-ignore a11y-label-has-associated-control -->
                                                            <label
                                                                class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                                >Nome</label>
                                                        </div>
                                                        <input
                                                            bind:value={userInfo.first_name}
                                                            class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                            type="text"
                                                            placeholder="Nome"
                                                            name="nome"
                                                            autocomplete="off" />
                                                    </div>
                                                    <!--end::Form group-->
                                                    <!--begin::Form group-->
                                                    <div class="form-group col-12 col-lg-6 mb-5 pl-0">
                                                        <div class="d-flex justify-content-between mt-n5">
                                                            <!-- svelte-ignore a11y-label-has-associated-control -->
                                                            <label
                                                                class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                                >Cognome</label>
                                                        </div>
                                                        <input
                                                            bind:value={userInfo.last_name}
                                                            class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                            type="text"
                                                            placeholder="Cognome"
                                                            name="cognome"
                                                            autocomplete="off" />
                                                    </div>
                                                    <!--end::Form group-->
                                                </div>
                                            {/if}
                                            <div class="row p-0 m-0">
                                                <!--begin::Form group-->
                                                <div class="form-group col-12 col-lg-6 mb-5 pl-0">
                                                    <div class="d-flex justify-content-between mt-n5">
                                                        <!-- svelte-ignore a11y-label-has-associated-control -->
                                                        <label class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                            >Nome Associazione Sportiva</label>
                                                    </div>
                                                    <input
                                                        bind:value={userInfo.denomination}
                                                        class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                        type="text"
                                                        placeholder="Nome Associazione Sportiva"
                                                        name="denomination"
                                                        autocomplete="off" />
                                                </div>
                                                <!--end::Form group-->
                                                <!--begin::Form group-->
                                                <div class="form-group col-12 col-lg-6 mb-5 pl-0">
                                                    <div class="d-flex justify-content-between mt-n5">
                                                        <!-- svelte-ignore a11y-label-has-associated-control -->
                                                        <label class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                            >Codice Fiscale / P.IVA</label>
                                                    </div>
                                                    <input
                                                        bind:value={userInfo.tax_code}
                                                        class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                        type="text"
                                                        placeholder="Codice Fiscale/P.IVA"
                                                        name="tax_code"
                                                        autocomplete="off" />
                                                </div>
                                                <!--end::Form group-->
                                            </div>
                                            {#if !singleSignOnData.onboarding}
                                                <h3 class="font-weight-bolder font-size-h3 font-size-h3-lg">Account</h3>
                                                <div class="row p-0 m-0">
                                                    <!--begin::Form group-->
                                                    <div class="form-group col-12 col-lg-6 mb-5 pl-0">
                                                        <div class="d-flex justify-content-between mt-n5">
                                                            <!-- svelte-ignore a11y-label-has-associated-control -->
                                                            <label
                                                                class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                                >Username</label>
                                                        </div>
                                                        <input
                                                            bind:value={userInfo.username}
                                                            class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                            type="text"
                                                            placeholder="Username"
                                                            name="username"
                                                            autocomplete="off" />
                                                    </div>
                                                    <!--end::Form group-->
                                                    <!--begin::Form group-->
                                                    <div class="form-group col-12 col-lg-6 mb-5 pl-0">
                                                        <div class="d-flex justify-content-between mt-n5">
                                                            <!-- svelte-ignore a11y-label-has-associated-control -->
                                                            <label
                                                                class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                                >Email</label>
                                                        </div>
                                                        <input
                                                            bind:value={userInfo.email}
                                                            class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                            type="email"
                                                            placeholder="Email"
                                                            name="email"
                                                            autocomplete="off" />
                                                    </div>
                                                    <!--end::Form group-->
                                                </div>
                                                <!--begin::Form group-->
                                                <div class="form-group">
                                                    <div class="d-flex justify-content-between mt-n5">
                                                        <!-- svelte-ignore a11y-label-has-associated-control -->
                                                        <label class="font-size-h6 font-weight-bolder text-dark pt-5"
                                                            >Password</label>
                                                    </div>

                                                    <div class="input-group input-group-solid">
                                                        <input
                                                            bind:value={userInfo.password}
                                                            class="form-control form-control-solid p-7 rounded-lg font-size-sm"
                                                            type="password"
                                                            placeholder="Password"
                                                            name="password"
                                                            autocomplete="off"
                                                            id="signin-password-association" />
                                                        <div
                                                            class="input-group-append"
                                                            style="cursor:pointer"
                                                            on:click={() =>
                                                                togglePasswordInput(
                                                                    'signin-password-association',
                                                                    'userPassword'
                                                                )}>
                                                            <span class="input-group-text">
                                                                {#if userInfo.password_visibility}
                                                                    <EyeOff size={16} />
                                                                {:else}
                                                                    <Eye size={16} />
                                                                {/if}
                                                            </span>
                                                        </div>
                                                    </div>
                                                </div>
                                            {/if}
                                            <!--end::Form group-->
                                            <!--begin::Form group-->
                                            <div class="form-group align-items-center">
                                                <label class="checkbox mb-0" style="font-size: 1rem !important;">
                                                    <input type="checkbox" name="agree" bind:checked={agreeCheckbox} />
                                                    <span />
                                                    <div class="pl-2">
                                                        Accetto i
                                                        {#if termsAndConditionsUrl}
                                                            <a
                                                                class="ml-1"
                                                                href={termsAndConditionsUrl}
                                                                target="_blank"
                                                                rel="noopener noreferrer">termini e condizioni</a>
                                                        {:else}
                                                            <span class="ml-1">termini e condizioni del servizio</span>
                                                        {/if}
                                                    </div>
                                                </label>
                                            </div>
                                            <div class="form-group align-items-center">
                                                <label class="checkbox mb-0" style="font-size: 1rem !important;">
                                                    <input type="checkbox" name="age" bind:checked={ageCheckbox} />
                                                    <span />
                                                    <div class="pl-2">Dichiario di aver compiuto 18 anni</div>
                                                </label>
                                            </div>
                                            <!--end::Form group-->
                                            <!--begin::Form group-->
                                            <div class="form-group d-flex flex-wrap pb-lg-0 pb-3">
                                                <button
                                                    type="button"
                                                    id="bkn_login_signup_cancel"
                                                    class="btn btn-light-primary font-weight-bolder font-size-sm px-8 py-4 my-3 mr-4"
                                                    on:click|preventDefault={() => toggleView('signup')}
                                                    >Indietro</button>
                                                <button
                                                    type="button"
                                                    disabled={!agreeCheckbox ||
                                                        !ageCheckbox ||
                                                        !userInfo.denomination ||
                                                        !userInfo.tax_code}
                                                    on:click|preventDefault={() => {
                                                        if (singleSignOnData.onboarding) {
                                                            userInfo.sport_association = true;
                                                            signup({
                                                                backend: singleSignOnData.backend,
                                                                token: singleSignOnData.token,
                                                                email: singleSignOnData.email,
                                                                name: singleSignOnData.name,
                                                                username: singleSignOnData.username,
                                                                picture: singleSignOnData.picture,
                                                                response: singleSignOnData.response,
                                                                is_sport_association:
                                                                    selectedAccountType != 'signup_athlete',
                                                            });
                                                        } else {
                                                            handleAssociationSignUpForm();
                                                        }
                                                    }}
                                                    class="btn btn-primary font-weight-bolder font-size-sm px-8 py-4 my-3"
                                                    >Conferma e Accedi</button>
                                            </div>
                                            <!--end::Form group-->
                                        </div>
                                    {/if}
                                </form>
                                <!--end::Form-->
                            </div>
                            <!--end::Signup Sport Association-->
                            <!--begin::Forgot-->
                            <div class="login-form login-forgot form-40" class:no-display={currentShown != 3}>
                                <div class="pb-13 pt-lg-0 pt-1">
                                    <img id="logo" class="h-40px" src={brandLogo} alt="logo" />
                                </div>
                                <!--begin::Form-->
                                <form
                                    class="form"
                                    novalidate="novalidate"
                                    id="bkn_login_forgot_form"
                                    autocomplete="off"
                                    on:keyup={e => e.key === 'Enter' && reset()}>
                                    {#if currentShown == 3}
                                        <div in:scale={{duration: 100, start: 0.95}}>
                                            <!--begin::Title-->
                                            <div class="pb-13 pt-lg-0 pt-5">
                                                <h3 class="font-weight-bolder text-dark font-size-h4 font-size-h1-lg">
                                                    Password Dimenticata ?
                                                </h3>
                                                <p class="text-muted font-weight-bold font-size-h4">
                                                    Inserisci la tua email per resettare la password
                                                </p>
                                            </div>
                                            <!--end::Title-->
                                            <!--begin::Form group-->
                                            <div class="form-group">
                                                <input
                                                    tabindex="0"
                                                    bind:value={email}
                                                    id="email"
                                                    class="form-control form-control-solid h-auto p-6 rounded-lg font-size-h6"
                                                    type="email"
                                                    placeholder="Email"
                                                    name="email_forgot"
                                                    autocomplete="off" />
                                            </div>
                                            <input class="no-display" type="text" autocomplete="off" value="1" />
                                            <!--end::Form group-->
                                            <!--begin::Form group-->
                                            <div class="form-group d-flex flex-wrap pb-lg-0">
                                                <a
                                                    id="bkn_login_forgot_cancel"
                                                    class="btn btn-light-primary font-weight-bolder font-size-h6 px-8 py-4 my-3 mr-4"
                                                    on:click|preventDefault={() => toggleView('login')}>Indietro</a>
                                                <button
                                                    type="button"
                                                    id="bkn_login_forgot_submit"
                                                    class="btn btn-primary font-weight-bolder font-size-h6 px-8 py-4 my-3"
                                                    on:click|preventDefault={() => reset()}>Invia Email</button>
                                            </div>
                                            <!--end::Form group-->
                                        </div>
                                    {/if}
                                </form>
                                <!--end::Form-->
                            </div>
                            <!--end::Forgot-->
                        </div>
                        <!--end::Content body-->
                    </div>

                    {#if $oemConfig?.displaySettings?.login?.showTestimonials}
                        <div
                            class="d-none d-md-flex flex-column align-items-center justify-content-center shadow-sm col-md-6 h-100 rounded-xl p-0"
                            style="background-image: url('/static/images/bg-testimonials.png');background-size:cover;overflow:hidden;">
                            <div
                                style="backdrop-filter: blur(3px) brightness(0.9);height:100%;width:100%;-webkit-backdrop-filter: blur(3px) brightness(0.9);"
                                class="d-flex flex-column justify-content-between align-items-start px-24 py-24">
                                <h1
                                    class="font-weight-boldest text-white text-shadow-lg"
                                    style="font-size:5rem;line-height:4.7rem;text-shadow: rgb(17 0 115 / 20%) 0px 0.5rem 2.5rem;letter-spacing: -3px;">
                                    Perché scegliere<br />
                                    {$oemConfig?.name || 'assozeta'}
                                </h1>
                                <div class="d-flex flex-column justify-content-between text-white">
                                    {#each testimonials as testimonial}
                                        <div
                                            class="d-flex flex-column align-items-start justify-content-start w-100 rounded-lg mb-8 shadow-sm"
                                            style="max-height: 16rem;font-size: 1.7rem; min-width: 100%;line-height:2.2rem; font-weight: 500; font-style: italic; background: var(--bg-surface-secondary); padding: 2rem; letter-spacing: -1px; color: var(--text-primary); max-width: 45rem;">
                                            <div class="text-left mb-4">
                                                <b style="font-size:2.5rem;">&quot</b>
                                                {testimonial.text}
                                                <b style="font-size:2.5rem;">&quot</b>
                                            </div>
                                            <div class="d-flex align-items-center">
                                                <div
                                                    class="rounded-circle bg-white mr-4"
                                                    style="overflow:hidden;height:30px;width:30px;">
                                                    <img
                                                        src={testimonial.image}
                                                        alt={testimonial.denomination}
                                                        height="31" />
                                                </div>
                                                <div
                                                    class="font-weight-bolder font-size-lg text-dark"
                                                    style="font-weight: 800 !important;letter-spacing: -0.5px;font-size:1.2rem;">
                                                    {testimonial.denomination}
                                                </div>
                                            </div>
                                        </div>
                                    {/each}
                                </div>
                            </div>
                        </div>
                    {/if}
                </div>
            </div>

            <!--end::Content-->
        </div>

        {#if $oemConfig?.displaySettings?.login?.showFooterInfo && (privacyPolicyUrl || termsAndConditionsUrl)}
            <!--begin::Content footer-->
            <div class="d-flex justify-content-center align-items-start py-2 py-lg-0 relative-bottom bg-white">
                {#if privacyPolicyUrl}
                    <a
                        href={privacyPolicyUrl}
                        class="m-0 p-0 text-primary font-weight-bolder font-size-sm"
                        title="Privacy Policy">Privacy Policy</a>
                {/if}
                {#if privacyPolicyUrl && termsAndConditionsUrl}
                    <span class="pl-2 pr-2 text-primary font-size-sm">|</span>
                {/if}
                {#if termsAndConditionsUrl}
                    <a
                        href={termsAndConditionsUrl}
                        class="m-0 p-0 text-primary font-weight-bolder font-size-sm"
                        title="Termini e Condizioni">Termini e Condizioni</a>
                {/if}
            </div>
        {/if}
        <!--end::Content footer-->
        <!--end::Login-->
    </div>
{/if}

<style>
    .no-display {
        display: none;
    }
    .form-40 {
        width: 40rem;
    }
    .form-50 {
        max-width: 50rem;
        width: 100%;
    }
</style>
