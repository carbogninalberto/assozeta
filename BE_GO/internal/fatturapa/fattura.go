// Package fatturapa reproduces BE/application/serializers/invoice_serializers.py's
// generate_einvoice (a38 0.1.8: FatturaPrivati12, add_dettaglio_linee,
// build_dati_riepilogo, build_importo_totale_documento, validation) and its
// generate_invoice_html, generate_invoice_xml and generate_invoice_json
// outputs for the CustomerInvoice rows the application stores.
//
// a38 details the port follows (checked against the library by the golden
// test, testdata/a38_renders.json):
//   - CedentePrestatore(dati, sede, contatti) and CessionarioCommittente(...)
//     take the Contatti positionally as stabile_organizzazione, whose copy has
//     no value: contacts never reach the output or the validation.
//   - codice_destinatario defaults to "0000000", so a PEC (the transferor's
//     e-mail, 8..256 characters) is required (code 00426).
//   - the cessionario's empty IdFiscaleIVA is a model, not None: rule 00417
//     never applies.
//   - DecimalField quantizes half up to its decimals range; Python's context
//     arithmetic (28 digits, half even) is exact for the values that fit, and
//     inputs whose arithmetic would round are refused (ErrUnsupported).
package fatturapa

import (
	"errors"
	"strconv"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrUnsupported is an input outside the ported subset.
	ErrUnsupported = refusal.New("fatturapa: input not ported")
	// ErrInvalid is an input for which generate_einvoice raises.
	ErrInvalid = errors.New("fatturapa: generate_einvoice raises")
)

// Invoice is a CustomerInvoice row (the columns generate_einvoice reads).
type Invoice struct {
	Prefix                 *string
	Number, FiscalYear     *int64
	AssignorPrefixVAT      string
	AssignorVAT            *string
	AssignorTaxCode        *string
	AssignorDenomination   *string
	AssignorFiscalRegime   string
	AssignorAddress        string
	AssignorPostalCode     string
	AssignorCity           string
	AssignorProvince       string
	AssignorCountry        string
	AssignorEmail          *string
	AssignorPhone          *string
	AssignorFax            *string
	TransferorPrefixVAT    string
	TransferorVAT          *string
	TransferorTaxCode      *string
	TransferorDenomination *string
	TransferorFiscalRegime string
	TransferorAddress      string
	TransferorPostalCode   string
	TransferorCity         string
	TransferorProvince     string
	TransferorCountry      string
	TransferorEmail        *string
	TransferorPhone        *string
	TransferorFax          *string

	CountryTransmitterPrefix string
	IDTransmitter            *string
	TransmittingFormat       string
	DocumentType             string
	Currency                 string
	TransmittingDate         *time.Time
	Causal                   *string
	Lines                    *string // jsonb text; nil is SQL NULL
	PaymentCondition         *string
	PaymentModality          *string
	PaymentExpiryDate        time.Time
}

// party is a CedentePrestatore or CessionarioCommittente.
type party struct {
	vatCountry, vatCode *string // IdFiscaleIVA; nil when not set
	taxCode             *string
	denomination        *string
	regime              string // cedente only
	address, cap, city  string
	province, country   string
}

type line struct {
	number      int
	description string
	quantity    *decimal
	unit        *string
	unitPrice   decimal
	total       decimal
	vat         decimal
}

type summary struct {
	vat, taxable, tax decimal
}

// fattura is the built FatturaPrivati12 with what the outputs read.
type fattura struct {
	transmitterCountry, transmitterCode string
	progressivo                         string
	pec                                 string
	cedente, cessionario                party
	documentType, currency, numero      string
	date, expiry                        time.Time
	causal                              []string
	lines                               []line
	summaries                           []summary
	total                               decimal
	condition, modality                 string
}

// builder carries the outcome flags of one build.
type builder struct {
	invalid bool // a raise or validation error independent of rounding
	inexact bool // Python's context arithmetic would have rounded
}

func optionalText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func runes(text string) int { return utf8.RuneCountInString(text) }

func lengthIn(text string, minimum, maximum int) bool {
	n := runes(text)
	return n >= minimum && n <= maximum
}

func oneOf(value string, choices []string) bool {
	for _, choice := range choices {
		if value == choice {
			return true
		}
	}
	return false
}

var (
	tipoDocumento = []string{"TD01", "TD02", "TD03", "TD04", "TD05", "TD06", "TD07", "TD08", "TD09", "TD16", "TD17", "TD18",
		"TD19", "TD20", "TD21", "TD22", "TD23", "TD24", "TD25", "TD26", "TD27"}
	regimeFiscale = []string{"RF01", "RF02", "RF04", "RF05", "RF06", "RF07", "RF08", "RF09", "RF10", "RF11", "RF12", "RF13",
		"RF14", "RF15", "RF16", "RF17", "RF18", "RF19"}
	modalitaPagamento = []string{"MP01", "MP02", "MP03", "MP04", "MP05", "MP06", "MP07", "MP08", "MP09", "MP10", "MP11", "MP12",
		"MP13", "MP14", "MP15", "MP16", "MP17", "MP18", "MP19", "MP20", "MP21", "MP22", "MP23"}
	condizioniPagamento = []string{"TP01", "TP02", "TP03"}
)

func newParty(prefix string, vat, taxCode, denomination *string, regime, address, cap, city, province, country string) party {
	p := party{taxCode: taxCode, denomination: denomination, regime: regime, address: address, cap: cap, city: city,
		province: province, country: country}
	// if prefix and vat: IdFiscaleIVA(prefix, vat)
	if prefix != "" && vat != nil && *vat != "" {
		p.vatCountry, p.vatCode = &prefix, vat
	}
	return p
}

// validParty is the field validation of DatiAnagrafici and Sede.
func (p party) valid(cedente bool) bool {
	if p.vatCountry != nil {
		if runes(*p.vatCountry) != 2 || runes(*p.vatCode) > 28 {
			return false
		}
	} else if cedente {
		return false // IdFiscaleIVA is required on the cedente
	}
	if p.taxCode != nil && !lengthIn(*p.taxCode, 11, 16) {
		return false
	}
	if p.denomination == nil || runes(*p.denomination) > 80 {
		return false
	}
	if cedente && (runes(p.regime) != 4 || !oneOf(p.regime, regimeFiscale)) {
		return false
	}
	return runes(p.address) <= 60 && runes(p.cap) == 5 && runes(p.city) <= 60 && runes(p.province) == 2 && runes(p.country) == 2
}

func checkDate(value time.Time) error {
	if value.Year() < 1000 {
		return ErrUnsupported // str(date) and strftime('%Y') padding
	}
	return nil
}

// build is generate_einvoice.
func build(inv Invoice, progressivo string) (*fattura, error) {
	if inv.TransmittingDate != nil {
		if err := checkDate(*inv.TransmittingDate); err != nil {
			return nil, err
		}
	}
	if err := checkDate(inv.PaymentExpiryDate); err != nil {
		return nil, err
	}
	b := &builder{}
	f := &fattura{
		transmitterCountry: inv.CountryTransmitterPrefix, transmitterCode: "0000000", progressivo: progressivo,
		cedente: newParty(inv.AssignorPrefixVAT, inv.AssignorVAT, inv.AssignorTaxCode, inv.AssignorDenomination,
			inv.AssignorFiscalRegime, inv.AssignorAddress, inv.AssignorPostalCode, inv.AssignorCity, inv.AssignorProvince,
			inv.AssignorCountry),
		cessionario: newParty(inv.TransferorPrefixVAT, inv.TransferorVAT, inv.TransferorTaxCode, inv.TransferorDenomination,
			inv.TransferorFiscalRegime, inv.TransferorAddress, inv.TransferorPostalCode, inv.TransferorCity,
			inv.TransferorProvince, inv.TransferorCountry),
		documentType: inv.DocumentType, currency: inv.Currency, expiry: inv.PaymentExpiryDate,
	}
	if inv.IDTransmitter != nil && *inv.IDTransmitter != "" {
		f.transmitterCode = *inv.IDTransmitter
	}
	hasPEC := inv.TransferorEmail != nil && *inv.TransferorEmail != ""
	if hasPEC {
		f.pec = *inv.TransferorEmail
	}
	number := func(value *int64) string {
		if value == nil {
			return "None"
		}
		return strconv.FormatInt(*value, 10)
	}
	f.numero = optionalText(inv.Prefix) + " " + number(inv.Number) + "/" + number(inv.FiscalYear)
	if inv.Causal != nil {
		f.causal = []string{*inv.Causal}
	}
	// Field validation that does not depend on the amounts.
	if runes(f.transmitterCountry) != 2 || runes(f.transmitterCode) > 28 || progressivo == "" || runes(progressivo) > 10 ||
		!hasPEC || !lengthIn(f.pec, 8, 256) || !f.cedente.valid(true) || !f.cessionario.valid(false) ||
		runes(f.documentType) != 4 || !oneOf(f.documentType, tipoDocumento) || runes(f.numero) > 20 || !containsDigit(f.numero) ||
		(len(f.causal) == 1 && runes(f.causal[0]) > 200) ||
		inv.PaymentCondition == nil || !oneOf(*inv.PaymentCondition, condizioniPagamento) ||
		inv.PaymentModality == nil || !oneOf(*inv.PaymentModality, modalitaPagamento) {
		b.invalid = true
	}
	// DatiGeneraliDocumento(data=str(None)) raises ValueError.
	if inv.TransmittingDate == nil {
		return nil, ErrInvalid
	}
	f.date = *inv.TransmittingDate
	if inv.PaymentCondition != nil {
		f.condition = *inv.PaymentCondition
	}
	if inv.PaymentModality != nil {
		f.modality = *inv.PaymentModality
	}
	if err := b.lines(f, inv.Lines); err != nil {
		return nil, err
	}
	if err := b.summarize(f); err != nil {
		return nil, err
	}
	switch {
	case b.invalid:
		return nil, ErrInvalid
	case b.inexact:
		return nil, ErrUnsupported
	}
	return f, nil
}

func containsDigit(text string) bool {
	for _, r := range text {
		if r >= '0' && r <= '9' {
			return true
		}
	}
	return false
}

// raise is an exception during the build: certain unless it depends on a
// rounded amount.
func (b *builder) raise(dependent bool) error {
	if dependent && b.inexact && !b.invalid {
		return ErrUnsupported
	}
	return ErrInvalid
}

func (b *builder) lines(f *fattura, raw *string) error {
	if raw == nil {
		return ErrInvalid // for line in None
	}
	parsed, err := parseJSON(*raw)
	if err != nil {
		return err
	}
	items, ok := parsed.([]any)
	if !ok {
		// Iterating a dict or str yields str keys/characters (indexing raises), an
		// empty one no lines (missing value); numbers, bools and None raise.
		return ErrInvalid
	}
	if len(items) == 0 {
		b.invalid = true // dettaglio_linee: missing value
	}
	for index, item := range items {
		object, ok := item.(map[string]any)
		if !ok {
			return ErrInvalid
		}
		var values [5]any
		for i, key := range []string{"description", "quantity", "unit_of_measure", "unit_price", "vat"} {
			value, present := object[key]
			if !present {
				return ErrInvalid // KeyError
			}
			values[i] = value
		}
		vat := values[4]
		if !pyTruthy(vat) {
			vat = "22.0"
		}
		l := line{number: index + 1}
		// DettaglioLinee(**kw): fields cleaned in declaration order.
		description, err := pyStr(values[0])
		if err != nil {
			return err
		}
		quantity, err := b.decimalField(values[1], 2, 8)
		if err != nil {
			return err
		}
		unit, err := pyStr(values[2])
		if err != nil {
			return err
		}
		price, err := b.decimalField(values[3], 2, 8)
		if err != nil {
			return err
		}
		rate, err := b.decimalField(vat, 2, 2)
		if err != nil {
			return err
		}
		l.quantity, l.unit = quantity, unit
		// autofill_prezzo_totale
		switch {
		case price == nil && quantity != nil:
			return ErrInvalid // None * Decimal
		case price == nil:
		case quantity == nil:
			l.unitPrice, l.total = *price, *price
		default:
			product, exact := price.mul(*quantity)
			if !exact {
				b.inexact = true
			}
			total, ok := product.clean(2, 8)
			if !ok {
				return b.raise(true)
			}
			l.unitPrice, l.total = *price, total
		}
		l.vat = *rate
		if description == nil {
			b.invalid = true
		} else {
			l.description = *description
		}
		if description != nil && runes(*description) > 1000 || (quantity == nil) != (unit == nil) ||
			unit != nil && runes(*unit) > 10 || len(strconv.Itoa(l.number)) > 4 || l.vat.isZero() ||
			quantity != nil && len(quantity.String()) > 21 || price != nil && len(price.String()) > 21 || len(rate.String()) > 6 {
			b.invalid = true
		}
		if price == nil {
			// prezzo_totale stays None: build_dati_riepilogo's sum raises TypeError.
			b.invalid = true
			continue
		}
		if len(l.total.String()) > 21 {
			b.dependentInvalid()
		}
		f.lines = append(f.lines, l)
	}
	return nil
}

// dependentInvalid is a validation error on a computed amount.
func (b *builder) dependentInvalid() {
	if !b.inexact {
		b.invalid = true
	}
}

// decimalField is DecimalField.clean_value of a JSON value (nil for None).
func (b *builder) decimalField(value any, minimum, maximum int) (*decimal, error) {
	parsed, err := pyDecimal(value)
	if err != nil || parsed == nil {
		return nil, err
	}
	cleaned, ok := parsed.clean(minimum, maximum)
	if !ok {
		return nil, ErrInvalid // quantize: InvalidOperation
	}
	return &cleaned, nil
}

// summarize is build_dati_riepilogo, build_importo_totale_documento and the
// payment amount.
func (b *builder) summarize(f *fattura) error {
	type group struct {
		vat   decimal
		lines []line
	}
	var groups []*group
	for _, l := range f.lines {
		var target *group
		for _, g := range groups {
			if g.vat.cmp(l.vat) == 0 {
				target = g
				break
			}
		}
		if target == nil {
			target = &group{vat: l.vat}
			groups = append(groups, target)
		}
		target.lines = append(target.lines, l)
	}
	// sorted(by_aliquota.items()): distinct rates, ascending.
	for i := 1; i < len(groups); i++ {
		for j := i; j > 0 && groups[j].vat.cmp(groups[j-1].vat) < 0; j-- {
			groups[j], groups[j-1] = groups[j-1], groups[j]
		}
	}
	total := decimal{coef: zeroInt()}
	for _, g := range groups {
		imponibile := decimal{coef: zeroInt()}
		for _, l := range g.lines {
			var exact bool
			if imponibile, exact = imponibile.add(l.total); !exact {
				b.inexact = true
			}
		}
		product, exact := imponibile.mul(g.vat)
		if !exact {
			b.inexact = true
		}
		taxable, ok := imponibile.clean(2, 2)
		if !ok {
			return b.raise(true)
		}
		tax, ok := product.div100().clean(2, 2)
		if !ok {
			return b.raise(true)
		}
		if len(taxable.String()) > 15 || len(tax.String()) > 15 {
			b.dependentInvalid()
		}
		f.summaries = append(f.summaries, summary{vat: g.vat, taxable: taxable, tax: tax})
		both, exact := taxable.add(tax)
		if !exact {
			b.inexact = true
		}
		if total, exact = total.add(both); !exact {
			b.inexact = true
		}
	}
	cleaned, ok := total.clean(2, 2)
	if !ok {
		return b.raise(true)
	}
	f.total = cleaned
	if len(cleaned.String()) > 15 {
		b.dependentInvalid()
	}
	return nil
}
