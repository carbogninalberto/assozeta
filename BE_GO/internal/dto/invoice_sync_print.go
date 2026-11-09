package dto

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// CustomerInvoiceMemory is what the add's in-memory instance holds where its
// str() differs from the stored row: payment_total_amount keeps the model
// default int 0 when absent, the JSON fields keep the parsed request values.
type CustomerInvoiceMemory struct {
	AmountDefault bool
	Lines, JSON   any
}

// CustomerInvoiceAddRecord is CustomerInvoiceSerializer's validated_data of a
// body ParseCustomerInvoiceAdd accepted, with the model defaults for absent
// fields. ID and CreationDate stay unset when absent (uuid4 and now).
func CustomerInvoiceAddRecord(object domain.RequestObject, association string, transmitting, expiry time.Time) (domain.NewCustomerInvoice,
	*time.Time, CustomerInvoiceMemory) {
	fpr, it, regime, empty := "FPR", "IT", "RF01", ""
	c := domain.NewCustomerInvoice{Prefix: &fpr, AssignorPrefixVAT: it, AssignorFiscalRegime: regime, AssignorCountry: it,
		TransferorPrefixVAT: it, TransferorFiscalRegime: regime, TransferorCountry: it, EInvoice: true, CountryTransmitterPrefix: it,
		TransmittingFormat: "FPR12", DocumentType: "TD01", Currency: "EUR", TransmittingDate: transmitting, PaymentTotalAmount: "0.00",
		PaymentExpiryDate: expiry, Association: association}
	nullable := map[string]**string{
		"prefix": &c.Prefix, "assignor_vat_number": &c.AssignorVAT, "assignor_tax_code": &c.AssignorTaxCode,
		"assignor_denomination": &c.AssignorDenomination, "assignor_contact_email": &c.AssignorEmail,
		"assignor_contact_phone": &c.AssignorPhone, "assignor_fax": &c.AssignorFax, "transferor_vat_number": &c.TransferorVAT,
		"transferor_tax_code": &c.TransferorTaxCode, "transferor_denomination": &c.TransferorDenomination,
		"transferor_contact_email": &c.TransferorEmail, "transferor_contact_phone": &c.TransferorPhone, "transferor_fax": &c.TransferorFax,
		"id_transmitter": &c.IDTransmitter, "causal": &c.Causal, "xml": &c.XML,
	}
	required := map[string]*string{
		"assignor_prefix_vat_number": &c.AssignorPrefixVAT, "assignor_fiscal_regime": &c.AssignorFiscalRegime,
		"assignor_address": &c.AssignorAddress, "assignor_postal_code": &c.AssignorPostalCode, "assignor_city": &c.AssignorCity,
		"assignor_province": &c.AssignorProvince, "assignor_country": &c.AssignorCountry,
		"transferor_prefix_vat_number": &c.TransferorPrefixVAT, "transferor_fiscal_regime": &c.TransferorFiscalRegime,
		"transferor_address": &c.TransferorAddress, "transferor_postal_code": &c.TransferorPostalCode, "transferor_city": &c.TransferorCity,
		"transferor_province": &c.TransferorProvince, "transferor_country": &c.TransferorCountry,
		"country_transmitter_prefix": &c.CountryTransmitterPrefix, "transmitting_format": &c.TransmittingFormat,
		"document_type": &c.DocumentType, "currency": &c.Currency,
	}
	c.AssignorAddress, c.AssignorPostalCode, c.AssignorCity, c.AssignorProvince = empty, empty, empty, empty
	for _, field := range customerInvoiceTexts {
		value, present := object.Lookup(field.Name)
		if !present {
			continue
		}
		text, _ := modelTextValue(SupplierField{Name: field.Name, Max: field.Max, Null: field.Null, Blank: field.Blank}, value, false)
		if target, ok := nullable[field.Name]; ok {
			*target = text
		} else if target, ok := required[field.Name]; ok && text != nil {
			*target = *text
		}
	}
	if value, present := object.Lookup("customer_invoice_id"); present {
		if id, ok := drfUUID(value); ok {
			c.ID = id
		}
	}
	for name, target := range map[string]**int64{"number": &c.Number, "fiscal_year": &c.FiscalYear} {
		if value, present := object.Lookup(name); present {
			*target, _ = drfInteger(value, true, -1<<31, 1<<31-1)
		}
	}
	for name, target := range map[string]*bool{"e_invoice": &c.EInvoice, "paid": &c.Paid, "transmitted": &c.Transmitted} {
		if value, present := object.Lookup(name); present {
			if parsed, _ := drfBoolean(value, false); parsed != nil {
				*target = *parsed
			}
		}
	}
	for name, target := range map[string]**string{"payment_condition": &c.PaymentCondition, "payment_modality": &c.PaymentModality} {
		if value, present := object.Lookup(name); present && value != nil {
			text := PythonStr(value)
			*target = &text
		}
	}
	memory := CustomerInvoiceMemory{AmountDefault: true}
	if value, present := object.Lookup("payment_total_amount"); present {
		c.PaymentTotalAmount, _ = drfDecimal(value, 9, 2)
		memory.AmountDefault = false
	}
	var created *time.Time
	if value, present := object.Lookup("creation_date"); present {
		created, _ = drfDateTime(value, false)
	}
	for name, target := range map[string]**string{"pdf": &c.PDF, "payment": &c.Payment} {
		if value, present := object.Lookup(name); present {
			related, _ := drfRelated(value, true)
			*target = related.ID
		}
	}
	for name, target := range map[string]*any{"lines": &memory.Lines, "json": &memory.JSON} {
		if value, present := object.Lookup(name); present && value != nil {
			*target = value
			text := string(PythonJSONDumps(value))
			if name == "lines" {
				c.Lines = &text
			} else {
				c.JSON = &text
			}
		}
	}
	return c, created, memory
}
