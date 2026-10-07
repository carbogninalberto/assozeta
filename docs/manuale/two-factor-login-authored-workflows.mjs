// Application-owned setup and credential login only; phone app interaction remains external.
// Prepared contracts: no screenshot or outcome is verified before a passed real-backend run.
export const twoFactorLoginAuthoredWorkflows = Object.freeze({
    "account-two-factor-login": {
        "version": 1,
        "script": "selfhost/tests/browser/manuale/two-factor-login.mjs",
        "prefix": "images/autenticazione/attivazione-accesso/",
        "pages": [
            "faq/come-abilitare-autenticazione-due-fattori.mdx"
        ],
        "sources": [
            "UI/src/routes/profile/sections/TwoFactor.svelte",
            "UI/src/routes/profile/ProfileMenu.svelte",
            "UI/src/routes/profile/Profile.svelte",
            "UI/src/utils/profileNavigation.js",
            "UI/src/routes/login/Login.svelte",
            "UI/src/utils/loginSession.js",
            "UI/src/utils/ApiMiddleware.js",
            "BE/application/views/two_fa_views.py",
            "BE/application/views/auth_views.py",
            "BE/application/views/profile_views.py",
            "BE/application/models/user_models.py",
            "BE/application/services/jwt_token_service.py",
            "BE/application/permissions_registry.py",
            "BE/application/impersonation.py",
            "BE/core/middleware.py",
            "BE/core/settings.py",
            "BE/application/urls.py"
        ],
        "dependencies": [
            "selfhost/tests/browser/manuale/scenario.mjs",
            "selfhost/tests/browser/manuale/frame.mjs",
            "selfhost/tests/browser/manuale/redaction.mjs",
            "selfhost/tests/browser/playwright.manual.config.mjs",
            "BE/application/management/commands/seed_manuale.py",
            "BE/application/management/commands/run_manuale_instance.py",
            "selfhost/tests/browser/manuale/two-factor-login.mjs",
            "docs/manuale/two-factor-login-authored-workflows.mjs",
            "UI/src/routes/profile/sections/TwoFactor.svelte",
            "UI/src/routes/profile/ProfileMenu.svelte",
            "UI/src/routes/profile/Profile.svelte",
            "UI/src/utils/profileNavigation.js",
            "UI/src/routes/login/Login.svelte",
            "UI/src/utils/loginSession.js",
            "UI/src/utils/ApiMiddleware.js",
            "BE/application/views/two_fa_views.py",
            "BE/application/views/auth_views.py",
            "BE/application/views/profile_views.py",
            "BE/application/models/user_models.py",
            "BE/application/services/jwt_token_service.py",
            "BE/application/permissions_registry.py",
            "BE/application/impersonation.py",
            "BE/core/middleware.py",
            "BE/core/settings.py",
            "BE/application/urls.py"
        ],
        "checkpoints": [
            {
                "id": "twofa-original-disabled",
                "caption": "Profilo: doppio fattore disattivato prima della configurazione."
            },
            {
                "id": "twofa-setup-secret-and-qr-masked",
                "caption": "Configurazione preparata ma non salvata, QR e chiave interamente nascosti."
            },
            {
                "id": "twofa-code-before-save-masked",
                "caption": "Codice OTP inserito e nascosto prima del salvataggio."
            },
            {
                "id": "twofa-enabled-after-reload",
                "caption": "Doppio fattore salvato e ancora attivo dopo il ricaricamento."
            },
            {
                "id": "twofa-fresh-login-credentials-masked",
                "caption": "Nuovo accesso con identificativo e password nascosti."
            },
            {
                "id": "twofa-fresh-login-otp-masked",
                "caption": "Richiesta del secondo fattore prima di concedere una sessione."
            },
            {
                "id": "twofa-fresh-login-authenticated",
                "caption": "Accesso alla bacheca completato con credenziali e codice reali."
            },
            {
                "id": "twofa-fresh-login-reloaded",
                "caption": "Profilo riaperto nella nuova sessione: doppio fattore ancora attivo."
            },
            {
                "id": "twofa-disabled-after-reload",
                "caption": "Disattivazione dell’account dimostrativo salvata dopo la prova."
            }
        ],
        "sections": [
            {
                "path": "faq/come-abilitare-autenticazione-due-fattori.mdx",
                "id": "come-attivare-il-2fa",
                "title": "Come attivare il 2FA",
                "checkpoints": [
                    "twofa-original-disabled",
                    "twofa-setup-secret-and-qr-masked",
                    "twofa-code-before-save-masked",
                    "twofa-enabled-after-reload"
                ],
                "images": [
                    {
                        "from": "/images/autenticazione/attivazione-accesso/2.placeholder.svg",
                        "checkpoint": "twofa-setup-secret-and-qr-masked"
                    }
                ],
                "insert_images": [
                    {
                        "step": "Apri la pagina del tuo profilo",
                        "checkpoint": "twofa-original-disabled"
                    },
                    {
                        "step": "Conferma il codice",
                        "checkpoint": "twofa-code-before-save-masked"
                    },
                    {
                        "step": "Salva e controlla lo stato",
                        "checkpoint": "twofa-enabled-after-reload"
                    }
                ]
            },
            {
                "path": "faq/come-abilitare-autenticazione-due-fattori.mdx",
                "id": "come-funziona-il-login-con-il-2fa",
                "title": "Come funziona il login con il 2FA",
                "checkpoints": [
                    "twofa-fresh-login-credentials-masked",
                    "twofa-fresh-login-otp-masked",
                    "twofa-fresh-login-authenticated",
                    "twofa-fresh-login-reloaded"
                ],
                "images": [
                    {
                        "from": "/images/autenticazione/attivazione-accesso/6.placeholder.svg",
                        "checkpoint": "twofa-fresh-login-otp-masked"
                    }
                ],
                "insert_images": [
                    {
                        "step": "Inserisci le credenziali",
                        "checkpoint": "twofa-fresh-login-credentials-masked"
                    },
                    {
                        "step": "Controlla l’accesso completato",
                        "checkpoint": "twofa-fresh-login-authenticated"
                    },
                    {
                        "step": "Controlla l’accesso completato",
                        "checkpoint": "twofa-fresh-login-reloaded"
                    }
                ]
            }
        ],
        "outcome": {
            "field": "two_factor_login_authored_workflow",
            "expected": {
                "initial_disabled": true,
                "setup_generated_via_ui": true,
                "qr_and_secret_masked": true,
                "setup_not_persisted_before_save": true,
                "invalid_setup_otp_rejected_without_activation": true,
                "enabled_persisted": true,
                "fresh_protected_route_denied": true,
                "invalid_login_otp_rejected_without_tokens": true,
                "fresh_login_required_otp": true,
                "fresh_login_authenticated": true,
                "authenticated_identity_matches": true,
                "fresh_login_reloaded_and_enabled": true,
                "disabled_via_ui_persisted": true,
                "scoped_cleanup_disabled": true
            }
        }
    }
});
