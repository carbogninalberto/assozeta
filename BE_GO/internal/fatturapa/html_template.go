// Code generated from generate_invoice_html (BE/application/serializers/invoice_serializers.py); DO NOT EDIT.

package fatturapa

// htmlParts are the literal text around htmlSlots: parts[0] slots[0] parts[1] ... parts[n].
var htmlParts = []string{
	`<!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="Content-Type" content="text/html;charset=UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Fattura Elettronica `,
	`</title>
        <style>
            /* Reset and base styles */
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

            body {
                font-family: Arial, sans-serif;
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
                line-height: 1.4;
                color: #000;
                margin: 0;
                padding: 0;
            }

            /* A4 page layout */
            .stampa {
                margin: 0;
                padding: 0.5cm 0.5cm;
                width: 21cm;
                min-height: 29.7cm;
            }
            
            @page {
                size: A4;
                margin: 0; /* Reset margini pagina */
            }

            /* Print optimization */
            @media print {
                body {
                    -webkit-print-color-adjust: exact;
                    print-color-adjust: exact;
                }

                .stampa {
                    width: 19cm;
                    margin: auto;
                    min-height: 27cm;
                    padding: 0.5cm 0.5cm;
                }

                .page-break-before {
                    page-break-before: always;
                    break-before: always;
                }

                .page-break-after {
                    page-break-after: always;
                    break-after: always;
                }

                div {
                    break-inside: avoid;
                }
            }

            /* Header styles */
            .header {
                border-bottom: 2pt solid #333;
                padding-bottom: 15px;
                margin-bottom: 20px;
                width: 100%;
            }

            /* Grid system */
            .columns {
                display: flex;
                flex-wrap: wrap;
                width: 100%;
                gap: 20px;
            }

            .column {
                flex: 1;
            }

            .is-6 {
                flex: 0 0 calc(50% - 10px);
            }

            .is-8 {
                flex: 0 0 calc(66.666% - 10px);
            }

            .is-4 {
                flex: 0 0 calc(33.333% - 10px);
            }

            .is-12 {
                flex: 0 0 100%;
            }

            /* Typography */
            h1 {
                font-size: 14pt;
                margin: 0;
                font-weight: bold;
            }

            h2 {
                font-size: 11pt;
                margin: 0;
                font-weight: bold;
            }

            h3 {
                font-size: 10pt;
                margin: 0 0 10px 0;
                font-weight: bold;
                border-bottom: 1px solid #ccc;
                padding-bottom: 5px;
            }

            p, .is-a-cell {
                font-size: 9pt;
                margin: 5px 0;
            }

            /* Company info sections */
            .company-info {
                font-size: 9pt;
            }

            .invoice-info {
                text-align: right;
                font-size: 9pt;
            }

            /* Section styles */
            .section {
                margin: 20px 0;
                padding: 10px 0;
            }

            .section-box {
                border: 1px solid #ddd;
                padding: 15px;
                margin: 10px 0;
                background: #fafafa;
            }

            /* Table styles */
            table {
                width: 100%;
                border-collapse: collapse;
                margin: 15px 0;
                table-layout: fixed;
                font-size: 9pt;
            }

            th, td {
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
            }

            th {
                background-color: #f5f5f5;
                font-weight: bold;
            }

            /* Alignment utilities */
            .text-right {
                text-align: right !important;
            }

            .text-center {
                text-align: center !important;
            }

            .text-left {
                text-align: left !important;
            }

            /* Amount columns */
            .amount {
                text-align: right;
                white-space: nowrap;
            }

            /* Total row */
            .total {
                font-weight: bold;
                background-color: #f9f9f9;
                font-size: 10pt;
            }

            .total-finale {
                font-size: 11pt;
                font-weight: bold;
                margin: 20px 0;
                padding: 10px;
                background-color: #f0f0f0;
                text-align: right;
            }

            /* Footer */
            .footer {
                margin-top: 30px;
                border-top: 1px solid #ccc;
                padding-top: 15px;
                font-size: 8pt;
                text-align: center;
            }

            /* Info box */
            .info-box {
                background: #f9f9f9;
                border: 1px solid #e0e0e0;
                padding: 10px;
                margin: 10px 0;
                font-size: 9pt;
            }

            .info-box strong {
                display: inline-block;
                min-width: 120px;
            }

            /* Clear float */
            .clear {
                clear: both;
            }
        </style>
    </head>
    <body>
        <div class="stampa">
            <div class="header">
                <div class="columns">
                    <div class="column is-6">
                        <div class="company-info">
                            <h2>`,
	`</h2>
                            <p>`,
	`<br>
                            `,
	` `,
	` (`,
	`)<br>
                            `,
	`</p>
                            `,
	`
                            `,
	`
                        </div>
                    </div>
                    <div class="column is-6">
                        <div class="invoice-info">
                            <h1>FATTURA ELETTRONICA</h1>
                            <p><strong>Numero:</strong> `,
	`</p>
                            <p><strong>Data:</strong> `,
	`</p>
                            <p><strong>Tipo documento:</strong> `,
	`</p>
                        </div>
                    </div>
                </div>
            </div>

            <div class="section">
                <h3>DATI DEL CLIENTE</h3>
                <div class="section-box">
                    <div class="columns">
                        <div class="column is-12">
                            <p><strong>`,
	`</strong></p>
                            <p>`,
	`<br>
                            `,
	` `,
	` (`,
	`)<br>
                            `,
	`</p>
                            <div class="columns" style="margin-top: 10px;">
                                <div class="column is-6">
                                    `,
	`
                                </div>
                                <div class="column is-6">
                                    `,
	`
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="section">
                <h3>DETTAGLIO BENI/SERVIZI</h3>
                <table>
                    <thead>
                        <tr>
                            <th style="width: 40%">Descrizione</th>
                            <th style="width: 10%" class="text-center">Quantità</th>
                            <th style="width: 10%" class="text-center">Unità</th>
                            <th style="width: 15%" class="amount">Prezzo Unit.</th>
                            <th style="width: 10%" class="text-center">IVA %</th>
                            <th style="width: 15%" class="amount">Totale</th>
                        </tr>
                    </thead>
                    <tbody>`,
	`
                    </tbody>
                </table>
            </div>

            <div class="section">
                <h3>RIEPILOGO IVA E TOTALI</h3>
                <div class="columns">
                    <div class="column is-8">
                        <table>
                            <thead>
                                <tr>
                                    <th style="width: 30%">Aliquota IVA</th>
                                    <th style="width: 35%" class="amount">Imponibile</th>
                                    <th style="width: 35%" class="amount">Imposta</th>
                                </tr>
                            </thead>
                            <tbody>`,
	`
                            </tbody>
                        </table>
                    </div>
                    <div class="column is-4">
                        <div class="info-box">
                            <p><strong>Totale Imponibile:</strong> € `,
	`</p>
                            <p><strong>Totale IVA:</strong> € `,
	`</p>
                            <div style="border-top: 2px solid #333; margin: 10px 0; padding-top: 10px;">
                                <p style="font-size: 11pt;"><strong>TOTALE FATTURA:</strong> € `,
	`</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            `,
	`

            <div class="footer">
                <p>Fattura generata elettronicamente in conformità alle disposizioni di legge vigenti.<br>
                <small>Documento privo di valore fiscale ai sensi del DPR 633/72 e successive modifiche.</small></p>
            </div>
        </div>

        <script>
            // Gestione layout per stampa
            window.addEventListener('beforeprint', function() {
                const tables = document.querySelectorAll('table');
                tables.forEach(table => {
                    const rect = table.getBoundingClientRect();
                    const pageHeight = 1122; // A4 height in pixels at 96dpi

                    if (rect.bottom > pageHeight && rect.top < pageHeight) {
                        table.style.pageBreakBefore = 'always';
                    }
                });
            });
        </script>
    </body>
    </html>`,
}

var htmlSlots = []string{
	"body.dati_generali.dati_generali_documento.numero",
	"cedente.dati_anagrafici.anagrafica.denominazione",
	"cedente.sede.indirizzo",
	"cedente.sede.cap",
	"cedente.sede.comune",
	"cedente.sede.provincia",
	"cedente.sede.nazione",
	"optional:cedente_piva|<p><strong>P.IVA:</strong> |</p>",
	"optional:cedente_cf|<p><strong>Codice Fiscale:</strong> |</p>",
	"body.dati_generali.dati_generali_documento.numero",
	"body.dati_generali.dati_generali_documento.data",
	"body.dati_generali.dati_generali_documento.tipo_documento",
	"cessionario.dati_anagrafici.anagrafica.denominazione",
	"cessionario.sede.indirizzo",
	"cessionario.sede.cap",
	"cessionario.sede.comune",
	"cessionario.sede.provincia",
	"cessionario.sede.nazione",
	"optional:cessionario_piva|<p><strong>P.IVA:</strong> |</p>",
	"optional:cessionario_cf|<p><strong>Codice Fiscale:</strong> |</p>",
	"linee_html",
	"riepiloghi_html",
	"totale_imponibile:.2f",
	"totale_imposta:.2f",
	"body.dati_generali.dati_generali_documento.importo_totale_documento:.2f",
	"pagamenti_html",
}
