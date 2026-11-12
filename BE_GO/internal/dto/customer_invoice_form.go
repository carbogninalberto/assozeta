package dto

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

// customerInvoiceFormText is one CustomerInvoiceSerializer text field's HTML-form
// empty-input rule (allow_null/allow_blank).
type customerInvoiceFormText struct {
	null, blank bool
}

var customerInvoiceFormTexts = func() map[string]customerInvoiceFormText {
	result := make(map[string]customerInvoiceFormText, len(customerInvoiceTexts))
	for _, field := range customerInvoiceTexts {
		result[field.Name] = customerInvoiceFormText{null: field.Null, blank: field.Blank}
	}
	return result
}()

// customerInvoiceFormBooleans are CustomerInvoiceSerializer's BooleanFields:
// Field.get_value returns False when the HTML form lacks the key, while a
// present empty input is the empty sentinel (the field is skipped and the model
// default applies).
var customerInvoiceFormBooleans = []string{"e_invoice", "paid", "transmitted"}

// CustomerInvoiceFormData is DRF's Field.get_value/JSONField.get_value for a
// CustomerInvoiceSerializer over a multipart form's last values, expressed as
// the JSON-equivalent object ParseCustomerInvoiceAdd and CustomerInvoiceAddRecord
// read. Absent booleans become False; JSON text is parsed like json.loads (a
// file's name is its text); any other present empty input is None, kept empty or
// absent according to the field's allow_null/allow_blank/required options.
// transmitting_date, payment_expiry_date and keys outside the serializer are
// copied unchanged. notPorted marks JSON text nested past the ported depth.
func CustomerInvoiceFormData(object domain.RequestObject) (data domain.RequestObject, errs InvoicePartyErrors, notPorted bool) {
	errs = InvoicePartyErrors{}
	data = make(domain.RequestObject, 0, len(object)+len(customerInvoiceFormBooleans))
	seen := make(map[string]bool, len(object))
	for _, field := range object {
		name, value := field.Name, field.Value
		seen[name] = true
		if name == "lines" || name == "json" {
			// JSONField.get_value: the form's text (a file's name) is json.loads.
			if upload, isUpload := value.(domain.RequestUpload); isUpload {
				value = upload.Name
			}
			tree, valid, _, deep := automationFormTree(value)
			switch {
			case deep:
				return nil, errs, true
			case !valid:
				errs.add(name, CourseSubscriptionJSONInvalid)
			default:
				data = append(data, domain.RequestField{Name: name, Value: tree})
			}
			continue
		}
		if converted, present := customerInvoiceFormValue(name, value); present {
			data = append(data, domain.RequestField{Name: name, Value: converted})
		}
	}
	for _, name := range customerInvoiceFormBooleans {
		if !seen[name] {
			data = append(data, domain.RequestField{Name: name, Value: false})
		}
	}
	return data, errs, false
}

// customerInvoiceFormValue is Field.get_value for a present key: a non-empty
// value is kept for to_internal_value; an empty input follows the field's
// options (None, an empty string, or skipped). A JSON field is handled by the
// caller because its get_value override treats even an empty input as JSON text.
func customerInvoiceFormValue(name string, value any) (any, bool) {
	text, isText := value.(string)
	if !isText || text != "" {
		return value, true
	}
	// Every BooleanField here has a model default (not required) and no
	// allow_blank: get_value turns its '' into the empty sentinel.
	for _, boolean := range customerInvoiceFormBooleans {
		if name == boolean {
			return nil, false
		}
	}
	if field, isText := customerInvoiceFormTexts[name]; isText {
		switch {
		case field.null && field.blank:
			return "", true
		case field.null:
			return nil, true
		default:
			// Not null and not required (the model has a default): absent.
			return nil, false
		}
	}
	if _, isChoice := customerInvoiceChoices[name]; isChoice {
		return nil, true // allow_null, no allow_blank
	}
	switch name {
	case "number", "fiscal_year":
		return nil, true // IntegerField allow_null
	case "pdf", "payment":
		return nil, true // PrimaryKeyRelatedField allow_null
	case "payment_total_amount", "creation_date", "customer_invoice_id":
		// Not null, not required: the empty input is absent.
		return nil, false
	}
	// transmitting_date, payment_expiry_date and keys outside the serializer
	// stay unchanged (the view validates the former before the serializer).
	return value, true
}
