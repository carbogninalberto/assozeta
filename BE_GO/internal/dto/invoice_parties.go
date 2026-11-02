package dto

import (
	"encoding/json"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// InvoicePartyErrors is serializer.errors: field -> []code, or a nested
// serializer's errors (map). Codes render through the payment-category table
// plus the invoice-party codes (max-value, text:).
type InvoicePartyErrors map[string]any

func (e InvoicePartyErrors) add(field string, codes ...string) {
	existing, _ := e[field].([]string)
	e[field] = append(existing, codes...)
}

// InvoicePartyValidationError is a DRF ValidationError (400) of serializer.errors.
type InvoicePartyValidationError struct{ Errors InvoicePartyErrors }

func (e *InvoicePartyValidationError) Error() string { return "invalid invoice party input" }

// Invoice-party codes; "text:" carries a message the view raises verbatim.
const (
	InvoicePartyMaxValue       = "max-value:"
	InvoicePartyText           = "text:"
	InvoicePartyUniqueTogether = "unique-together:customer-invoice"
)

// InvoicePartyRelated is one present PrimaryKeyRelatedField value before its
// queryset lookup: nil ID is None; Raw is str(value) for the does-not-exist
// message.
type InvoicePartyRelated struct {
	Present bool
	ID      *string
	Raw     string
}

// --- Python value semantics ------------------------------------------------

// PythonAuditStr is smart_str(value) of a raw request value assigned to a
// model attribute (auditlog's diff side).
func PythonAuditStr(value any) string { return PythonStr(value) }

// DjangoBooleanToPython is models.BooleanField.to_python for a non-null
// field: (value, ok); nil means None (the NOT NULL constraint then fails).
func DjangoBooleanToPython(value any) (*bool, bool) {
	yes, no := true, false
	switch v := value.(type) {
	case nil:
		return nil, true
	case bool:
		return &v, true
	case json.Number:
		number, ok := new(big.Float).SetString(string(v))
		if ok && number.Cmp(big.NewFloat(1)) == 0 {
			return &yes, true
		}
		if ok && number.Sign() == 0 {
			return &no, true
		}
	case string:
		switch v {
		case "t", "True", "1":
			return &yes, true
		case "f", "False", "0":
			return &no, true
		}
	}
	return nil, false
}

// drfBoolean is serializers.BooleanField.to_internal_value (exact value sets).
func drfBoolean(value any, null bool) (*bool, string) {
	yes, no := true, false
	switch v := value.(type) {
	case nil:
		if null {
			return nil, ""
		}
		return nil, PaymentCategoryValidationNull
	case bool:
		return &v, ""
	case json.Number:
		number, ok := new(big.Float).SetString(string(v))
		if ok && number.Cmp(big.NewFloat(1)) == 0 {
			return &yes, ""
		}
		if ok && number.Sign() == 0 {
			return &no, ""
		}
	case string:
		switch v {
		case "t", "T", "y", "Y", "yes", "Yes", "YES", "true", "True", "TRUE", "on", "On", "ON", "1":
			return &yes, ""
		case "f", "F", "n", "N", "no", "No", "NO", "false", "False", "FALSE", "off", "Off", "OFF", "0":
			return &no, ""
		case "null", "Null", "NULL", "":
			if null {
				return nil, ""
			}
		}
	}
	return nil, PaymentCategoryValidationBoolean
}

// drfUUID is serializers.UUIDField.to_internal_value (hex_verbose): ints
// (bools included) through uuid.UUID(int=), strings through uuid.UUID(hex=).
func drfUUID(value any) (string, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return "00000000-0000-0000-0000-000000000001", true
		}
		return "00000000-0000-0000-0000-000000000000", true
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "", false
		}
	}
	return supplierUUID(value)
}

// djangoUUIDLookup is UUIDField.to_python for a queryset filter value: ok
// false is the ValidationError (a 500 in a view's own filter).
func DjangoUUIDLookup(value any) (string, bool) {
	switch value.(type) {
	case string, json.Number, bool:
		return drfUUID(value)
	}
	return "", false
}

// drfDateTime is serializers.DateTimeField.to_internal_value (ISO 8601,
// enforce_timezone to UTC).
func drfDateTime(value any, null bool) (*time.Time, string) {
	if value == nil {
		if null {
			return nil, ""
		}
		return nil, PaymentCategoryValidationNull
	}
	text, ok := value.(string)
	if !ok {
		return nil, PaymentCategoryValidationDateTime
	}
	parsed, err := parsePaymentCategoryDjangoDateTime(text)
	if err == errPaymentCategoryDateTimeOverflow {
		return nil, PaymentCategoryValidationDateTimeOverflow
	}
	if err != nil {
		return nil, PaymentCategoryValidationDateTime
	}
	parsed = parsed.UTC()
	return &parsed, ""
}

// drfInteger is IntegerField with min/max values.
func drfInteger(value any, null bool, minimum, maximum int64) (*int64, []string) {
	if value == nil {
		if null {
			return nil, nil
		}
		return nil, []string{PaymentCategoryValidationNull}
	}
	number, code := DRFInteger(value)
	if code != "" {
		return nil, []string{code}
	}
	var codes []string
	if number.Cmp(big.NewInt(maximum)) > 0 {
		codes = append(codes, InvoicePartyMaxValue+strconv.FormatInt(maximum, 10))
	}
	if number.Cmp(big.NewInt(minimum)) < 0 {
		codes = append(codes, "min-value:"+strconv.FormatInt(minimum, 10))
	}
	if codes != nil {
		return nil, codes
	}
	result := number.Int64()
	return &result, nil
}

// drfDecimal is DecimalField (not null) quantized text.
func drfDecimal(value any, maxDigits, places int) (string, string) {
	if value == nil {
		return "", PaymentCategoryValidationNull
	}
	return DRFDecimal(value, maxDigits, places)
}

// drfRelated is PrimaryKeyRelatedField's format step on a UUID key (” is None).
func drfRelated(value any, null bool) (InvoicePartyRelated, string) {
	if value == "" {
		value = nil
	}
	if value == nil {
		if null {
			return InvoicePartyRelated{Present: true}, ""
		}
		return InvoicePartyRelated{}, PaymentCategoryValidationNull
	}
	id, code := supplierRelated(value)
	if code != "" {
		return InvoicePartyRelated{}, code
	}
	return InvoicePartyRelated{Present: true, ID: &id, Raw: PythonStr(value)}, ""
}

// nestedObject is a nested serializer field's type check: nil is None; a
// non-dict is {'non_field_errors': ['Invalid data. Expected a dictionary, but got <type>.']}.
func nestedObject(value any, null bool) (isNull, isObject bool, errors any) {
	switch value.(type) {
	case nil:
		if null {
			return true, false, nil
		}
		return false, false, []string{PaymentCategoryValidationNull}
	case domain.RequestObject:
		return false, true, nil
	}
	return false, false, map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(value)}}
}

// --- invoice_customers_add ------------------------------------------------

type invoicePartyText struct {
	Name        string
	Max         int
	Null, Blank bool
}

// customerInvoiceTexts are CustomerInvoiceSerializer's CharFields (model options).
var customerInvoiceTexts = []invoicePartyText{
	{"prefix", 10, true, false},
	{"assignor_prefix_vat_number", 3, false, false}, {"assignor_vat_number", 20, true, false}, {"assignor_tax_code", 20, true, false},
	{"assignor_denomination", 255, true, false}, {"assignor_fiscal_regime", 255, false, false}, {"assignor_address", 255, false, false},
	{"assignor_postal_code", 10, false, false}, {"assignor_city", 255, false, false}, {"assignor_province", 2, false, false},
	{"assignor_country", 2, false, false}, {"assignor_contact_email", 255, true, true}, {"assignor_contact_phone", 255, true, true},
	{"assignor_fax", 255, true, true},
	{"transferor_prefix_vat_number", 3, false, false}, {"transferor_vat_number", 20, true, false}, {"transferor_tax_code", 20, true, false},
	{"transferor_denomination", 255, true, false}, {"transferor_fiscal_regime", 255, false, false}, {"transferor_address", 255, false, false},
	{"transferor_postal_code", 10, false, false}, {"transferor_city", 255, false, false}, {"transferor_province", 2, false, false},
	{"transferor_country", 2, false, false}, {"transferor_contact_email", 255, true, true}, {"transferor_contact_phone", 255, true, true},
	{"transferor_fax", 255, true, true},
	{"country_transmitter_prefix", 3, false, false}, {"id_transmitter", 20, true, false}, {"transmitting_format", 10, false, false},
	{"document_type", 10, false, false}, {"currency", 3, false, false}, {"causal", 200, true, false}, {"xml", 0, true, false},
}

var customerInvoiceChoices = map[string][]string{
	"payment_condition": {"TP01", "TP02", "TP03"},
	"payment_modality": {"MP01", "MP02", "MP03", "MP04", "MP05", "MP06", "MP07", "MP08", "MP09", "MP10", "MP11", "MP12", "MP13",
		"MP14", "MP15", "MP16", "MP17", "MP18", "MP19", "MP20", "MP21", "MP22"},
}

// CustomerInvoiceAdd is the validated data the serializer-level validators
// and validate() read, plus the relations the service looks up.
type CustomerInvoiceAdd struct {
	ID                 *string
	Prefix             *string // default 'FPR'
	Number, FiscalYear *int64  // default None
	IDTransmitter      *string
	IDTransmitterSet   bool
	ContactEmail       *string
	ContactEmailSet    bool
	PDF, Payment       InvoicePartyRelated
}

// ParseCustomerInvoiceAdd ports CustomerInvoiceSerializer field validation
// (the view already replaced both dates with valid DD/MM/YYYY text and the
// association with the user's own).
func ParseCustomerInvoiceAdd(object domain.RequestObject) (CustomerInvoiceAdd, InvoicePartyErrors) {
	fpr := "FPR"
	input := CustomerInvoiceAdd{Prefix: &fpr}
	errors := InvoicePartyErrors{}
	if value, present := object.Lookup("customer_invoice_id"); present {
		if value == nil {
			errors.add("customer_invoice_id", PaymentCategoryValidationNull)
		} else if id, ok := drfUUID(value); ok {
			input.ID = &id
		} else {
			errors.add("customer_invoice_id", PaymentCategoryValidationUUID)
		}
	}
	for _, field := range customerInvoiceTexts {
		value, present := object.Lookup(field.Name)
		if !present {
			continue
		}
		text, codes := modelTextValue(SupplierField{Name: field.Name, Max: field.Max, Null: field.Null, Blank: field.Blank}, value, false)
		if len(codes) > 0 {
			errors.add(field.Name, codes...)
			continue
		}
		switch field.Name {
		case "prefix":
			input.Prefix = text
		case "id_transmitter":
			input.IDTransmitter, input.IDTransmitterSet = text, true
		case "transferor_contact_email":
			input.ContactEmail, input.ContactEmailSet = text, true
		}
	}
	for _, name := range []string{"number", "fiscal_year"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		number, codes := drfInteger(value, true, math.MinInt32, math.MaxInt32)
		if codes != nil {
			errors.add(name, codes...)
		} else if name == "number" {
			input.Number = number
		} else {
			input.FiscalYear = number
		}
	}
	for _, name := range []string{"e_invoice", "paid", "transmitted"} {
		if value, present := object.Lookup(name); present {
			if _, code := drfBoolean(value, false); code != "" {
				errors.add(name, code)
			}
		}
	}
	for name, choices := range customerInvoiceChoices {
		value, present := object.Lookup(name)
		if !present || value == nil {
			continue
		}
		text := PythonStr(value)
		valid := false
		for _, choice := range choices {
			valid = valid || choice == text
		}
		if !valid {
			errors.add(name, "choice:"+text)
		}
	}
	if value, present := object.Lookup("payment_total_amount"); present {
		if _, code := drfDecimal(value, 9, 2); code != "" {
			errors.add("payment_total_amount", code)
		}
	}
	if value, present := object.Lookup("creation_date"); present {
		if _, code := drfDateTime(value, false); code != "" {
			errors.add("creation_date", code)
		}
	}
	for _, name := range []string{"pdf", "payment"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		related, code := drfRelated(value, true)
		if code != "" {
			errors.add(name, code)
		} else if name == "pdf" {
			input.PDF = related
		} else {
			input.Payment = related
		}
	}
	return input, errors
}

// --- invoice_suppliers_add / update ---------------------------------------

// SupplierInvoiceInput is InvoiceSuppliersSerializer's validated data
// (presence flags for a partial update).
type SupplierInvoiceInput struct {
	ID                                            *string
	IdentifierSet, AmountSet, PaymentDateSet      bool
	Identifier, Amount                            string
	PaymentDate                                   *time.Time
	ExpireDateSet, PaidSet, NotesSet, PaymentNull bool
	ExpireDate                                    *time.Time
	Paid                                          bool
	Notes                                         *string
	PaymentIDSet                                  bool
	PaymentID                                     *string
	Association, Document                         InvoicePartyRelated
	// NotPorted: a nested payment or supplier object (validated and written
	// by DRF's nested serializers) or a payment_id to store.
	NotPorted bool
}

// ParseSupplierInvoice ports InvoiceSuppliersSerializer field validation;
// relation existence, tenant scope and uniqueness are checked by the service.
func ParseSupplierInvoice(body any, partial, form bool) (SupplierInvoiceInput, InvoicePartyErrors) {
	input := SupplierInvoiceInput{}
	errors := InvoicePartyErrors{}
	object, ok := body.(domain.RequestObject)
	if !ok {
		errors.add("non_field_errors", "dictionary:"+pythonTypeName(body))
		return input, errors
	}
	lookup := func(name string) (any, bool) {
		value, present := object.Lookup(name)
		if form && present && value == "" {
			return value, present
		}
		return value, present
	}
	if value, present := lookup("invoice_supplier_id"); present {
		if value == nil {
			errors.add("invoice_supplier_id", PaymentCategoryValidationNull)
		} else if id, ok := drfUUID(value); ok {
			input.ID = &id
		} else {
			errors.add("invoice_supplier_id", PaymentCategoryValidationUUID)
		}
	}
	nested := func(name string, null bool) {
		if form {
			// parse_html_dict: only "<name>.<key>" fields form the nested value.
			for _, field := range object {
				if strings.HasPrefix(field.Name, name+".") {
					input.NotPorted = true
				}
			}
			return
		}
		value, present := object.Lookup(name)
		if !present {
			return
		}
		isNull, isObject, problem := nestedObject(value, null)
		switch {
		case problem != nil:
			errors[name] = problem
		case isObject:
			input.NotPorted = true
		case isNull && name == "payment":
			input.PaymentNull = true
		}
	}
	nested("payment", true)
	text := func(name string, field SupplierField) (*string, bool) {
		value, present := lookup(name)
		if !present {
			if field.Required && !partial {
				errors.add(name, PaymentCategoryValidationRequired)
			}
			return nil, false
		}
		result, codes := modelTextValue(field, value, form)
		if len(codes) > 0 {
			errors.add(name, codes...)
			return nil, false
		}
		return result, true
	}
	if value, set := text("payment_id", SupplierField{Null: true, Blank: true}); set {
		input.PaymentIDSet, input.PaymentID = true, value
		if value != nil {
			input.NotPorted = true
		}
	}
	if value, set := text("notes", SupplierField{Null: true, Blank: true}); set {
		input.NotesSet, input.Notes = true, value
	}
	nested("supplier", false)
	if value, set := text("invoice_identifier", SupplierField{Max: 255, Required: true}); set {
		input.IdentifierSet, input.Identifier = true, *value
	}
	if value, present := lookup("amount"); present {
		if amount, code := drfDecimal(value, 9, 2); code != "" {
			errors.add("amount", code)
		} else {
			input.AmountSet, input.Amount = true, amount
		}
	} else if !partial {
		errors.add("amount", PaymentCategoryValidationRequired)
	}
	for _, name := range []string{"payment_date", "expire_date"} {
		value, present := lookup(name)
		if !present {
			continue
		}
		if form && value == "" {
			value = nil
		}
		moment, code := drfDateTime(value, true)
		if code != "" {
			errors.add(name, code)
		} else if name == "payment_date" {
			input.PaymentDateSet, input.PaymentDate = true, moment
		} else {
			input.ExpireDateSet, input.ExpireDate = true, moment
		}
	}
	if value, present := lookup("paid"); present {
		if flag, code := drfBoolean(value, false); code != "" {
			errors.add("paid", code)
		} else {
			input.PaidSet, input.Paid = true, *flag
		}
	} else if form && !partial {
		// BooleanField.default_empty_html in a full HTML form.
		input.PaidSet = true
	}
	for _, name := range []string{"sport_association", "document_pdf"} {
		value, present := lookup(name)
		if !present {
			if name == "sport_association" && !partial {
				errors.add(name, PaymentCategoryValidationRequired)
			}
			continue
		}
		related, code := drfRelated(value, name == "document_pdf")
		if code != "" {
			errors.add(name, code)
		} else if name == "sport_association" {
			input.Association = related
		} else {
			input.Document = related
		}
	}
	return input, errors
}

// SupplierPaymentEntry is PaymentEntrySerializer(data=payment_data) of the
// add view: amount, paid, payment_date and creation_date are request values.
type SupplierPaymentEntry struct {
	Amount       string
	Paid         bool
	PaymentDate  *time.Time
	CreationDate time.Time
}

// ParseSupplierPaymentEntry validates payment_data; paymentDate is the
// view's data['payment_date'] (already defaulted), paid the raw data['paid'].
func ParseSupplierPaymentEntry(amount, paid, paymentDate any) (SupplierPaymentEntry, InvoicePartyErrors) {
	entry := SupplierPaymentEntry{}
	errors := InvoicePartyErrors{}
	if value, code := drfDecimal(amount, 9, 2); code != "" {
		errors.add("amount", code)
	} else {
		entry.Amount = value
	}
	truthy := PythonTruthy(paid)
	paidValue := any(false)
	if truthy {
		paidValue = paid
	}
	if flag, code := drfBoolean(paidValue, false); code != "" {
		errors.add("paid", code)
	} else {
		entry.Paid = *flag
	}
	if truthy {
		if moment, code := drfDateTime(paymentDate, true); code != "" {
			errors.add("payment_date", code)
		} else {
			entry.PaymentDate = moment
		}
	}
	if moment, code := drfDateTime(paymentDate, false); code != "" {
		errors.add("creation_date", code)
	} else {
		entry.CreationDate = *moment
	}
	return entry, errors
}

// --- invoice_suppliers_list search ------------------------------------------

// PythonStrptimeDMY is datetime.strptime(text, '%d/%m/%Y').strftime('%Y-%m-%d').
func PythonStrptimeDMY(text string) (string, bool) {
	runes := []rune(text)
	at := 0
	ascii := func(i int, low, high rune) bool { return i < len(runes) && runes[i] >= low && runes[i] <= high }
	// %d: 3[01]|[12]\d|0[1-9]|[1-9]| [1-9]
	day := 0
	switch {
	case ascii(at, '3', '3') && ascii(at+1, '0', '1'):
		day, at = 30+int(runes[at+1]-'0'), at+2
	case ascii(at, '1', '2') && at+1 < len(runes) && unicode.IsDigit(runes[at+1]):
		value, _ := pythonDigit(runes[at+1])
		day, at = 10*int(runes[at]-'0')+value, at+2
	case ascii(at, '0', '0') && ascii(at+1, '1', '9'):
		day, at = int(runes[at+1]-'0'), at+2
	case ascii(at, '1', '9'):
		day, at = int(runes[at]-'0'), at+1
	case ascii(at, ' ', ' ') && ascii(at+1, '1', '9'):
		day, at = int(runes[at+1]-'0'), at+2
	default:
		return "", false
	}
	if !ascii(at, '/', '/') {
		return "", false
	}
	at++
	month := 0
	switch {
	case ascii(at, '1', '1') && ascii(at+1, '0', '2'):
		month, at = 10+int(runes[at+1]-'0'), at+2
	case ascii(at, '0', '0') && ascii(at+1, '1', '9'):
		month, at = int(runes[at+1]-'0'), at+2
	case ascii(at, '1', '9'):
		month, at = int(runes[at]-'0'), at+1
	default:
		return "", false
	}
	if !ascii(at, '/', '/') {
		return "", false
	}
	at++
	year := 0
	for i := 0; i < 4; i++ {
		if at >= len(runes) {
			return "", false
		}
		value, ok := pythonDigit(runes[at])
		if !ok {
			return "", false
		}
		year, at = year*10+value, at+1
	}
	if at != len(runes) || year < 1 || day > auditDaysIn(year, month) {
		return "", false
	}
	return time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC).Format("2006-01-02"), true
}

// PythonFloatParse is float(text): the parsed value or ok false (ValueError).
func PythonFloatParse(text string) (float64, bool) {
	text = strings.TrimFunc(text, PythonIsSpace)
	digits := strings.Map(func(r rune) rune {
		if value, ok := pythonDigit(r); ok {
			return '0' + rune(value)
		}
		return r
	}, text)
	body := strings.TrimLeft(digits, "+-")
	if len(digits)-len(body) > 1 {
		return 0, false
	}
	switch strings.ToLower(body) {
	case "inf", "infinity":
		if strings.HasPrefix(digits, "-") {
			return math.Inf(-1), true
		}
		return math.Inf(1), true
	case "nan":
		return math.NaN(), true
	}
	match := pythonDecimalLiteral.FindStringSubmatch(digits)
	if match == nil || match[2] == "" && match[3] == "" {
		return 0, false
	}
	value, err := strconv.ParseFloat(strings.ReplaceAll(digits, "_", ""), 64)
	if err != nil {
		if numErr, ok := err.(*strconv.NumError); ok && numErr.Err == strconv.ErrRange {
			return value, true
		}
		return 0, false
	}
	return value, true
}

// PythonFloatStr is str(float) for a finite value.
func PythonFloatStr(value float64) string { return pythonFloatRepr(value) }

// DecimalFromFloat9 is Context(prec=9).create_decimal_from_float(value) text.
func DecimalFromFloat9(value float64) string {
	return strconv.FormatFloat(value, 'e', 8, 64)
}
