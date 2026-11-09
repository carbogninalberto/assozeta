package fatturapa

import (
	"fmt"
	"strconv"
	"strings"
)

// ordered is a Python dict in insertion order; values are string, *string,
// nil, int, []any or ordered.
type ordered []field

type field struct {
	key   string
	value any
}

func nulls(keys ...string) ordered {
	out := make(ordered, len(keys))
	for i, key := range keys {
		out[i] = field{key, nil}
	}
	return out
}

func str(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

func partyAnagrafica(p party) ordered {
	return append(ordered{{"denominazione", *p.denomination}}, nulls("nome", "cognome", "titolo", "cod_eori")...)
}

func partyVAT(p party) ordered {
	return ordered{{"id_paese", str(p.vatCountry)}, {"id_codice", str(p.vatCode)}}
}

func partySede(p party) ordered {
	return ordered{{"indirizzo", p.address}, {"numero_civico", nil}, {"cap", p.cap}, {"comune", p.city},
		{"provincia", p.province}, {"nazione", p.country}}
}

// JSON is generate_invoice_json with ProgressivoInvio set to progressivo.
func JSON(inv Invoice, progressivo string) (string, error) {
	f, err := build(inv, progressivo)
	if err != nil {
		return "", err
	}
	causal := []any{}
	for _, c := range f.causal {
		causal = append(causal, c)
	}
	lines := []any{}
	for _, l := range f.lines {
		quantity := "None"
		if l.quantity != nil {
			quantity = l.quantity.String()
		}
		lines = append(lines, ordered{{"numero_linea", l.number}, {"tipo_cessione_prestazione", nil},
			{"codice_articolo", []any{}}, {"descrizione", l.description}, {"quantita", quantity}, {"unita_misura", str(l.unit)},
			{"data_inizio_periodo", nil}, {"data_fine_periodo", nil}, {"prezzo_unitario", l.unitPrice.String()},
			{"sconto_maggiorazione", []any{}}, {"prezzo_totale", l.total.String()}, {"aliquota_iva", l.vat.String()},
			{"ritenuta", nil}, {"natura", nil}, {"riferimento_amministrazione", nil}, {"altri_dati_gestionali", []any{}}})
	}
	summaries := []any{}
	for _, s := range f.summaries {
		summaries = append(summaries, ordered{{"aliquota_iva", s.vat.String()}, {"natura", nil}, {"spese_accessorie", nil},
			{"arrotondamento", nil}, {"imponibile_importo", s.taxable.String()}, {"imposta", s.tax.String()},
			{"esigibilita_iva", "I"}, {"riferimento_normativo", nil}})
	}
	payment := append(ordered{{"beneficiario", nil}, {"modalita_pagamento", f.modality},
		{"data_riferimento_termini_pagamento", nil}, {"giorni_termini_pagamento", nil},
		{"data_scadenza_pagamento", f.expiryText() + "T00:00:00Z"}, {"importo_pagamento", f.total.String()}},
		nulls("cod_ufficio_postale", "cognome_quietanzante", "nome_quietanzante", "cf_quietanzante", "titolo_quietanzante",
			"istituto_finanziario", "iban", "abi", "cab", "bic", "sconto_pagamento_anticipato", "data_limite_pagamento_anticipato",
			"penalita_pagamenti_ritardati", "data_decorrenza_penale", "codice_pagamento")...)
	cedente := ordered{
		{"dati_anagrafici", append(append(ordered{{"id_fiscale_iva", partyVAT(f.cedente)}, {"codice_fiscale", str(f.cedente.taxCode)},
			{"anagrafica", partyAnagrafica(f.cedente)}}, nulls("albo_professionale", "provincia_albo", "numero_iscrizione_albo",
			"data_iscrizione_albo")...), field{"regime_fiscale", f.cedente.regime})},
		{"sede", partySede(f.cedente)},
		{"stabile_organizzazione", nil},
		{"iscrizione_rea", nulls("ufficio", "numero_rea", "capitale_sociale", "socio_unico", "stato_liquidazione")},
		{"contatti", nulls("telefono", "fax", "email")},
		{"riferimento_amministrazione", nil},
	}
	cessionario := ordered{
		{"dati_anagrafici", ordered{{"id_fiscale_iva", partyVAT(f.cessionario)}, {"codice_fiscale", str(f.cessionario.taxCode)},
			{"anagrafica", partyAnagrafica(f.cessionario)}}},
		{"sede", partySede(f.cessionario)},
		{"stabile_organizzazione", nil},
		{"rappresentante_fiscale", nil},
	}
	document := ordered{{"tipo_documento", f.documentType}, {"divisa", f.currency}, {"data", f.dateText() + "T00:00:00Z"},
		{"numero", f.numero}, {"dati_ritenuta", []any{}}, {"dati_bollo", nil}, {"dati_cassa_previdenziale", []any{}},
		{"sconto_maggiorazione", []any{}}, {"importo_totale_documento", f.total.String()}, {"arrotondamento", nil},
		{"causale", causal}, {"art73", nil}}
	body := ordered{
		{"dati_generali", ordered{{"dati_generali_documento", document}, {"dati_ordine_acquisto", []any{}},
			{"dati_contratto", []any{}}, {"dati_convenzione", []any{}}, {"dati_ricezione", []any{}},
			{"dati_fatture_collegate", []any{}}, {"dati_sal", []any{}}, {"dati_ddt", []any{}}, {"dati_trasporto", nil},
			{"fattura_principale", nil}}},
		{"dati_beni_servizi", ordered{{"dettaglio_linee", lines}, {"dati_riepilogo", summaries}}},
		{"dati_veicoli", nil},
		{"dati_pagamento", []any{ordered{{"condizioni_pagamento", f.condition}, {"dettaglio_pagamento", []any{payment}}}}},
		{"allegati", []any{}},
	}
	root := ordered{
		{"fattura_elettronica_header", ordered{
			{"dati_trasmissione", ordered{
				{"id_trasmittente", ordered{{"id_paese", f.transmitterCountry}, {"id_codice", f.transmitterCode}}},
				{"progressivo_invio", f.progressivo}, {"formato_trasmissione", "FPR12"}, {"codice_destinatario", "0000000"},
				{"contatti_trasmittente", nulls("telefono", "email")}, {"pec_destinatario", f.pec}}},
			{"cedente_prestatore", cedente},
			{"rappresentante_fiscale", nil},
			{"cessionario_committente", cessionario},
			{"terzo_intermediario_o_soggetto_emittente", nil},
			{"soggetto_emittente", nil},
		}},
		{"fattura_elettronica_body", []any{body}},
	}
	var out strings.Builder
	writeJSON(&out, root, 0)
	return out.String(), nil
}

// writeJSON is json.dumps(indent=2, ensure_ascii=False).
func writeJSON(out *strings.Builder, value any, depth int) {
	indent := func(level int) string { return "\n" + strings.Repeat("  ", level) }
	switch v := value.(type) {
	case nil:
		out.WriteString("null")
	case string:
		out.WriteString(jsonString(v))
	case int:
		out.WriteString(strconv.Itoa(v))
	case []any:
		if len(v) == 0 {
			out.WriteString("[]")
			return
		}
		out.WriteString("[")
		for i, item := range v {
			if i > 0 {
				out.WriteString(",")
			}
			out.WriteString(indent(depth + 1))
			writeJSON(out, item, depth+1)
		}
		out.WriteString(indent(depth) + "]")
	case ordered:
		if len(v) == 0 {
			out.WriteString("{}")
			return
		}
		out.WriteString("{")
		for i, item := range v {
			if i > 0 {
				out.WriteString(",")
			}
			out.WriteString(indent(depth+1) + jsonString(item.key) + ": ")
			writeJSON(out, item.value, depth+1)
		}
		out.WriteString(indent(depth) + "}")
	default:
		panic(fmt.Sprintf("fatturapa: unexpected JSON value %T", value))
	}
}

func jsonString(text string) string {
	var out strings.Builder
	out.WriteByte('"')
	for _, r := range text {
		switch r {
		case '"':
			out.WriteString(`\"`)
		case '\\':
			out.WriteString(`\\`)
		case '\n':
			out.WriteString(`\n`)
		case '\r':
			out.WriteString(`\r`)
		case '\t':
			out.WriteString(`\t`)
		case '\b':
			out.WriteString(`\b`)
		case '\f':
			out.WriteString(`\f`)
		default:
			if r < 0x20 {
				fmt.Fprintf(&out, `\u%04x`, r)
			} else {
				out.WriteRune(r)
			}
		}
	}
	out.WriteByte('"')
	return out.String()
}
