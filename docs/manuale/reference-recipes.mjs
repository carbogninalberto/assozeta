// 2FA source re-reviewed: OTP confirmation, QR setup and successful enable/disable
// remain unchanged. Any audit-index exception rolls back the selected user mutation;
// locked reads and selected columns preserve concurrent profile changes.
// Implementation contracts only. User-facing prose is maintained in Bakney/manuale MDX.
import {editorialDescriptors, referenceDescriptors} from './evidence-contracts.mjs';
const reviewed = {
  "BE/application/management/commands/seed_selfhost.py": "de0393c2d68e684dc715c729036104822ea7edc625274f42fccf39e80b0e663b",
  "BE/application/views/billing_views.py": "28ee7ec15b5bc759546d5df4aaaf4558fe577cbedc8ab60a5256f7dcda06a4c8",
  "BE/application/views/payment_views.py": "23bc385b80772d57f9eb32739860ed287fbe7106c0168a9e584221ca13beeb71",
  "BE/core/authentication.py": "4ea4b9041f1c19aef3105fa9518edb3fb880d1deddef46be7dff701c7a3da3d2",
  "BE/application/views/two_fa_views.py": "51b94979b66fae4afc9f65386b0f619258c061fc608b1dd29d3dc59a071cfdf3",
  "BE/instance/permissions.py": "ce413a53cb3dcf3cca2478c4df656078cd105f6faff868b53a4073e973885f58",
  // Reviewed: tutor conflict details remain in the owner-only, no-store report;
  // public previews expose counts only. Restore authorization is unchanged.
  "BE/instance/restore/views.py": "279a0a03b75681534b84944c09a684fa14402ff3b6e57da222e6619f70da6c0a",
  "BE/application/views/auth_views.py": "ff45ee9f1e2665b0ab19e7ae832834a481256e4ff266d7d0cec3860a5854975c",
  "BE/application/audit_context.py": "8609c865f5b2d5c4f818cd6b4fe6620ec9504fae2d5a2368fccb5745cbce25ff",
  "BE/application/signals.py": "5db3be8a3db1f261492491b2f69c52de3d722b9ccb65b7e20f246a0ad2b39d72"
};
const contracts = [
  ["BE/application/management/commands/seed_selfhost.py","class Command(BaseCommand):",6],
  ["BE/application/views/billing_views.py","def billing_active_plan(request):",8],
  ["BE/application/views/payment_views.py","def payment_add(request):",24],
  ["BE/application/views/billing_views.py","def billing_checkout(request):",24],
  ["BE/core/authentication.py","class ScopedJWTAuthentication(",15],
  ["BE/application/views/two_fa_views.py","def two_fa_update(request):",54],
  ["BE/instance/permissions.py","class IsInstanceOwner(BasePermission):",5],
  ["BE/instance/restore/views.py","class DataRestoreView(APIView):",72],
  ["BE/instance/permissions.py","def is_instance_administrator(",12],
  ["BE/application/views/auth_views.py","def oauth2_login(request):",182],
  ["BE/application/audit_context.py","_strict_audit_indexes = ContextVar(",20],
  ["BE/application/signals.py","def create_audit_log_index(",37]
];
const sections = [
  {
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "id": "piani-di-abbonamento",
    "title": "Piani di abbonamento",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [
      0,
      1,
      2
    ]
  },
  {
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "id": "confronto-tra-i-piani",
    "title": "Confronto tra i piani",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [
      0,
      3
    ]
  },
  {
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "id": "piano-base",
    "title": "Piano Base",
    "status": "unsupported",
    "reason": "Public Base purchase/table does not describe the self-hosted configuration.",
    "contracts": [
      0,
      3
    ]
  },
  {
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "id": "piano-pro",
    "title": "Piano Pro",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [
      0,
      1
    ]
  },
  {
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "id": "piano-teams",
    "title": "Piano Teams",
    "status": "unsupported",
    "reason": "Public Teams purchase is disabled for the documented self-hosted installation.",
    "contracts": [
      3
    ]
  },
  {
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "id": "come-cambiare-piano",
    "title": "Come cambiare piano",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [
      3,
      2
    ]
  },
  {
    "path": "faq/i-miei-dati-sono-al-sicuro.mdx",
    "id": "la-sicurezza-dei-tuoi-dati-e-la-nostra-priorita",
    "title": "La sicurezza dei tuoi dati è la nostra priorità",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [
      4,
      5,
      6,
      10,
      11
    ]
  },
  {
    "path": "faq/i-miei-dati-sono-al-sicuro.mdx",
    "id": "conformita-gdpr-e-gestione-responsabile-dei-dati",
    "title": "Conformità GDPR e gestione responsabile dei dati",
    "status": "needs_external_verification",
    "reason": "Deployment, hosting, backup retention, encryption and legal/privacy practices require operational evidence outside application source.",
    "contracts": []
  },
  {
    "path": "faq/i-miei-dati-sono-al-sicuro.mdx",
    "id": "robusta-politica-di-backup",
    "title": "Robusta politica di backup",
    "status": "needs_external_verification",
    "reason": "Deployment, hosting, backup retention, encryption and legal/privacy practices require operational evidence outside application source.",
    "contracts": [
      7,
      6,
      8
    ]
  },
  {
    "path": "faq/i-miei-dati-sono-al-sicuro.mdx",
    "id": "misure-di-sicurezza-avanzate",
    "title": "Misure di sicurezza avanzate",
    "status": "pending",
    "reason": "Editorial draft metadata only; implementation and workflow proof remain required.",
    "contracts": [
      4,
      5,
      9,
      7,
      6,
      8,
      10,
      11
    ]
  },
  {
    "path": "faq/i-miei-dati-sono-al-sicuro.mdx",
    "id": "infrastruttura-di-archiviazione-affidabile",
    "title": "Infrastruttura di archiviazione affidabile",
    "status": "needs_external_verification",
    "reason": "Deployment, hosting, backup retention, encryption and legal/privacy practices require operational evidence outside application source.",
    "contracts": []
  }
];
export function referenceEditorialContracts() {
    return editorialDescriptors("docs/manuale/reference-recipes.mjs", sections, contracts, reviewed);
}
const references = [
  {
    "id": "piani-di-abbonamento",
    "status": "verified",
    "features": [
      "self_hosted"
    ],
    "reason": "",
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "title": "Piani di abbonamento",
    "contracts": [
      0,
      1,
      2
    ]
  },
  {
    "id": "confronto-tra-i-piani",
    "status": "verified",
    "features": [
      "self_hosted"
    ],
    "reason": "",
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "title": "Confronto tra i piani",
    "contracts": [
      0,
      3
    ]
  },
  {
    "id": "piano-base",
    "status": "unsupported",
    "features": [
      "self_hosted"
    ],
    "reason": "Public Base purchase/table does not describe the self-hosted configuration.",
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "title": "Piano Base",
    "contracts": [
      0,
      3
    ]
  },
  {
    "id": "piano-pro",
    "status": "verified",
    "features": [
      "self_hosted"
    ],
    "reason": "",
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "title": "Piano Pro",
    "contracts": [
      0,
      1
    ]
  },
  {
    "id": "piano-teams",
    "status": "unsupported",
    "features": [
      "self_hosted"
    ],
    "reason": "Public Teams purchase is disabled for the documented self-hosted installation.",
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "title": "Piano Teams",
    "contracts": [
      3
    ]
  },
  {
    "id": "come-cambiare-piano",
    "status": "verified",
    "features": [
      "self_hosted"
    ],
    "reason": "",
    "path": "faq/quali-sono-piani-abbonamento.mdx",
    "title": "Come cambiare piano",
    "contracts": [
      3,
      2
    ]
  },
  {
    "id": "la-sicurezza-dei-tuoi-dati-e-la-nostra-priorita",
    "status": "verified",
    "features": [],
    "reason": "",
    "path": "faq/i-miei-dati-sono-al-sicuro.mdx",
    "title": "La sicurezza dei tuoi dati è la nostra priorità",
    "contracts": [
      4,
      5,
      6,
      10,
      11
    ]
  },
  {
    "id": "conformita-gdpr-e-gestione-responsabile-dei-dati",
    "status": "needs_external_verification",
    "features": [],
    "reason": "Deployment, hosting, backup retention, encryption and legal/privacy practices require operational evidence outside application source.",
    "path": "faq/i-miei-dati-sono-al-sicuro.mdx",
    "title": "Conformità GDPR e gestione responsabile dei dati",
    "contracts": []
  },
  {
    "id": "robusta-politica-di-backup",
    "status": "needs_external_verification",
    "features": [
      "self_hosted"
    ],
    "reason": "Deployment, hosting, backup retention, encryption and legal/privacy practices require operational evidence outside application source.",
    "path": "faq/i-miei-dati-sono-al-sicuro.mdx",
    "title": "Robusta politica di backup",
    "contracts": [
      7,
      6,
      8
    ]
  },
  {
    "id": "misure-di-sicurezza-avanzate",
    "status": "verified",
    "features": [
      "self_hosted"
    ],
    "reason": "",
    "path": "faq/i-miei-dati-sono-al-sicuro.mdx",
    "title": "Misure di sicurezza avanzate",
    "contracts": [
      4,
      5,
      9,
      7,
      6,
      8,
      10,
      11
    ]
  },
  {
    "id": "infrastruttura-di-archiviazione-affidabile",
    "status": "needs_external_verification",
    "features": [],
    "reason": "Deployment, hosting, backup retention, encryption and legal/privacy practices require operational evidence outside application source.",
    "path": "faq/i-miei-dati-sono-al-sicuro.mdx",
    "title": "Infrastruttura di archiviazione affidabile",
    "contracts": []
  }
];
export function referenceSections(source) {
    return referenceDescriptors(references, contracts, reviewed, source);
}
