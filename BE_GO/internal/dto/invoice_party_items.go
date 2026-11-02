package dto

import (
	"encoding/json"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func invoicePartyTime(value *time.Time) any {
	if value == nil {
		return nil
	}
	return DRFUTCDateTime(*value)
}

func invoicePartyDate(value *time.Time) any {
	if value == nil {
		return nil
	}
	return value.Format("2006-01-02")
}

// invoicePartyJSON renders a JSONField value (SQL NULL is None).
func invoicePartyJSON(value *string) any {
	if value == nil {
		return nil
	}
	return json.RawMessage(*value)
}

func invoicePartyNumber(value *int32) any {
	if value == nil {
		return nil
	}
	return *value
}

// CustomerInvoiceItem is CustomerInvoiceSerializer (fields '__all__' plus pdf_token).
func CustomerInvoiceItem(row domain.CustomerInvoiceRow) map[string]any {
	return map[string]any{
		"customer_invoice_id": row.ID, "pdf_token": row.PDFToken, "prefix": row.Prefix, "number": invoicePartyNumber(row.Number),
		"fiscal_year": invoicePartyNumber(row.FiscalYear), "assignor_prefix_vat_number": row.AssignorPrefixVATNumber,
		"assignor_vat_number": row.AssignorVATNumber, "assignor_tax_code": row.AssignorTaxCode, "assignor_denomination": row.AssignorDenomination,
		"assignor_fiscal_regime": row.AssignorFiscalRegime, "assignor_address": row.AssignorAddress, "assignor_postal_code": row.AssignorPostalCode,
		"assignor_city": row.AssignorCity, "assignor_province": row.AssignorProvince, "assignor_country": row.AssignorCountry,
		"assignor_contact_email": row.AssignorContactEmail, "assignor_contact_phone": row.AssignorContactPhone, "assignor_fax": row.AssignorFax,
		"transferor_prefix_vat_number": row.TransferorPrefixVATNumber, "transferor_vat_number": row.TransferorVATNumber,
		"transferor_tax_code": row.TransferorTaxCode, "transferor_denomination": row.TransferorDenomination,
		"transferor_fiscal_regime": row.TransferorFiscalRegime, "transferor_address": row.TransferorAddress,
		"transferor_postal_code": row.TransferorPostalCode, "transferor_city": row.TransferorCity, "transferor_province": row.TransferorProvince,
		"transferor_country": row.TransferorCountry, "transferor_contact_email": row.TransferorContactEmail,
		"transferor_contact_phone": row.TransferorContactPhone, "transferor_fax": row.TransferorFax, "e_invoice": row.EInvoice,
		"country_transmitter_prefix": row.CountryTransmitterPrefix, "id_transmitter": row.IDTransmitter,
		"transmitting_format": row.TransmittingFormat, "document_type": row.DocumentType, "currency": row.Currency,
		"transmitting_date": invoicePartyDate(row.TransmittingDate), "causal": row.Causal, "lines": invoicePartyJSON(row.Lines),
		"payment_condition": row.PaymentCondition, "payment_modality": row.PaymentModality, "payment_total_amount": row.PaymentTotalAmount,
		"payment_expiry_date": row.PaymentExpiryDate.Format("2006-01-02"), "xml": row.XML, "json": invoicePartyJSON(row.JSON),
		"creation_date": DRFUTCDateTime(row.CreationDate), "paid": row.Paid, "transmitted": row.Transmitted, "pdf": row.PDFID,
		"sport_association": row.AssociationID, "payment": row.PaymentID,
	}
}

func invoicePartySupplier(row *domain.Supplier) any {
	if row == nil {
		return nil
	}
	return SupplierItemFrom(*row)
}

// InvoicePartyPaymentItem is PaymentSerializer (fields '__all__') for a payment
// without associate, instructor or invoice (the service refuses those).
func InvoicePartyPaymentItem(row domain.InvoicePartyPayment) map[string]any {
	var user any
	if row.User != nil {
		user = map[string]any{"first_name": row.User.FirstName, "last_name": row.User.LastName, "email": row.User.Email,
			"phone": row.User.Phone, "username": row.User.Username, "role": row.User.Role}
	}
	attachments := make([]map[string]any, 0, len(row.Attachments))
	for _, document := range row.Attachments {
		attachments = append(attachments, map[string]any{"document_id": document.ID, "creation_date": DRFUTCDateTime(document.CreationDate),
			"filepath": document.Filepath, "filename": document.Filename, "file_size_bytes": document.Size, "token": document.Token})
	}
	var supplierID any
	if row.Supplier != nil {
		supplierID = row.Supplier.ID
	}
	return map[string]any{
		"payment_id": row.ID, "user": user, "associate": nil, "subscription_id": row.Subscription, "instructor_id": nil, "instructor": nil,
		"supplier": invoicePartySupplier(row.Supplier), "supplier_id": supplierID, "custom_account_type": row.AccountType,
		"custom_account_name": row.AccountName, "invoice": nil, "attachments": attachments, "payment_category_name": row.CategoryName,
		"type": row.Type, "subject": row.Subject, "amount": row.Amount, "creation_date": DRFUTCDateTime(row.CreationDate),
		"payment_date": invoicePartyTime(row.PaymentDate), "paid": row.Paid, "expense": row.Expense, "description": row.Description,
		"meta_payment_categories": invoicePartyJSON(row.MetaPaymentCategories), "payment_intent_id": row.PaymentIntentID,
		"archived": row.Archived, "invoice_fiscal_code": row.InvoiceFiscalCode, "meta": invoicePartyJSON(row.Meta), "notes": row.Notes,
		"deleted": row.Deleted, "course": invoicePartyJSON(row.Course), "payment_category": row.CategoryID,
		"sport_association": row.AssociationID, "custom_accounts": row.AccountID, "signature": row.SignatureID, "group": row.GroupID,
	}
}

// SupplierInvoiceItem is InvoiceSuppliersSerializer.
func SupplierInvoiceItem(row domain.SupplierInvoiceRow) map[string]any {
	var payment any
	if row.Payment != nil {
		payment = InvoicePartyPaymentItem(*row.Payment)
	}
	return map[string]any{
		"invoice_supplier_id": row.ID, "payment": payment, "payment_id": row.PaymentID, "document_token": row.DocumentToken,
		"notes": row.Notes, "supplier": invoicePartySupplier(row.Supplier), "invoice_identifier": row.Identifier, "amount": row.Amount,
		"payment_date": invoicePartyTime(row.PaymentDate), "expire_date": invoicePartyTime(row.ExpireDate), "paid": row.Paid,
		"sport_association": row.AssociationID, "document_pdf": row.DocumentID,
	}
}
