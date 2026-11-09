package service

import (
	"context"
	"errors"
	"fmt"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/fatturapa"
)

// einvoice is document_einvoice_view: CustomerInvoice.objects.get(pk=uid)
// (no scope, no group) and generate_invoice_html; every exception is the
// 500 page.
func (s *InvoiceViews) einvoice(ctx context.Context, request DocumentViewRequest) (*DocumentViewReply, error) {
	id, ok := parseModelUUID(request.UID)
	if !ok {
		return nil, ErrDocumentViewServer
	}
	row, err := s.store.CustomerInvoice(ctx, id)
	if err != nil {
		return nil, lookupError(err)
	}
	html, err := fatturapa.HTML(InvoiceViewsFattura(row))
	if err != nil {
		if errors.Is(err, fatturapa.ErrUnsupported) {
			return nil, fmt.Errorf("%w: %v", errInvoiceViewsNotPorted, err)
		}
		if errors.Is(err, fatturapa.ErrInvalid) {
			return nil, ErrDocumentViewServer
		}
		return nil, err
	}
	return htmlReply(html, request.Language), nil
}

// InvoiceViewsFattura is the CustomerInvoice row as generate_einvoice reads it.
func InvoiceViewsFattura(c *domain.InvoiceViewCustomerInvoice) fatturapa.Invoice {
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
		TransmittingDate: c.TransmittingDate, Causal: c.Causal, Lines: c.Lines,
		PaymentCondition: c.PaymentCondition, PaymentModality: c.PaymentModality, PaymentExpiryDate: c.PaymentExpiryDate,
	}
}
