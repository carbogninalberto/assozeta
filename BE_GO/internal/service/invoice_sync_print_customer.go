package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/fatturapa"
)

// invoiceSyncCustomer is the valid invoice_customers_add (invoice-sync-print):
// without it the add is refused after validation.
type invoiceSyncCustomer struct {
	store   domain.CustomerInvoiceSyncStore
	printer *InvoiceSyncPrint
}

// WithPendingCustomerInvoices enables invoice_customers_list's first loop
// (it needs WithCustomerInvoiceSync's printer).
func (s *InvoiceParties) WithPendingCustomerInvoices(store domain.CustomerInvoicePendingStore) *InvoiceParties {
	s.pendingCustomers = store
	return s
}

// customerListPending is invoice_customers_list's first loop: for each of the
// association's customer invoices without a PDF, generate_invoice_xml of the stored row and
// invoice.save() (an exception there is only logged), then
// print_document_customer_invoice.apply. Inputs outside the FatturaPA port
// are refused before any write.
func (s *InvoiceParties) customerListPending(ctx context.Context, request InvoicePartyRequest, association string) error {
	pending, err := s.pendingCustomers.PendingCustomerInvoices(ctx, association)
	if err != nil {
		return err
	}
	if len(pending) == 0 {
		return nil
	}
	if s.customerSync == nil {
		return domain.ErrInvoicePartyNotPorted
	}
	sample := fatturapa.Progressivo(s.now().Unix(), 0)
	for i := range pending {
		if _, err = fatturapa.XML(InvoiceViewsFattura(&pending[i].Row), sample); errors.Is(err, fatturapa.ErrUnsupported) {
			return domain.ErrInvoicePartyNotPorted
		}
	}
	audit := paymentCategoryAuditContext(request.Identity, request.Audit)
	ctx = context.WithoutCancel(ctx)
	for i := range pending {
		invoice := &pending[i]
		progressivo, err := invoiceSyncNextProgressivo(time.Now())
		if err != nil {
			return err
		}
		xml, err := fatturapa.XML(InvoiceViewsFattura(&invoice.Row), progressivo)
		switch {
		case errors.Is(err, fatturapa.ErrInvalid):
			// logger.error(...): no save.
		case err != nil:
			return err
		default:
			if err = s.pendingCustomers.SaveCustomerInvoiceXML(ctx, invoice.ID, invoice.Association, xml,
				audit, s.now().UTC().Truncate(time.Microsecond)); err != nil {
				return err
			}
		}
		if err = s.customerSync.printer.CustomerInvoice(ctx, invoice.ID, request.Authorization); err != nil {
			return err
		}
	}
	return nil
}

// WithCustomerInvoiceSync enables the valid customer add.
func (s *InvoiceParties) WithCustomerInvoiceSync(store domain.CustomerInvoiceSyncStore, printer *InvoiceSyncPrint) *InvoiceParties {
	s.customerSync = &invoiceSyncCustomer{store: store, printer: printer}
	return s
}

// errInvoiceSyncXML is the add's 200 {'error': ...} after an exception in
// generate_invoice_xml / generate_invoice_json.
var errInvoiceSyncXML = &domain.InvoicePartyResponseError{Status: 200, Message: "Errore durante la generazione del file XML."}

func invoiceSyncFattura(c domain.NewCustomerInvoice) fatturapa.Invoice {
	transmitting := c.TransmittingDate
	return fatturapa.Invoice{
		Prefix: c.Prefix, Number: c.Number, FiscalYear: c.FiscalYear,
		AssignorPrefixVAT: c.AssignorPrefixVAT, AssignorVAT: c.AssignorVAT, AssignorTaxCode: c.AssignorTaxCode,
		AssignorDenomination: c.AssignorDenomination, AssignorFiscalRegime: c.AssignorFiscalRegime,
		AssignorAddress: c.AssignorAddress, AssignorPostalCode: c.AssignorPostalCode, AssignorCity: c.AssignorCity,
		AssignorProvince: c.AssignorProvince, AssignorCountry: c.AssignorCountry, AssignorEmail: c.AssignorEmail,
		AssignorPhone: c.AssignorPhone, AssignorFax: c.AssignorFax,
		TransferorPrefixVAT: c.TransferorPrefixVAT, TransferorVAT: c.TransferorVAT, TransferorTaxCode: c.TransferorTaxCode,
		TransferorDenomination: c.TransferorDenomination, TransferorFiscalRegime: c.TransferorFiscalRegime,
		TransferorAddress: c.TransferorAddress, TransferorPostalCode: c.TransferorPostalCode, TransferorCity: c.TransferorCity,
		TransferorProvince: c.TransferorProvince, TransferorCountry: c.TransferorCountry, TransferorEmail: c.TransferorEmail,
		TransferorPhone: c.TransferorPhone, TransferorFax: c.TransferorFax,
		CountryTransmitterPrefix: c.CountryTransmitterPrefix, IDTransmitter: c.IDTransmitter,
		TransmittingFormat: c.TransmittingFormat, DocumentType: c.DocumentType, Currency: c.Currency,
		TransmittingDate: &transmitting, Causal: c.Causal, Lines: c.Lines,
		PaymentCondition: c.PaymentCondition, PaymentModality: c.PaymentModality, PaymentExpiryDate: c.PaymentExpiryDate,
	}
}

// invoiceSyncDate is the view's default (today) or strptime('%Y-%m-%d') of the value.
func invoiceSyncDate(object domain.RequestObject, name string, today time.Time) (time.Time, error) {
	value, present := object.Lookup(name)
	if !present || value == nil {
		return today, nil
	}
	parsed, _ := dto.PythonStrptimeDate(value.(string))
	if parsed.Year() < 1000 {
		// strftime('%Y') of a year below 1000 is platform dependent (unpadded on glibc).
		return time.Time{}, domain.ErrInvoicePartyNotPorted
	}
	return parsed, nil
}

// customerAddSave continues a validated invoice_customers_add: serializer.save()
// (create entry from the in-memory values), invoice.save() (an entry for what
// differs from the stored row), generate_invoice_xml/json of the in-memory
// instance and the third save, then print_document_customer_invoice.apply.
func (s *InvoiceParties) customerAddSave(ctx context.Context, request InvoicePartyRequest, association string,
	object domain.RequestObject) error {
	now := s.now().UTC()
	today := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	transmitting, err := invoiceSyncDate(object, "transmitting_date", today)
	if err != nil {
		return err
	}
	expiry, err := invoiceSyncDate(object, "payment_expiry_date", today)
	if err != nil {
		return err
	}
	invoice, created, memory := dto.CustomerInvoiceAddRecord(object, association, transmitting, expiry)
	if invoice.ID == "" {
		invoice.ID = s.ids()
	}
	at := now.Truncate(time.Microsecond)
	invoice.CreationDate = at
	if created != nil {
		invoice.CreationDate = *created
	}
	// Refuse inputs outside the ported FatturaPA subset before any write.
	fattura := invoiceSyncFattura(invoice)
	sample := fatturapa.Progressivo(now.Unix(), 0)
	if _, err = fatturapa.XML(fattura, sample); errors.Is(err, fatturapa.ErrUnsupported) {
		return domain.ErrInvoicePartyNotPorted
	}
	if _, err = fatturapa.JSON(fattura, sample); errors.Is(err, fatturapa.ErrUnsupported) {
		return domain.ErrInvoicePartyNotPorted
	}
	overrides := map[string]string{}
	saved := map[string]string{}
	if memory.AmountDefault {
		overrides["payment_total_amount"], saved["payment_total_amount"] = "0", "0"
	}
	for name, value := range map[string]any{"lines": memory.Lines, "json": memory.JSON} {
		if value != nil {
			overrides[name] = string(dto.PythonJSONDumpsSorted(value))
		}
		saved[name] = string(dto.PythonJSONDumpsSorted(value))
	}
	audit := paymentCategoryAuditContext(request.Identity, request.Audit)
	ctx = context.WithoutCancel(ctx)
	if err = s.customerSync.store.CreateCustomerInvoice(ctx, invoice, overrides, audit, at); err != nil {
		return err
	}
	if err = s.customerSync.store.SaveCustomerInvoice(ctx, invoice.ID, association, saved, nil, nil, audit, at); err != nil {
		return err
	}
	xml, json, err := invoiceSyncOutputs(fattura)
	if err != nil {
		if errors.Is(err, fatturapa.ErrInvalid) {
			return errInvoiceSyncXML
		}
		return err
	}
	saved["xml"] = xml
	saved["json"] = string(dto.PythonJSONDumpsSorted(json))
	stored := string(dto.PythonJSONDumps(json))
	if err = s.customerSync.store.SaveCustomerInvoice(ctx, invoice.ID, association, saved, &xml, &stored, audit, at); err != nil {
		return err
	}
	return s.customerSync.printer.CustomerInvoice(ctx, invoice.ID, request.Authorization)
}

// invoiceSyncOutputs is generate_invoice_xml then generate_invoice_json, each
// building the a38 model (one ProgressivoInvio each).
func invoiceSyncOutputs(fattura fatturapa.Invoice) (string, string, error) {
	progressivo, err := invoiceSyncNextProgressivo(time.Now())
	if err != nil {
		return "", "", err
	}
	xml, err := fatturapa.XML(fattura, progressivo)
	if err != nil {
		return "", "", err
	}
	if progressivo, err = invoiceSyncNextProgressivo(time.Now()); err != nil {
		return "", "", err
	}
	json, err := fatturapa.JSON(fattura, progressivo)
	if err != nil {
		return "", "", err
	}
	return xml, json, nil
}
