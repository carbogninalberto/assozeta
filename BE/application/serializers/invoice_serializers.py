import json

import a38
import xml.etree.ElementTree as ET
from rest_framework import serializers

from application.models.invoices_models import Invoice, InvoiceSuppliers, CustomerInvoice
from application.serializers.payment_serializers import PaymentSerializer, SupplierSerializer


class InvoiceSerializer(serializers.ModelSerializer):
    document_token = serializers.SerializerMethodField()
    def create(self, validated_data):
        instance = Invoice.objects.create(**validated_data)
        return instance

    class Meta:
        model = Invoice
        fields = '__all__'

    def get_document_token(self, obj):
        # check document exists
        doc = None
        try:
            doc = obj.document_pdf.token
        except Exception as e:
            pass
        return doc


class InvoiceSuppliersSerializer(serializers.ModelSerializer):
    payment = PaymentSerializer(required=False, allow_null=True)
    payment_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    document_token = serializers.SerializerMethodField()
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    supplier = SupplierSerializer(required=False, allow_null=False)

    def create(self, validated_data):
        instance = InvoiceSuppliers.objects.create(**validated_data)
        return instance

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = InvoiceSuppliers
        fields = '__all__'

    def get_document_token(self, obj):
        # check document exists
        if obj.document_pdf is None:
            return None
        return obj.document_pdf.token


# create Serializer for CustomerInvoice
class CustomerInvoiceSerializer(serializers.ModelSerializer):

    pdf_token = serializers.SerializerMethodField()

    def get_pdf_token(self, obj):
        """
        Returns the token of the PDF document associated with the CustomerInvoice.
        If the document does not exist, returns None.
        """
        if obj.pdf:
            return obj.pdf.token
        return None

    # validate on create
    def validate(self, data):
        if 'id_transmitter' not in data:
            data['id_transmitter'] = None
        if 'transferor_contact_email' not in data:
            data['transferor_contact_email'] = None
        if data['transferor_contact_email'] is None and data['id_transmitter'] is None:
            raise serializers.ValidationError("Either transferor_contact_email or id_transmitter must be provided!")
        # check if the invoice is already present
        if CustomerInvoice.objects.filter(number=data['number'], fiscal_year=data['fiscal_year']).exists():
            raise serializers.ValidationError("Invoice already exists!")
        return data

    class Meta:
        model = CustomerInvoice
        fields = '__all__'

def generate_einvoice(invoice: CustomerInvoice):
    invoice_number = f'{invoice.prefix} {invoice.number}/{invoice.fiscal_year}'

    if invoice.assignor_prefix_vat_number and invoice.assignor_vat_number:
        assignor_id_fiscale_iva = a38.IdFiscaleIVA(
            invoice.assignor_prefix_vat_number,
            invoice.assignor_vat_number
        )
    else:
        assignor_id_fiscale_iva = None

    assignor = a38.CedentePrestatore(
        a38.DatiAnagraficiCedentePrestatore(
            id_fiscale_iva=assignor_id_fiscale_iva,
            codice_fiscale=invoice.assignor_tax_code,
            anagrafica=a38.Anagrafica(
                denominazione=invoice.assignor_denomination
            ),
            regime_fiscale=invoice.assignor_fiscal_regime
        ),
        a38.Sede(
            indirizzo=invoice.assignor_address,
            cap=invoice.assignor_postal_code,
            comune=invoice.assignor_city,
            provincia=invoice.assignor_province,
            nazione=invoice.assignor_country
        ),
        a38.Contatti(
            fax=invoice.assignor_fax,
            telefono=invoice.assignor_contact_phone,
            email=invoice.assignor_contact_email
        )
    )

    if invoice.transferor_prefix_vat_number and invoice.transferor_vat_number:
        transferor_id_fiscale_iva = a38.IdFiscaleIVA(
            invoice.transferor_prefix_vat_number,
            invoice.transferor_vat_number
        )
    else:
        transferor_id_fiscale_iva = None

    transferor = a38.CessionarioCommittente(
        a38.DatiAnagraficiCessionarioCommittente(
            id_fiscale_iva=transferor_id_fiscale_iva,
            codice_fiscale=invoice.transferor_tax_code,
            anagrafica=a38.Anagrafica(
                denominazione=invoice.transferor_denomination
            ),
            regime_fiscale=invoice.transferor_fiscal_regime
        ),
        a38.Sede(
            indirizzo=invoice.transferor_address,
            cap=invoice.transferor_postal_code,
            comune=invoice.transferor_city,
            provincia=invoice.transferor_province,
            nazione=invoice.transferor_country
        ),
        a38.Contatti(
            fax=invoice.transferor_fax,
            telefono=invoice.transferor_contact_phone,
            email=invoice.transferor_contact_email
        )
    )

    # Create a new invoice using a38
    generated_invoice = a38.FatturaPrivati12()

    generated_invoice.fattura_elettronica_header.dati_trasmissione.id_trasmittente = a38.IdTrasmittente(
        invoice.country_transmitter_prefix,
        invoice.id_transmitter if invoice.id_transmitter else '0000000'
    )
    generated_invoice.fattura_elettronica_header.dati_trasmissione.pec_destinatario = \
        invoice.transferor_contact_email if invoice.transferor_contact_email else None
    generated_invoice.fattura_elettronica_header.cedente_prestatore = assignor
    generated_invoice.fattura_elettronica_header.cessionario_committente = transferor

    body = generated_invoice.fattura_elettronica_body[0]
    body.dati_generali.dati_generali_documento = a38.DatiGeneraliDocumento(
        formato_trasmissione=invoice.transmitting_format,
        tipo_documento=invoice.document_type,
        divisa=invoice.currency,
        data=str(invoice.transmitting_date).format('%d/%m/%Y'),
        numero=invoice_number,
        causale=[invoice.causal],
    )

    for line in invoice.lines:
        # add line to the invoice
        body.dati_beni_servizi.add_dettaglio_linee(
            descrizione=line['description'],
            quantita=line['quantity'],
            unita_misura=line['unit_of_measure'],
            prezzo_unitario=line['unit_price'],
            aliquota_iva=line['vat'] if line['vat'] else "22.0"
        )

    body.dati_beni_servizi.build_dati_riepilogo()
    body.build_importo_totale_documento()

    body.dati_pagamento = [
        a38.DatiPagamento(
            condizioni_pagamento=invoice.payment_condition,
            dettaglio_pagamento=[
                a38.DettaglioPagamento(
                    modalita_pagamento=invoice.payment_modality,
                    importo_pagamento=body.dati_generali.dati_generali_documento.importo_totale_documento,
                    data_scadenza_pagamento=str(invoice.payment_expiry_date).format('%dd/%mm/%YYYY')
                )
            ]
        )
    ]

    res = a38.validation.Validation()
    generated_invoice.validate(res)
    if res.warnings:
        for w in res.warnings:
            print(str(w))
    if res.errors:
        for e in res.errors:
            print(str(e))
        raise Exception("Errori nella validazione del file XML")

    return generated_invoice


def generate_invoice_xml(invoice: CustomerInvoice):
    """
    This function generates the xml for the invoice
    :param invoice:
    :return:
    """

    generated_invoice = generate_einvoice(invoice)

    tree = generated_invoice.build_etree()
    # convert the xml tree to string
    xml_string = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)

    return xml_string.decode("utf-8")


def generate_invoice_json(invoice: CustomerInvoice):
    """
    Genera rappresentazione JSON della fattura elettronica
    :param invoice: Oggetto CustomerInvoice
    :return: Stringa JSON della fattura
    """
    generated_invoice = generate_einvoice(invoice)

    # Helper function per gestire valori None
    def safe_get(obj, attr, default=None):
        return getattr(obj, attr, default) if obj else default

    # Helper per formattare date in ISO format
    def format_date_iso(date_str):
        if not date_str:
            return None
        try:
            # Assumendo formato YYYY-MM-DD, convertire in ISO con timezone
            return f"{date_str}T00:00:00Z"
        except ValueError:
            return date_str

    # Estrae dati dall'oggetto a38 per conversione JSON
    invoice_data = {
        "fattura_elettronica_header": {
            "dati_trasmissione": {
                "id_trasmittente": {
                    "id_paese": generated_invoice.fattura_elettronica_header.dati_trasmissione.id_trasmittente.id_paese,
                    "id_codice": generated_invoice.fattura_elettronica_header.dati_trasmissione.id_trasmittente.id_codice
                },
                "progressivo_invio": generated_invoice.fattura_elettronica_header.dati_trasmissione.progressivo_invio,
                "formato_trasmissione": generated_invoice.fattura_elettronica_header.dati_trasmissione.formato_trasmissione,
                "codice_destinatario": safe_get(generated_invoice.fattura_elettronica_header.dati_trasmissione,
                                                'codice_destinatario'),
                "contatti_trasmittente": {
                    "telefono": safe_get(safe_get(generated_invoice.fattura_elettronica_header.dati_trasmissione,
                                                  'contatti_trasmittente'), 'telefono'),
                    "email": safe_get(safe_get(generated_invoice.fattura_elettronica_header.dati_trasmissione,
                                               'contatti_trasmittente'), 'email')
                } if hasattr(generated_invoice.fattura_elettronica_header.dati_trasmissione,
                             'contatti_trasmittente') else None,
                "pec_destinatario": safe_get(generated_invoice.fattura_elettronica_header.dati_trasmissione,
                                             'pec_destinatario')
            },
            "cedente_prestatore": {
                "dati_anagrafici": {
                    "id_fiscale_iva": {
                        "id_paese": safe_get(
                            safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici,
                                     'id_fiscale_iva'), 'id_paese'),
                        "id_codice": safe_get(
                            safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici,
                                     'id_fiscale_iva'), 'id_codice')
                    } if hasattr(generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici,
                                 'id_fiscale_iva') else None,
                    "codice_fiscale": safe_get(
                        generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici,
                        'codice_fiscale'),
                    "anagrafica": {
                        "denominazione": safe_get(
                            generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici.anagrafica,
                            'denominazione'),
                        "nome": safe_get(
                            generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici.anagrafica,
                            'nome'),
                        "cognome": safe_get(
                            generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici.anagrafica,
                            'cognome'),
                        "titolo": safe_get(
                            generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici.anagrafica,
                            'titolo'),
                        "cod_eori": safe_get(
                            generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici.anagrafica,
                            'cod_eori')
                    },
                    "albo_professionale": safe_get(
                        generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici,
                        'albo_professionale'),
                    "provincia_albo": safe_get(
                        generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici,
                        'provincia_albo'),
                    "numero_iscrizione_albo": safe_get(
                        generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici,
                        'numero_iscrizione_albo'),
                    "data_iscrizione_albo": format_date_iso(
                        safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici,
                                 'data_iscrizione_albo')),
                    "regime_fiscale": generated_invoice.fattura_elettronica_header.cedente_prestatore.dati_anagrafici.regime_fiscale
                },
                "sede": {
                    "indirizzo": generated_invoice.fattura_elettronica_header.cedente_prestatore.sede.indirizzo,
                    "numero_civico": safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore.sede,
                                              'numero_civico'),
                    "cap": generated_invoice.fattura_elettronica_header.cedente_prestatore.sede.cap,
                    "comune": generated_invoice.fattura_elettronica_header.cedente_prestatore.sede.comune,
                    "provincia": generated_invoice.fattura_elettronica_header.cedente_prestatore.sede.provincia,
                    "nazione": generated_invoice.fattura_elettronica_header.cedente_prestatore.sede.nazione
                },
                "stabile_organizzazione": None,  # Aggiungi logica se necessario
                "iscrizione_rea": {
                    "ufficio": safe_get(
                        safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore, 'iscrizione_rea'),
                        'ufficio'),
                    "numero_rea": safe_get(
                        safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore, 'iscrizione_rea'),
                        'numero_rea'),
                    "capitale_sociale": safe_get(
                        safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore, 'iscrizione_rea'),
                        'capitale_sociale'),
                    "socio_unico": safe_get(
                        safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore, 'iscrizione_rea'),
                        'socio_unico'),
                    "stato_liquidazione": safe_get(
                        safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore, 'iscrizione_rea'),
                        'stato_liquidazione')
                } if hasattr(generated_invoice.fattura_elettronica_header.cedente_prestatore,
                             'iscrizione_rea') else None,
                "contatti": {
                    "telefono": safe_get(
                        safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore, 'contatti'),
                        'telefono'),
                    "fax": safe_get(
                        safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore, 'contatti'), 'fax'),
                    "email": safe_get(
                        safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore, 'contatti'), 'email')
                } if hasattr(generated_invoice.fattura_elettronica_header.cedente_prestatore, 'contatti') else None,
                "riferimento_amministrazione": safe_get(generated_invoice.fattura_elettronica_header.cedente_prestatore,
                                                        'riferimento_amministrazione')
            },
            "rappresentante_fiscale": None,  # Aggiungi se necessario
            "cessionario_committente": {
                "dati_anagrafici": {
                    "id_fiscale_iva": {
                        "id_paese": safe_get(safe_get(
                            generated_invoice.fattura_elettronica_header.cessionario_committente.dati_anagrafici,
                            'id_fiscale_iva'), 'id_paese'),
                        "id_codice": safe_get(safe_get(
                            generated_invoice.fattura_elettronica_header.cessionario_committente.dati_anagrafici,
                            'id_fiscale_iva'), 'id_codice')
                    } if hasattr(generated_invoice.fattura_elettronica_header.cessionario_committente.dati_anagrafici,
                                 'id_fiscale_iva') else None,
                    "codice_fiscale": safe_get(
                        generated_invoice.fattura_elettronica_header.cessionario_committente.dati_anagrafici,
                        'codice_fiscale'),
                    "anagrafica": {
                        "denominazione": safe_get(
                            generated_invoice.fattura_elettronica_header.cessionario_committente.dati_anagrafici.anagrafica,
                            'denominazione'),
                        "nome": safe_get(
                            generated_invoice.fattura_elettronica_header.cessionario_committente.dati_anagrafici.anagrafica,
                            'nome'),
                        "cognome": safe_get(
                            generated_invoice.fattura_elettronica_header.cessionario_committente.dati_anagrafici.anagrafica,
                            'cognome'),
                        "titolo": safe_get(
                            generated_invoice.fattura_elettronica_header.cessionario_committente.dati_anagrafici.anagrafica,
                            'titolo'),
                        "cod_eori": safe_get(
                            generated_invoice.fattura_elettronica_header.cessionario_committente.dati_anagrafici.anagrafica,
                            'cod_eori')
                    }
                },
                "sede": {
                    "indirizzo": generated_invoice.fattura_elettronica_header.cessionario_committente.sede.indirizzo,
                    "numero_civico": safe_get(generated_invoice.fattura_elettronica_header.cessionario_committente.sede,
                                              'numero_civico'),
                    "cap": generated_invoice.fattura_elettronica_header.cessionario_committente.sede.cap,
                    "comune": generated_invoice.fattura_elettronica_header.cessionario_committente.sede.comune,
                    "provincia": generated_invoice.fattura_elettronica_header.cessionario_committente.sede.provincia,
                    "nazione": generated_invoice.fattura_elettronica_header.cessionario_committente.sede.nazione
                },
                "stabile_organizzazione": None,
                "rappresentante_fiscale": None
            },
            "terzo_intermediario_o_soggetto_emittente": None,
            "soggetto_emittente": safe_get(generated_invoice.fattura_elettronica_header, 'soggetto_emittente')
        },
        "fattura_elettronica_body": []
    }

    # Estrae il body della fattura
    for body in generated_invoice.fattura_elettronica_body:
        body_data = {
            "dati_generali": {
                "dati_generali_documento": {
                    "tipo_documento": body.dati_generali.dati_generali_documento.tipo_documento,
                    "divisa": body.dati_generali.dati_generali_documento.divisa,
                    "data": format_date_iso(body.dati_generali.dati_generali_documento.data),
                    "numero": body.dati_generali.dati_generali_documento.numero,
                    "dati_ritenuta": [],  # Aggiungi logica se necessario
                    "dati_bollo": None,  # Aggiungi logica se necessario
                    "dati_cassa_previdenziale": [],  # Aggiungi logica se necessario
                    "sconto_maggiorazione": [],  # Aggiungi logica se necessario
                    "importo_totale_documento": str(
                        body.dati_generali.dati_generali_documento.importo_totale_documento),
                    "arrotondamento": safe_get(body.dati_generali.dati_generali_documento, 'arrotondamento'),
                    "causale": body.dati_generali.dati_generali_documento.causale if isinstance(
                        body.dati_generali.dati_generali_documento.causale, list) else [
                        body.dati_generali.dati_generali_documento.causale] if body.dati_generali.dati_generali_documento.causale else [],
                    "art73": safe_get(body.dati_generali.dati_generali_documento, 'art73')
                },
                "dati_ordine_acquisto": [],
                "dati_contratto": [],
                "dati_convenzione": [],
                "dati_ricezione": [],
                "dati_fatture_collegate": [],
                "dati_sal": [],
                "dati_ddt": [],
                "dati_trasporto": None,
                "fattura_principale": None
            },
            "dati_beni_servizi": {
                "dettaglio_linee": [],
                "dati_riepilogo": []
            },
            "dati_veicoli": None,
            "dati_pagamento": [],
            "allegati": []
        }

        # Linee di dettaglio
        for linea in body.dati_beni_servizi.dettaglio_linee:
            linea_data = {
                "numero_linea": linea.numero_linea,
                "tipo_cessione_prestazione": safe_get(linea, 'tipo_cessione_prestazione'),
                "codice_articolo": [],
                "descrizione": linea.descrizione,
                "quantita": str(linea.quantita),
                "unita_misura": safe_get(linea, 'unita_misura'),
                "data_inizio_periodo": format_date_iso(safe_get(linea, 'data_inizio_periodo')),
                "data_fine_periodo": format_date_iso(safe_get(linea, 'data_fine_periodo')),
                "prezzo_unitario": str(linea.prezzo_unitario),
                "sconto_maggiorazione": [],
                "prezzo_totale": str(linea.prezzo_totale),
                "aliquota_iva": str(linea.aliquota_iva),
                "ritenuta": safe_get(linea, 'ritenuta'),
                "natura": safe_get(linea, 'natura'),
                "riferimento_amministrazione": safe_get(linea, 'riferimento_amministrazione'),
                "altri_dati_gestionali": []
            }
            body_data["dati_beni_servizi"]["dettaglio_linee"].append(linea_data)

        # Dati riepilogo
        for riepilogo in body.dati_beni_servizi.dati_riepilogo:
            riepilogo_data = {
                "aliquota_iva": str(riepilogo.aliquota_iva),
                "natura": safe_get(riepilogo, 'natura'),
                "spese_accessorie": safe_get(riepilogo, 'spese_accessorie'),
                "arrotondamento": safe_get(riepilogo, 'arrotondamento'),
                "imponibile_importo": str(riepilogo.imponibile_importo),
                "imposta": str(riepilogo.imposta),
                "esigibilita_iva": safe_get(riepilogo, 'esigibilita_iva'),
                "riferimento_normativo": safe_get(riepilogo, 'riferimento_normativo')
            }
            body_data["dati_beni_servizi"]["dati_riepilogo"].append(riepilogo_data)

        # Dati pagamento
        if hasattr(body, 'dati_pagamento') and body.dati_pagamento:
            for pagamento in body.dati_pagamento:
                pagamento_data = {
                    "condizioni_pagamento": pagamento.condizioni_pagamento,
                    "dettaglio_pagamento": []
                }
                for dettaglio in pagamento.dettaglio_pagamento:
                    dettaglio_data = {
                        "beneficiario": safe_get(dettaglio, 'beneficiario'),
                        "modalita_pagamento": dettaglio.modalita_pagamento,
                        "data_riferimento_termini_pagamento": format_date_iso(
                            safe_get(dettaglio, 'data_riferimento_termini_pagamento')),
                        "giorni_termini_pagamento": safe_get(dettaglio, 'giorni_termini_pagamento'),
                        "data_scadenza_pagamento": format_date_iso(dettaglio.data_scadenza_pagamento),
                        "importo_pagamento": str(dettaglio.importo_pagamento),
                        "cod_ufficio_postale": safe_get(dettaglio, 'cod_ufficio_postale'),
                        "cognome_quietanzante": safe_get(dettaglio, 'cognome_quietanzante'),
                        "nome_quietanzante": safe_get(dettaglio, 'nome_quietanzante'),
                        "cf_quietanzante": safe_get(dettaglio, 'cf_quietanzante'),
                        "titolo_quietanzante": safe_get(dettaglio, 'titolo_quietanzante'),
                        "istituto_finanziario": safe_get(dettaglio, 'istituto_finanziario'),
                        "iban": safe_get(dettaglio, 'iban'),
                        "abi": safe_get(dettaglio, 'abi'),
                        "cab": safe_get(dettaglio, 'cab'),
                        "bic": safe_get(dettaglio, 'bic'),
                        "sconto_pagamento_anticipato": safe_get(dettaglio, 'sconto_pagamento_anticipato'),
                        "data_limite_pagamento_anticipato": format_date_iso(
                            safe_get(dettaglio, 'data_limite_pagamento_anticipato')),
                        "penalita_pagamenti_ritardati": safe_get(dettaglio, 'penalita_pagamenti_ritardati'),
                        "data_decorrenza_penale": format_date_iso(safe_get(dettaglio, 'data_decorrenza_penale')),
                        "codice_pagamento": safe_get(dettaglio, 'codice_pagamento')
                    }
                    pagamento_data["dettaglio_pagamento"].append(dettaglio_data)
                body_data["dati_pagamento"].append(pagamento_data)

        invoice_data["fattura_elettronica_body"].append(body_data)

    # Rimuovi chiavi con valore None per pulizia (opzionale)
    def remove_none_values(d):
        if isinstance(d, dict):
            return {k: remove_none_values(v) for k, v in d.items() if v is not None}
        elif isinstance(d, list):
            return [remove_none_values(i) for i in d]
        else:
            return d

    # Se vuoi mantenere i None, commenta la riga seguente
    # invoice_data = remove_none_values(invoice_data)

    return json.dumps(invoice_data, indent=2, ensure_ascii=False)

def generate_invoice_html(invoice: CustomerInvoice):
    """
    This function generates the html for the invoice
    :param invoice:
    :return:
    """
    generated_invoice = generate_einvoice(invoice)

    # Estrae i dati necessari per il template
    body = generated_invoice.fattura_elettronica_body[0]

    # Linee di dettaglio
    linee_html = ""
    for linea in body.dati_beni_servizi.dettaglio_linee:
        linee_html += f"""
                <tr>
                    <td>{linea.descrizione}</td>
                    <td class="amount">{linea.quantita}</td>
                    <td>{linea.unita_misura}</td>
                    <td class="amount">€ {linea.prezzo_unitario:.2f}</td>
                    <td class="amount">{linea.aliquota_iva}%</td>
                    <td class="amount">€ {linea.prezzo_totale:.2f}</td>
                </tr>"""

    # Riepiloghi IVA
    riepiloghi_html = ""
    totale_imponibile = 0
    totale_imposta = 0
    for riepilogo in body.dati_beni_servizi.dati_riepilogo:
        riepiloghi_html += f"""
                <tr>
                    <td>{riepilogo.aliquota_iva}%</td>
                    <td class="amount">€ {riepilogo.imponibile_importo:.2f}</td>
                    <td class="amount">€ {riepilogo.imposta:.2f}</td>
                </tr>"""
        totale_imponibile += riepilogo.imponibile_importo
        totale_imposta += riepilogo.imposta

    # Pagamenti
    pagamenti_html = ""
    if body.dati_pagamento:
        pagamenti_html = '<div class="section"><h3>Condizioni di Pagamento</h3>'
        for pagamento in body.dati_pagamento:
            pagamenti_html += f'<p><strong>Condizioni:</strong> {pagamento.condizioni_pagamento}</p>'
            for dettaglio in pagamento.dettaglio_pagamento:
                pagamenti_html += f"""<p><strong>Modalità:</strong> {dettaglio.modalita_pagamento}<br>
                <strong>Importo:</strong> € {dettaglio.importo_pagamento:.2f}<br>"""
                if dettaglio.data_scadenza_pagamento:
                    pagamenti_html += f"<strong>Scadenza:</strong> {dettaglio.data_scadenza_pagamento}"
                pagamenti_html += "</p>"
        pagamenti_html += "</div>"

    # Dati cedente e cessionario
    cedente = generated_invoice.fattura_elettronica_header.cedente_prestatore
    cessionario = generated_invoice.fattura_elettronica_header.cessionario_committente

    cedente_piva = cedente.dati_anagrafici.id_fiscale_iva.id_codice if cedente.dati_anagrafici.id_fiscale_iva else ""
    cedente_cf = cedente.dati_anagrafici.codice_fiscale or ""

    cessionario_piva = cessionario.dati_anagrafici.id_fiscale_iva.id_codice if cessionario.dati_anagrafici.id_fiscale_iva else ""
    cessionario_cf = cessionario.dati_anagrafici.codice_fiscale or ""

    html_string = f"""<!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="Content-Type" content="text/html;charset=UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Fattura Elettronica {body.dati_generali.dati_generali_documento.numero}</title>
        <style>
            /* Reset and base styles */
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}

            body {{
                font-family: Arial, sans-serif;
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
                line-height: 1.4;
                color: #000;
                margin: 0;
                padding: 0;
            }}

            /* A4 page layout */
            .stampa {{
                margin: 0;
                padding: 0.5cm 0.5cm;
                width: 21cm;
                min-height: 29.7cm;
            }}
            
            @page {{
                size: A4;
                margin: 0; /* Reset margini pagina */
            }}

            /* Print optimization */
            @media print {{
                body {{
                    -webkit-print-color-adjust: exact;
                    print-color-adjust: exact;
                }}

                .stampa {{
                    width: 19cm;
                    margin: auto;
                    min-height: 27cm;
                    padding: 0.5cm 0.5cm;
                }}

                .page-break-before {{
                    page-break-before: always;
                    break-before: always;
                }}

                .page-break-after {{
                    page-break-after: always;
                    break-after: always;
                }}

                div {{
                    break-inside: avoid;
                }}
            }}

            /* Header styles */
            .header {{
                border-bottom: 2pt solid #333;
                padding-bottom: 15px;
                margin-bottom: 20px;
                width: 100%;
            }}

            /* Grid system */
            .columns {{
                display: flex;
                flex-wrap: wrap;
                width: 100%;
                gap: 20px;
            }}

            .column {{
                flex: 1;
            }}

            .is-6 {{
                flex: 0 0 calc(50% - 10px);
            }}

            .is-8 {{
                flex: 0 0 calc(66.666% - 10px);
            }}

            .is-4 {{
                flex: 0 0 calc(33.333% - 10px);
            }}

            .is-12 {{
                flex: 0 0 100%;
            }}

            /* Typography */
            h1 {{
                font-size: 14pt;
                margin: 0;
                font-weight: bold;
            }}

            h2 {{
                font-size: 11pt;
                margin: 0;
                font-weight: bold;
            }}

            h3 {{
                font-size: 10pt;
                margin: 0 0 10px 0;
                font-weight: bold;
                border-bottom: 1px solid #ccc;
                padding-bottom: 5px;
            }}

            p, .is-a-cell {{
                font-size: 9pt;
                margin: 5px 0;
            }}

            /* Company info sections */
            .company-info {{
                font-size: 9pt;
            }}

            .invoice-info {{
                text-align: right;
                font-size: 9pt;
            }}

            /* Section styles */
            .section {{
                margin: 20px 0;
                padding: 10px 0;
            }}

            .section-box {{
                border: 1px solid #ddd;
                padding: 15px;
                margin: 10px 0;
                background: #fafafa;
            }}

            /* Table styles */
            table {{
                width: 100%;
                border-collapse: collapse;
                margin: 15px 0;
                table-layout: fixed;
                font-size: 9pt;
            }}

            th, td {{
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
            }}

            th {{
                background-color: #f5f5f5;
                font-weight: bold;
            }}

            /* Alignment utilities */
            .text-right {{
                text-align: right !important;
            }}

            .text-center {{
                text-align: center !important;
            }}

            .text-left {{
                text-align: left !important;
            }}

            /* Amount columns */
            .amount {{
                text-align: right;
                white-space: nowrap;
            }}

            /* Total row */
            .total {{
                font-weight: bold;
                background-color: #f9f9f9;
                font-size: 10pt;
            }}

            .total-finale {{
                font-size: 11pt;
                font-weight: bold;
                margin: 20px 0;
                padding: 10px;
                background-color: #f0f0f0;
                text-align: right;
            }}

            /* Footer */
            .footer {{
                margin-top: 30px;
                border-top: 1px solid #ccc;
                padding-top: 15px;
                font-size: 8pt;
                text-align: center;
            }}

            /* Info box */
            .info-box {{
                background: #f9f9f9;
                border: 1px solid #e0e0e0;
                padding: 10px;
                margin: 10px 0;
                font-size: 9pt;
            }}

            .info-box strong {{
                display: inline-block;
                min-width: 120px;
            }}

            /* Clear float */
            .clear {{
                clear: both;
            }}
        </style>
    </head>
    <body>
        <div class="stampa">
            <div class="header">
                <div class="columns">
                    <div class="column is-6">
                        <div class="company-info">
                            <h2>{cedente.dati_anagrafici.anagrafica.denominazione}</h2>
                            <p>{cedente.sede.indirizzo}<br>
                            {cedente.sede.cap} {cedente.sede.comune} ({cedente.sede.provincia})<br>
                            {cedente.sede.nazione}</p>
                            {f'<p><strong>P.IVA:</strong> {cedente_piva}</p>' if cedente_piva else ''}
                            {f'<p><strong>Codice Fiscale:</strong> {cedente_cf}</p>' if cedente_cf else ''}
                        </div>
                    </div>
                    <div class="column is-6">
                        <div class="invoice-info">
                            <h1>FATTURA ELETTRONICA</h1>
                            <p><strong>Numero:</strong> {body.dati_generali.dati_generali_documento.numero}</p>
                            <p><strong>Data:</strong> {body.dati_generali.dati_generali_documento.data}</p>
                            <p><strong>Tipo documento:</strong> {body.dati_generali.dati_generali_documento.tipo_documento}</p>
                        </div>
                    </div>
                </div>
            </div>

            <div class="section">
                <h3>DATI DEL CLIENTE</h3>
                <div class="section-box">
                    <div class="columns">
                        <div class="column is-12">
                            <p><strong>{cessionario.dati_anagrafici.anagrafica.denominazione}</strong></p>
                            <p>{cessionario.sede.indirizzo}<br>
                            {cessionario.sede.cap} {cessionario.sede.comune} ({cessionario.sede.provincia})<br>
                            {cessionario.sede.nazione}</p>
                            <div class="columns" style="margin-top: 10px;">
                                <div class="column is-6">
                                    {f'<p><strong>P.IVA:</strong> {cessionario_piva}</p>' if cessionario_piva else ''}
                                </div>
                                <div class="column is-6">
                                    {f'<p><strong>Codice Fiscale:</strong> {cessionario_cf}</p>' if cessionario_cf else ''}
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
                    <tbody>{linee_html}
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
                            <tbody>{riepiloghi_html}
                            </tbody>
                        </table>
                    </div>
                    <div class="column is-4">
                        <div class="info-box">
                            <p><strong>Totale Imponibile:</strong> € {totale_imponibile:.2f}</p>
                            <p><strong>Totale IVA:</strong> € {totale_imposta:.2f}</p>
                            <div style="border-top: 2px solid #333; margin: 10px 0; padding-top: 10px;">
                                <p style="font-size: 11pt;"><strong>TOTALE FATTURA:</strong> € {body.dati_generali.dati_generali_documento.importo_totale_documento:.2f}</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {pagamenti_html}

            <div class="footer">
                <p>Fattura generata elettronicamente in conformità alle disposizioni di legge vigenti.<br>
                <small>Documento privo di valore fiscale ai sensi del DPR 633/72 e successive modifiche.</small></p>
            </div>
        </div>

        <script>
            // Gestione layout per stampa
            window.addEventListener('beforeprint', function() {{
                const tables = document.querySelectorAll('table');
                tables.forEach(table => {{
                    const rect = table.getBoundingClientRect();
                    const pageHeight = 1122; // A4 height in pixels at 96dpi

                    if (rect.bottom > pageHeight && rect.top < pageHeight) {{
                        table.style.pageBreakBefore = 'always';
                    }}
                }});
            }});
        </script>
    </body>
    </html>"""

    return html_string