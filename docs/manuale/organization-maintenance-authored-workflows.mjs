// Prepared source-grounded workflows; no capture or execution proof is fabricated here.
export const organizationMaintenanceAuthoredWorkflows = Object.freeze({
  "archive-maintenance": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/archive-maintenance.mjs",
    "prefix": "images/archivio/manutenzione/",
    "pages": [
      "docs/archivio.mdx",
      "faq/come-archiviare-dati.mdx"
    ],
    "sources": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/utils/Permissions.js",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/MembersListArchive.svelte",
      "UI/src/routes/association/archive/Archive.svelte",
      "UI/src/routes/association/Members/shared/NavigationTab.svelte",
      "UI/src/routes/accounting/payment/PaymentListArchive.svelte",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/components/buttons/ArchiveButton.svelte",
      "UI/src/components/inputs/BottomBarFixedSave.svelte",
      "BE/application/views/subscriptions_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/user_models.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/views/invoice_views.py",
      "BE/application/views/course_views.py",
      "BE/application/permissions_registry.py",
      "BE/application/tasks.py",
      "BE/application/urls.py"
    ],
    "dependencies": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/utils/Permissions.js",
      "UI/src/routes/association/Members/MembersList.svelte",
      "UI/src/routes/association/Members/MembersListArchive.svelte",
      "UI/src/routes/association/archive/Archive.svelte",
      "UI/src/routes/association/Members/shared/NavigationTab.svelte",
      "UI/src/routes/accounting/payment/PaymentListArchive.svelte",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Settings.svelte",
      "UI/src/components/buttons/ArchiveButton.svelte",
      "UI/src/components/inputs/BottomBarFixedSave.svelte",
      "BE/application/views/subscriptions_views.py",
      "BE/application/views/payment_views.py",
      "BE/application/views/profile_views.py",
      "BE/application/models/subscriptions_models.py",
      "BE/application/models/payment_models.py",
      "BE/application/models/user_models.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/views/invoice_views.py",
      "BE/application/views/course_views.py",
      "BE/application/permissions_registry.py",
      "BE/application/tasks.py",
      "BE/application/urls.py",
      "selfhost/tests/browser/manuale/archive-maintenance.mjs",
      "docs/manuale/organization-maintenance-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "selected-unpaid-member-before-archiving",
        "caption": "Iscrizione di Sara Conti selezionata prima di Archivia."
      },
      {
        "id": "member-archive-confirmation-and-cancel",
        "caption": "Conferma Archivia selezionati e possibilità di Annulla."
      },
      {
        "id": "active-members-after-archive-and-reload",
        "caption": "Tesserati dopo l’archiviazione e il ricaricamento: Sara assente."
      },
      {
        "id": "archived-member-and-restore-action-after-reload",
        "caption": "Sara presente nell’Archivio delle iscrizioni dopo il ricaricamento."
      },
      {
        "id": "archive-search-finds-selected-registration",
        "caption": "Ricerca Sara nell’Archivio delle iscrizioni."
      },
      {
        "id": "unpaid-member-payment-in-separate-payment-archive",
        "caption": "Quota non saldata di Sara nel distinto Archivio dei pagamenti."
      },
      {
        "id": "reader-consults-archive-with-disabled-restore",
        "caption": "Collaboratore in sola lettura: iscrizione consultabile e ripristino disattivato."
      },
      {
        "id": "member-restore-confirmation",
        "caption": "Conferma Sposta nel libro soci dalla riga archiviata."
      },
      {
        "id": "restored-member-and-payment-still-archived",
        "caption": "Iscrizione ripristinata dopo ricaricamento; pagamento da controllare separatamente."
      },
      {
        "id": "fiscal-start-before-automatic-archive-preference",
        "caption": "Anno fiscale controllato prima dell’opzione automatica."
      },
      {
        "id": "automatic-archive-enabled-before-save",
        "caption": "Archivia Iscrizioni Automaticamente attivato prima di Salva."
      },
      {
        "id": "automatic-archive-preference-persists-after-reload",
        "caption": "Interruttore conservato dopo il salvataggio e il ricaricamento."
      },
      {
        "id": "reader-can-consult-automatic-preference-without-changing-it",
        "caption": "Collaboratore in sola lettura: preferenza visibile e controllo disattivato."
      },
      {
        "id": "automatic-archive-preference-restored-and-baseline-preserved",
        "caption": "Opzione dimostrativa disattivata di nuovo e preferenze iniziali ripristinate."
      }
    ],
    "sections": [
      {
        "path": "docs/archivio.mdx",
        "id": "come-archiviare-un-elemento",
        "title": "Come archiviare un elemento",
        "checkpoints": [
          "selected-unpaid-member-before-archiving",
          "member-archive-confirmation-and-cancel",
          "active-members-after-archive-and-reload",
          "archived-member-and-restore-action-after-reload",
          "unpaid-member-payment-in-separate-payment-archive"
        ],
        "images": [
          {
            "from": "/images/archivio/manutenzione/1.placeholder.svg",
            "checkpoint": "selected-unpaid-member-before-archiving"
          },
          {
            "from": "/images/archivio/manutenzione/2.placeholder.svg",
            "checkpoint": "member-archive-confirmation-and-cancel"
          },
          {
            "from": "/images/archivio/manutenzione/3.placeholder.svg",
            "checkpoint": "active-members-after-archive-and-reload"
          },
          {
            "from": "/images/archivio/manutenzione/4.placeholder.svg",
            "checkpoint": "archived-member-and-restore-action-after-reload"
          },
          {
            "from": "/images/archivio/manutenzione/6.placeholder.svg",
            "checkpoint": "unpaid-member-payment-in-separate-payment-archive"
          }
        ],
        "remove_draft_text": [
          "<Note>Bozza in attesa di prova: i passaggi e le immagini previste richiedono la verifica del flusso reale prima della pubblicazione.</Note>"
        ]
      },
      {
        "path": "docs/archivio.mdx",
        "id": "consultare-l-archivio",
        "title": "Consultare l'archivio",
        "checkpoints": [
          "archived-member-and-restore-action-after-reload",
          "archive-search-finds-selected-registration",
          "unpaid-member-payment-in-separate-payment-archive",
          "reader-consults-archive-with-disabled-restore"
        ],
        "images": [
          {
            "from": "/images/archivio/manutenzione/4.placeholder.svg",
            "checkpoint": "archived-member-and-restore-action-after-reload"
          },
          {
            "from": "/images/archivio/manutenzione/5.placeholder.svg",
            "checkpoint": "archive-search-finds-selected-registration"
          },
          {
            "from": "/images/archivio/manutenzione/6.placeholder.svg",
            "checkpoint": "unpaid-member-payment-in-separate-payment-archive"
          },
          {
            "from": "/images/archivio/manutenzione/7.placeholder.svg",
            "checkpoint": "reader-consults-archive-with-disabled-restore"
          }
        ],
        "remove_draft_text": [
          "<Note>Bozza in attesa di prova: i passaggi e le immagini previste richiedono la verifica del flusso reale prima della pubblicazione.</Note>"
        ]
      },
      {
        "path": "faq/come-archiviare-dati.mdx",
        "id": "come-attivarla",
        "title": "Come attivarla",
        "checkpoints": [
          "fiscal-start-before-automatic-archive-preference",
          "automatic-archive-enabled-before-save",
          "automatic-archive-preference-persists-after-reload",
          "reader-can-consult-automatic-preference-without-changing-it",
          "automatic-archive-preference-restored-and-baseline-preserved"
        ],
        "images": [
          {
            "from": "/images/archivio/manutenzione/10.placeholder.svg",
            "checkpoint": "fiscal-start-before-automatic-archive-preference"
          },
          {
            "from": "/images/archivio/manutenzione/11.placeholder.svg",
            "checkpoint": "automatic-archive-enabled-before-save"
          },
          {
            "from": "/images/archivio/manutenzione/12.placeholder.svg",
            "checkpoint": "automatic-archive-preference-persists-after-reload"
          },
          {
            "from": "/images/archivio/manutenzione/13.placeholder.svg",
            "checkpoint": "reader-can-consult-automatic-preference-without-changing-it"
          },
          {
            "from": "/images/archivio/manutenzione/14.placeholder.svg",
            "checkpoint": "automatic-archive-preference-restored-and-baseline-preserved"
          }
        ],
        "remove_draft_text": [
          "<Note>Bozza in attesa di prova: i passaggi e le immagini previste richiedono la verifica del flusso reale prima della pubblicazione.</Note>"
        ]
      },
      {
        "path": "docs/archivio.mdx",
        "id": "introduzione",
        "title": "Introduzione",
        "checkpoints": [
          "archived-member-and-restore-action-after-reload"
        ],
        "images": [
          {
            "from": "/images/archivio/manutenzione/4.placeholder.svg",
            "checkpoint": "archived-member-and-restore-action-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "docs/archivio.mdx",
        "id": "ripristinare-un-elemento-dall-archivio",
        "title": "Ripristinare un elemento dall'archivio",
        "checkpoints": [
          "archived-member-and-restore-action-after-reload",
          "member-restore-confirmation",
          "restored-member-and-payment-still-archived"
        ],
        "images": [
          {
            "from": "/images/archivio/manutenzione/4.placeholder.svg",
            "checkpoint": "archived-member-and-restore-action-after-reload"
          },
          {
            "from": "/images/archivio/manutenzione/8.placeholder.svg",
            "checkpoint": "member-restore-confirmation"
          },
          {
            "from": "/images/archivio/manutenzione/9.placeholder.svg",
            "checkpoint": "restored-member-and-payment-still-archived"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-archiviare-dati.mdx",
        "id": "cosa-significa-archiviare",
        "title": "Cosa significa archiviare",
        "checkpoints": [
          "archived-member-and-restore-action-after-reload"
        ],
        "images": [
          {
            "from": "/images/archivio/manutenzione/4.placeholder.svg",
            "checkpoint": "archived-member-and-restore-action-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-archiviare-dati.mdx",
        "id": "archiviazione-manuale",
        "title": "Archiviazione manuale",
        "checkpoints": [
          "selected-unpaid-member-before-archiving",
          "member-archive-confirmation-and-cancel",
          "archived-member-and-restore-action-after-reload"
        ],
        "images": [
          {
            "from": "/images/archivio/manutenzione/1.placeholder.svg",
            "checkpoint": "selected-unpaid-member-before-archiving"
          },
          {
            "from": "/images/archivio/manutenzione/2.placeholder.svg",
            "checkpoint": "member-archive-confirmation-and-cancel"
          },
          {
            "from": "/images/archivio/manutenzione/4.placeholder.svg",
            "checkpoint": "archived-member-and-restore-action-after-reload"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-archiviare-dati.mdx",
        "id": "consultare-i-dati-archiviati",
        "title": "Consultare i dati archiviati",
        "checkpoints": [
          "archive-search-finds-selected-registration",
          "unpaid-member-payment-in-separate-payment-archive"
        ],
        "images": [
          {
            "from": "/images/archivio/manutenzione/5.placeholder.svg",
            "checkpoint": "archive-search-finds-selected-registration"
          },
          {
            "from": "/images/archivio/manutenzione/6.placeholder.svg",
            "checkpoint": "unpaid-member-payment-in-separate-payment-archive"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-archiviare-dati.mdx",
        "id": "ripristinare-i-dati-archiviati",
        "title": "Ripristinare i dati archiviati",
        "checkpoints": [
          "archived-member-and-restore-action-after-reload",
          "member-restore-confirmation",
          "restored-member-and-payment-still-archived"
        ],
        "images": [
          {
            "from": "/images/archivio/manutenzione/4.placeholder.svg",
            "checkpoint": "archived-member-and-restore-action-after-reload"
          },
          {
            "from": "/images/archivio/manutenzione/8.placeholder.svg",
            "checkpoint": "member-restore-confirmation"
          },
          {
            "from": "/images/archivio/manutenzione/9.placeholder.svg",
            "checkpoint": "restored-member-and-payment-still-archived"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-archiviare-dati.mdx",
        "id": "cosa-viene-archiviato",
        "title": "Cosa viene archiviato",
        "checkpoints": [
          "unpaid-member-payment-in-separate-payment-archive"
        ],
        "images": [
          {
            "from": "/images/archivio/manutenzione/6.placeholder.svg",
            "checkpoint": "unpaid-member-payment-in-separate-payment-archive"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "archive_maintenance_authored_workflow",
      "expected": {
        "archive_cancel_preserved_state": true,
        "member_archived_after_reload": true,
        "archive_search_and_clear_worked": true,
        "separate_unpaid_payment_archived": true,
        "other_paid_payments_preserved": true,
        "reader_restore_status": 403,
        "member_restored_after_reload": true,
        "restore_does_not_restore_payment": true,
        "payment_restored_separately": true,
        "automatic_preference_saved_after_reload": true,
        "fiscal_settings_preserved": true,
        "saving_preference_does_not_move_members": true,
        "reader_automatic_setting_status": 403,
        "baseline_records_and_settings_restored": true,
        "baseline_receipts_and_course_enrollments_preserved": true
      }
    }
  },
  "collaborator-removal": {
    "version": 1,
    "script": "selfhost/tests/browser/manuale/collaborator-removal.mjs",
    "prefix": "images/collaboratori/rimozione/",
    "pages": [
      "docs/collaboratori.mdx",
      "faq/come-invitare-collaboratori.mdx"
    ],
    "sources": [
      "BE/application/impersonation.py",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/serializers/auth_serializers.py",
      "BE/application/serializers/collaborators_serializers.py",
      "BE/application/urls.py",
      "BE/application/views/collaborator_views.py",
      "BE/application/views/profile_views.py",
      "BE/core/authentication.py",
      "BE/instance/permissions.py",
      "UI/src/components/PermissionsComponent.svelte",
      "UI/src/components/Sidebar.svelte",
      "UI/src/components/buttons/DeleteButton.svelte",
      "UI/src/routes.js",
      "UI/src/routes/connectedCollaborators/CollaboratorActions.svelte",
      "UI/src/routes/connectedCollaborators/ConnectedCollaborators.svelte",
      "UI/src/routes/connectedCollaborators/modals/EditModal.svelte",
      "UI/src/routes/profile/ProfileMenu.svelte",
      "UI/src/routes/profile/sections/Account.svelte",
      "UI/src/utils/Permissions.js"
    ],
    "dependencies": [
      "UI/src/components/Sidebar.svelte",
      "UI/src/routes.js",
      "UI/src/utils/Permissions.js",
      "UI/src/routes/connectedCollaborators/ConnectedCollaborators.svelte",
      "UI/src/routes/connectedCollaborators/CollaboratorActions.svelte",
      "UI/src/components/buttons/DeleteButton.svelte",
      "BE/application/views/collaborator_views.py",
      "BE/application/serializers/collaborators_serializers.py",
      "BE/application/models/user_models.py",
      "BE/application/permissions_registry.py",
      "BE/application/impersonation.py",
      "BE/core/authentication.py",
      "BE/application/management/commands/seed_manuale.py",
      "BE/application/urls.py",
      "selfhost/tests/browser/manuale/collaborator-removal.mjs",
      "docs/manuale/organization-maintenance-authored-workflows.mjs"
    ],
    "checkpoints": [
      {
        "id": "removable-accepted-account-and-separate-pending-invite",
        "caption": "Paolo Testa Accettato e invito temporaneo In attesa nella lista Collaboratori."
      },
      {
        "id": "reader-cannot-delete-account-or-invite",
        "caption": "Cestini disattivati per il collaboratore in sola lettura."
      },
      {
        "id": "account-removal-confirmation-and-cancel",
        "caption": "Vuoi eliminare il collaboratore? con Annulla ed Elimina."
      },
      {
        "id": "removed-account-absent-after-reload-invite-preserved",
        "caption": "Account Paolo Testa assente dopo il ricaricamento; invito separato conservato."
      },
      {
        "id": "pending-invite-removal-confirmation",
        "caption": "Conferma di eliminazione per la riga dell’invito non accettato."
      },
      {
        "id": "pending-invite-absent-after-reload-baseline-reader-preserved",
        "caption": "Invito temporaneo assente dopo il ricaricamento; Marco Neri conservato."
      }
    ],
    "sections": [
      {
        "path": "docs/collaboratori.mdx",
        "id": "rimuovere-un-collaboratore",
        "title": "Rimuovere un collaboratore",
        "checkpoints": [
          "removable-accepted-account-and-separate-pending-invite",
          "account-removal-confirmation-and-cancel",
          "removed-account-absent-after-reload-invite-preserved",
          "pending-invite-removal-confirmation",
          "pending-invite-absent-after-reload-baseline-reader-preserved",
          "reader-cannot-delete-account-or-invite"
        ],
        "images": [
          {
            "from": "/images/collaboratori/rimozione/1.placeholder.svg",
            "checkpoint": "removable-accepted-account-and-separate-pending-invite"
          },
          {
            "from": "/images/collaboratori/rimozione/3.placeholder.svg",
            "checkpoint": "account-removal-confirmation-and-cancel"
          },
          {
            "from": "/images/collaboratori/rimozione/4.placeholder.svg",
            "checkpoint": "removed-account-absent-after-reload-invite-preserved"
          },
          {
            "from": "/images/collaboratori/rimozione/5.placeholder.svg",
            "checkpoint": "pending-invite-removal-confirmation"
          },
          {
            "from": "/images/collaboratori/rimozione/6.placeholder.svg",
            "checkpoint": "pending-invite-absent-after-reload-baseline-reader-preserved"
          },
          {
            "from": "/images/collaboratori/rimozione/2.placeholder.svg",
            "checkpoint": "reader-cannot-delete-account-or-invite"
          }
        ],
        "remove_draft_text": [
          "<Note>Bozza in attesa di prova: i passaggi e le immagini previste richiedono la verifica del flusso reale prima della pubblicazione.</Note>"
        ]
      },
      {
        "path": "faq/come-invitare-collaboratori.mdx",
        "id": "rimuovere-un-collaboratore",
        "title": "Rimuovere un collaboratore",
        "checkpoints": [
          "removable-accepted-account-and-separate-pending-invite",
          "account-removal-confirmation-and-cancel",
          "removed-account-absent-after-reload-invite-preserved",
          "pending-invite-removal-confirmation",
          "pending-invite-absent-after-reload-baseline-reader-preserved",
          "reader-cannot-delete-account-or-invite"
        ],
        "images": [
          {
            "from": "/images/collaboratori/rimozione/1.placeholder.svg",
            "checkpoint": "removable-accepted-account-and-separate-pending-invite"
          },
          {
            "from": "/images/collaboratori/rimozione/3.placeholder.svg",
            "checkpoint": "account-removal-confirmation-and-cancel"
          },
          {
            "from": "/images/collaboratori/rimozione/4.placeholder.svg",
            "checkpoint": "removed-account-absent-after-reload-invite-preserved"
          },
          {
            "from": "/images/collaboratori/rimozione/5.placeholder.svg",
            "checkpoint": "pending-invite-removal-confirmation"
          },
          {
            "from": "/images/collaboratori/rimozione/6.placeholder.svg",
            "checkpoint": "pending-invite-absent-after-reload-baseline-reader-preserved"
          },
          {
            "from": "/images/collaboratori/rimozione/2.placeholder.svg",
            "checkpoint": "reader-cannot-delete-account-or-invite"
          }
        ],
        "remove_draft_text": [
          "<Note>Bozza in attesa di prova: i passaggi e le immagini previste richiedono la verifica del flusso reale prima della pubblicazione.</Note>"
        ]
      },
      {
        "path": "docs/collaboratori.mdx",
        "id": "stato-degli-inviti",
        "title": "Stato degli inviti",
        "checkpoints": [
          "removable-accepted-account-and-separate-pending-invite"
        ],
        "images": [
          {
            "from": "/images/collaboratori/rimozione/1.placeholder.svg",
            "checkpoint": "removable-accepted-account-and-separate-pending-invite"
          }
        ],
        "insert_images": []
      },
      {
        "path": "faq/come-invitare-collaboratori.mdx",
        "id": "stato-degli-inviti",
        "title": "Stato degli inviti",
        "checkpoints": [
          "removable-accepted-account-and-separate-pending-invite"
        ],
        "images": [
          {
            "from": "/images/collaboratori/rimozione/1.placeholder.svg",
            "checkpoint": "removable-accepted-account-and-separate-pending-invite"
          }
        ],
        "insert_images": []
      }
    ],
    "outcome": {
      "field": "collaborator_removal_authored_workflow",
      "expected": {
        "dedicated_fixture_only": true,
        "account_cancel_preserved_session_and_list": true,
        "accepted_account_deleted_after_reload": true,
        "removed_existing_token_profile_status": 401,
        "removed_existing_token_members_status": 401,
        "pending_invite_preserved_after_account_deletion": true,
        "pending_invite_cancel_preserved_record": true,
        "pending_invite_deleted_after_reload": true,
        "reader_delete_account_status": 403,
        "reader_delete_invite_status": 403,
        "baseline_reader_session_preserved": true,
        "baseline_members_payments_owner_preserved": true,
        "disposable_actor_and_invite_absent": true,
        "invitation_dispatches": 0
      }
    },
    "fixture_profile": "collaborator-removal"
  }
});
