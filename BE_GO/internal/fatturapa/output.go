package fatturapa

import (
	"fmt"
	"strconv"
	"strings"
)

const namespace = "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"

// Progressivo is ProgressivoInvioField._encode_b56((ts << 16) + sequence).
func Progressivo(unix int64, sequence int) string {
	const chars = "+-./0123456789=@ABCDEFGHIJKLMNOPQRSTUVWXYZ_"
	value := uint64(unix)<<16 + uint64(sequence)
	var out []byte
	for value > 0 {
		out = append([]byte{chars[value%43]}, out...)
		value /= 43
	}
	return string(out)
}

func isoDate(year, month, day int) string { return fmt.Sprintf("%04d-%02d-%02d", year, month, day) }

func (f *fattura) dateText() string {
	return isoDate(f.date.Year(), int(f.date.Month()), f.date.Day())
}

func (f *fattura) expiryText() string {
	return isoDate(f.expiry.Year(), int(f.expiry.Month()), f.expiry.Day())
}

// HTML is generate_invoice_html.
func HTML(inv Invoice) (string, error) {
	// The HTML does not show ProgressivoInvio; any valid value builds the same page.
	f, err := build(inv, "A")
	if err != nil {
		return "", err
	}
	var lines, summaries strings.Builder
	for _, l := range f.lines {
		quantity := "None"
		if l.quantity != nil {
			quantity = l.quantity.String()
		}
		fmt.Fprintf(&lines, `
                <tr>
                    <td>%s</td>
                    <td class="amount">%s</td>
                    <td>%s</td>
                    <td class="amount">€ %s</td>
                    <td class="amount">%s%%</td>
                    <td class="amount">€ %s</td>
                </tr>`, l.description, quantity, optionalText(l.unit), l.unitPrice.fixed2(), l.vat.String(), l.total.fixed2())
	}
	taxable, tax := decimal{coef: zeroInt()}, decimal{coef: zeroInt()}
	for _, s := range f.summaries {
		fmt.Fprintf(&summaries, `
                <tr>
                    <td>%s%%</td>
                    <td class="amount">€ %s</td>
                    <td class="amount">€ %s</td>
                </tr>`, s.vat.String(), s.taxable.fixed2(), s.tax.fixed2())
		taxable, _ = taxable.add(s.taxable)
		tax, _ = tax.add(s.tax)
	}
	payments := `<div class="section"><h3>Condizioni di Pagamento</h3>` +
		`<p><strong>Condizioni:</strong> ` + f.condition + `</p>` +
		`<p><strong>Modalità:</strong> ` + f.modality + `<br>
                <strong>Importo:</strong> € ` + f.total.fixed2() + `<br>` +
		`<strong>Scadenza:</strong> ` + f.expiryText() + `</p></div>`
	optional := func(prefix, value, suffix string) string {
		if value == "" {
			return ""
		}
		return prefix + value + suffix
	}
	vatCode := func(p party) string {
		if p.vatCode == nil {
			return ""
		}
		return *p.vatCode
	}
	taxCode := func(p party) string {
		if p.taxCode == nil {
			return ""
		}
		return *p.taxCode
	}
	slots := map[string]string{
		"body.dati_generali.dati_generali_documento.numero":         f.numero,
		"body.dati_generali.dati_generali_documento.data":           f.dateText(),
		"body.dati_generali.dati_generali_documento.tipo_documento": f.documentType,
		"linee_html":            lines.String(),
		"riepiloghi_html":       summaries.String(),
		"totale_imponibile:.2f": taxable.fixed2(),
		"totale_imposta:.2f":    tax.fixed2(),
		"body.dati_generali.dati_generali_documento.importo_totale_documento:.2f": f.total.fixed2(),
		"pagamenti_html": payments,
	}
	for name, p := range map[string]party{"cedente": f.cedente, "cessionario": f.cessionario} {
		slots[name+".dati_anagrafici.anagrafica.denominazione"] = *p.denomination
		slots[name+".sede.indirizzo"] = p.address
		slots[name+".sede.cap"] = p.cap
		slots[name+".sede.comune"] = p.city
		slots[name+".sede.provincia"] = p.province
		slots[name+".sede.nazione"] = p.country
		slots[name+"_piva"] = vatCode(p)
		slots[name+"_cf"] = taxCode(p)
	}
	var out strings.Builder
	for i, part := range htmlParts {
		out.WriteString(part)
		if i == len(htmlSlots) {
			break
		}
		name := htmlSlots[i]
		if rest, ok := strings.CutPrefix(name, "optional:"); ok {
			fields := strings.SplitN(rest, "|", 3)
			out.WriteString(optional(fields[1], slots[fields[0]], fields[2]))
			continue
		}
		value, ok := slots[name]
		if !ok {
			return "", fmt.Errorf("fatturapa: unknown slot %q", name)
		}
		out.WriteString(value)
	}
	return out.String(), nil
}

// --- XML (xml.etree.ElementTree.tostring) ----------------------------------------------------

var xmlEscaper = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;")

type xmlWriter struct{ strings.Builder }

func (w *xmlWriter) leaf(tag, text string) {
	if text == "" {
		w.WriteString("<" + tag + " />")
		return
	}
	w.WriteString("<" + tag + ">" + xmlEscaper.Replace(text) + "</" + tag + ">")
}

func (w *xmlWriter) optionalLeaf(tag string, text *string) {
	if text != nil {
		w.leaf(tag, *text)
	}
}

func (w *xmlWriter) open(tag string)  { w.WriteString("<" + tag + ">") }
func (w *xmlWriter) close(tag string) { w.WriteString("</" + tag + ">") }

func (w *xmlWriter) party(p party, cedente bool) {
	w.open("DatiAnagrafici")
	if p.vatCountry != nil {
		w.open("IdFiscaleIVA")
		w.leaf("IdPaese", *p.vatCountry)
		w.leaf("IdCodice", *p.vatCode)
		w.close("IdFiscaleIVA")
	}
	w.optionalLeaf("CodiceFiscale", p.taxCode)
	w.open("Anagrafica")
	w.leaf("Denominazione", *p.denomination)
	w.close("Anagrafica")
	if cedente {
		w.leaf("RegimeFiscale", p.regime)
	}
	w.close("DatiAnagrafici")
	w.open("Sede")
	w.leaf("Indirizzo", p.address)
	w.leaf("CAP", p.cap)
	w.leaf("Comune", p.city)
	w.leaf("Provincia", p.province)
	w.leaf("Nazione", p.country)
	w.close("Sede")
}

// XML is generate_invoice_xml with ProgressivoInvio set to progressivo.
func XML(inv Invoice, progressivo string) (string, error) {
	f, err := build(inv, progressivo)
	if err != nil {
		return "", err
	}
	w := &xmlWriter{}
	w.WriteString("<?xml version='1.0' encoding='utf-8'?>\n")
	w.WriteString(`<ns0:FatturaElettronica xmlns:ns0="` + namespace + `" versione="FPR12">`)
	w.open("FatturaElettronicaHeader")
	w.open("DatiTrasmissione")
	w.open("IdTrasmittente")
	w.leaf("IdPaese", f.transmitterCountry)
	w.leaf("IdCodice", f.transmitterCode)
	w.close("IdTrasmittente")
	w.leaf("ProgressivoInvio", f.progressivo)
	w.leaf("FormatoTrasmissione", "FPR12")
	w.leaf("CodiceDestinatario", "0000000")
	w.leaf("PECDestinatario", f.pec)
	w.close("DatiTrasmissione")
	w.open("CedentePrestatore")
	w.party(f.cedente, true)
	w.close("CedentePrestatore")
	w.open("CessionarioCommittente")
	w.party(f.cessionario, false)
	w.close("CessionarioCommittente")
	w.close("FatturaElettronicaHeader")
	w.open("FatturaElettronicaBody")
	w.open("DatiGenerali")
	w.open("DatiGeneraliDocumento")
	w.leaf("TipoDocumento", f.documentType)
	w.leaf("Divisa", f.currency)
	w.leaf("Data", f.dateText())
	w.leaf("Numero", f.numero)
	w.leaf("ImportoTotaleDocumento", f.total.String())
	for _, causal := range f.causal {
		w.leaf("Causale", causal)
	}
	w.close("DatiGeneraliDocumento")
	w.close("DatiGenerali")
	w.open("DatiBeniServizi")
	for _, l := range f.lines {
		w.open("DettaglioLinee")
		w.leaf("NumeroLinea", strconv.Itoa(l.number))
		w.leaf("Descrizione", l.description)
		if l.quantity != nil {
			w.leaf("Quantita", l.quantity.String())
		}
		w.optionalLeaf("UnitaMisura", l.unit)
		w.leaf("PrezzoUnitario", l.unitPrice.String())
		w.leaf("PrezzoTotale", l.total.String())
		w.leaf("AliquotaIVA", l.vat.String())
		w.close("DettaglioLinee")
	}
	for _, s := range f.summaries {
		w.open("DatiRiepilogo")
		w.leaf("AliquotaIVA", s.vat.String())
		w.leaf("ImponibileImporto", s.taxable.String())
		w.leaf("Imposta", s.tax.String())
		w.leaf("EsigibilitaIVA", "I")
		w.close("DatiRiepilogo")
	}
	w.close("DatiBeniServizi")
	w.open("DatiPagamento")
	w.leaf("CondizioniPagamento", f.condition)
	w.open("DettaglioPagamento")
	w.leaf("ModalitaPagamento", f.modality)
	w.leaf("DataScadenzaPagamento", f.expiryText())
	w.leaf("ImportoPagamento", f.total.String())
	w.close("DettaglioPagamento")
	w.close("DatiPagamento")
	w.close("FatturaElettronicaBody")
	w.WriteString("</ns0:FatturaElettronica>")
	return w.String(), nil
}
