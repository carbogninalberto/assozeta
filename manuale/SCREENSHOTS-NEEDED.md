# Screenshot Requirements Table

This table lists all screenshots needed for the Bakney Sport documentation.
Gli screenshot finali vengono acquisiti automaticamente da un’istanza Assozeta isolata con dati di esempio. Gli SVG sono soltanto segnaposto per la stesura.

## Naming Convention

- Save screenshots in `images/<section-name>/` folders
- Use sequential numbering: `1.png`, `2.png`, etc.
- Stesura: SVG 1920 × 1080. Consegna: PNG reali con originale 1920 × 1080, scala 1; eventuali ritagli conservano originale e coordinate.

## Status Legend

- **Has screenshots**: Page already has images
- **Needs screenshots**: New page, no images yet

---

## Documentation Pages (`docs/`)

| Page | Status | Screenshots Needed | Description |
|------|--------|-------------------|-------------|
| `introduzione.mdx` | No images needed | - | Landing page with cards only |
| `bacheca.mdx` | Needs screenshots | 3 | 1. Full dashboard view with widgets. 2. "Aggiungi widget" modal showing available widgets. 3. Dashboard in edit mode (drag/resize handles visible). |
| `libro-soci.mdx` | Has screenshots (4) | 0 | Already complete. |
| `corsi.mdx` | Needs screenshots | 3 | 1. Course list view. 2. Course creation form. 3. Course detail with enrolled athletes. |
| `istruttori.mdx` | Needs screenshots | 3 | 1. Instructor list view. 2. Instructor profile/detail page. 3. Instructor hours report (monthly view). |
| `camp-e-ritiri.mdx` | Needs screenshots | 4 | 1. Camp list view. 2. Camp creation form (general info). 3. Camp periods/services configuration. 4. Public registration page (athlete view). |
| `calendario.mdx` | Needs screenshots | 3 | 1. Calendar monthly view with events. 2. Event creation/edit modal. 3. Calendar sharing options (link/embed). |
| `carnet.mdx` | Needs screenshots | 2 | 1. Carnet list view. 2. Carnet creation/configuration form. |
| `registro-presenze.mdx` | Needs screenshots | 2 | 1. Attendance register view. 2. Marking attendance (check-in interface). |
| `pagamenti.mdx` | Needs screenshots | 3 | 1. Payments list view. 2. Payment creation form. 3. Payment detail with installments. |
| `ricevute.mdx` | Needs screenshots | 2 | 1. Receipts list view. 2. Receipt preview/PDF example. |
| `contabilita-avanzata.mdx` | Needs screenshots | 4 | 1. Active invoices list. 2. Invoice creation form. 3. Suppliers/customers registry. 4. Financial accounts (conti finanziari) view. |
| `bilancio.mdx` | Needs screenshots | 2 | 1. Balance summary view. 2. Detailed balance with categories expanded. |
| `comunicazioni.mdx` | Needs screenshots | 2 | 1. Communications list/compose view. 2. Email template editor. |
| `archivio.mdx` | Needs screenshots | 2 | 1. Archive view with tabs (Soci, Corsi, Pagamenti, Documenti). 2. Restore action from archive. |
| `collaboratori.mdx` | Needs screenshots | 3 | 1. Collaborators list with statuses. 2. Invite collaborator modal. 3. Permission configuration panel (granular permissions). |
| `impostazioni.mdx` | Needs screenshots | 5 | 1. Organization info form. 2. Fiscal year / sports season settings. 3. Receipt and document template settings. 4. Stripe payment configuration page. 5. Two-factor authentication (2FA) setup with QR code. |

## FAQ Pages (`faq/`)

| Page | Status | Screenshots Needed | Description |
|------|--------|-------------------|-------------|
| `come-si-crea-un-socio.mdx` | Has screenshots (3) | 0 | Already complete. |
| `come-inviare-email-nuovi-iscritti.mdx` | Needs screenshots | 2 | 1. Email settings for new members. 2. Example of the automatic welcome email. |
| `come-si-importano-dei-soci.mdx` | Needs screenshots | 2 | 1. Import modal/upload interface. 2. CSV template or mapping screen. |
| `i-miei-dati-sono-al-sicuro.mdx` | No images needed | - | Text-only security FAQ. |
| `come-condividere-il-link-iscrizioni.mdx` | Needs screenshots | 3 | 1. "Condividi link iscrizioni" button in Libro Soci. 2. Sharing modal with copy link, WhatsApp, email, QR code options. 3. QR code close-up (downloadable/printable). |
| `come-gestire-i-certificati-medici.mdx` | Needs screenshots | 3 | 1. Athlete profile - "Certificato Medico" tab. 2. Certificate upload form with expiry date field. 3. Dashboard widget showing expiring certificates. |
| `come-configurare-i-pagamenti-online.mdx` | Needs screenshots | 2 | 1. Stripe configuration in Impostazioni. 2. Payment link example (athlete view). |
| `come-stampare-tessere-atleti.mdx` | Needs screenshots | 2 | 1. Card template customization page. 2. Print preview of athlete card. |
| `come-generare-bilancio.mdx` | Needs screenshots | 2 | 1. Balance sheet summary view. 2. Published balance sheet. |
| `come-usare-ricerca-filtri-atleti.mdx` | Needs screenshots | 2 | 1. Search bar with filter dropdowns. 2. Bulk action menu on selected athletes. |
| `come-archiviare-dati.mdx` | Needs screenshots | 2 | 1. Archive section with tabs. 2. Manual archive action on a member. |
| `come-calcolare-generare-codici-fiscali.mdx` | Needs screenshots | 1 | 1. Tax code generation/validation in athlete profile. |
| `come-invitare-collaboratori.mdx` | Needs screenshots | 2 | 1. Invite collaborator modal. 2. Collaborator permissions panel. |
| `come-cambiare-anno-sportivo-fiscale.mdx` | Needs screenshots | 2 | 1. Fiscal year settings page. 2. Fiscal year type selector. |
| `come-collegare-google-calendar.mdx` | Needs screenshots | 2 | 1. Google Calendar connection settings. 2. Synced calendar view. |
| `come-abilitare-autenticazione-due-fattori.mdx` | Needs screenshots | 2 | 1. 2FA setup page with QR code. 2. TOTP code entry screen. |
| `quali-sono-piani-abbonamento.mdx` | No images needed | - | Text-only plan comparison table. |

## Tutorial Pages (`tutorials/`)

| Page | Status | Screenshots Needed | Description |
|------|--------|-------------------|-------------|
| `come-assegnare-i-tag-agli-atleti.mdx` | Has screenshots (6) | 0 | Already complete. |
| `come-creare-moduli-iscrizione-personalizzati.mdx` | Has screenshots (13) | 0 | Already complete. |
| `come-gestire-registro-presenze-carnet.mdx` | Needs screenshots | 3 | 1. Attendance register with checkboxes. 2. Carnet assignment modal. 3. Carnet usage summary for an athlete. |
| `come-esportare-dati-associazione.mdx` | Needs screenshots | 3 | 1. Export button in Libro Soci. 2. Export format selection (Excel/CSV). 3. Payments export with date filter. |
| `come-impostare-pagamenti-rateizzati.mdx` | Needs screenshots | 3 | 1. Course creation with "Rateizza" option enabled. 2. Installment plan configuration (dates/amounts). 3. Athlete payment detail showing installments. |
| `come-gestire-certificati-medici.mdx` | Needs screenshots | 3 | 1. Medical certificate section in athlete profile. 2. Certificate upload form. 3. Expiring certificates dashboard widget. |
| `come-impostare-gestire-istruttori.mdx` | Needs screenshots | 3 | 1. Instructor creation form. 2. Instructor detail with assigned courses. 3. Monthly hours report. |
| `come-impostare-stripe-pagamenti-online.mdx` | Needs screenshots | 4 | 1. Stripe settings page in Impostazioni. 2. Stripe Connect onboarding. 3. Online payment link (athlete view). 4. Stripe payment confirmation. |
| `come-impostare-automazioni-workflow.mdx` | Needs screenshots | 3 | 1. Workflow list view. 2. Workflow builder with trigger and blocks. 3. Workflow execution log/history. |

---

## Summary

| Category | Total Pages | With Screenshots | Needs Screenshots | Total Screenshots Needed |
|----------|------------|-----------------|-------------------|-------------------------|
| Docs | 17 | 1 | 14 | 38 |
| FAQ | 17 | 1 | 14 | 29 |
| Tutorials | 9 | 2 | 7 | 22 |
| **Total** | **43** | **4** | **35** | **89** |

## Tips for Taking Screenshots

1. Use a clean browser window (no bookmarks bar, no extensions visible)
2. Use the light theme (default) for consistency
3. Populate the account with realistic sample data (Italian names, real-looking courses)
4. Acquisire con viewport 1920 × 1080 e scala dispositivo 1; conservare sempre l’originale Full HD
5. Crop to show only the relevant section, not the entire browser window
6. If a step-by-step guide has 3+ steps, consider taking a screenshot for each key step
7. Blur or redact any real personal data (email addresses, phone numbers, etc.)
