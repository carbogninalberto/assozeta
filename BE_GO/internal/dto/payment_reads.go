package dto

import (
	"encoding/json"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PaymentReadsJSONTruthy is Python truthiness of a JSONField value (stored jsonb text).
func PaymentReadsJSONTruthy(raw *string) bool {
	if raw == nil {
		return false
	}
	decoder := json.NewDecoder(strings.NewReader(*raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return true
	}
	switch typed := value.(type) {
	case nil:
		return false
	case bool:
		return typed
	case string:
		return typed != ""
	case []any:
		return len(typed) > 0
	case map[string]any:
		return len(typed) > 0
	case json.Number:
		number, ok := new(big.Float).SetString(string(typed))
		return !ok || number.Sign() != 0
	}
	return true
}

func paymentReadsOptional(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

// paymentReadsAssociate is AssociateOptimizedSerializer: available_tutors are
// AssociateTutorRelationSerializer over the tutor relations.
func paymentReadsAssociate(row domain.PaymentReadsAssociate, relations, second, third []domain.PersonaRelation) map[string]any {
	tutors := []any{}
	for _, relation := range relations {
		tutors = append(tutors, domain.RequestObject{{Name: "tutor", Value: personaBasicAssociate(relation.PersonaAssociate, second, third)},
			{Name: "is_primary", Value: relation.IsPrimary}})
	}
	return map[string]any{"associate_id": row.ID, "first_name": paymentReadsOptional(row.FirstName),
		"last_name": paymentReadsOptional(row.LastName), "is_minor": row.IsMinor, "available_tutors": tutors}
}

// paymentReadsInvoice is InvoiceSerializer (fields '__all__' plus document_token).
func paymentReadsInvoice(row domain.PaymentReadsInvoice) (map[string]any, error) {
	attached := []string{}
	if err := json.Unmarshal([]byte(row.Attached), &attached); err != nil {
		return nil, err
	}
	var number, imported any
	if row.Number != nil {
		number = *row.Number
	}
	if row.Imported != nil {
		imported = *row.Imported
	}
	var token any
	if row.DocumentID != nil {
		token = paymentReadsOptional(row.DocumentToken)
	}
	return map[string]any{"invoice_id": row.ID, "document_token": token, "creation_date": DRFUTCDateTime(row.CreationDate),
		"membership_fee": row.MembershipFee, "activity_fee": row.ActivityFee, "meta_payment_categories": invoicePartyJSON(row.MetaPaymentCategories),
		"description": paymentReadsOptional(row.Description), "number": number, "archived": row.Archived, "cancelled": row.Cancelled,
		"meta": invoicePartyJSON(row.Meta), "imported_from_associami": imported, "deleted": row.Deleted,
		"document_pdf": paymentReadsOptional(row.DocumentID), "sport_association": row.AssociationID,
		"selected_tutor": paymentReadsOptional(row.SelectedTutorID), "group": paymentReadsOptional(row.GroupID), "attached_documents": attached}, nil
}

// PaymentReadsItem is PaymentOptimizedSerializer(payment).data.
func PaymentReadsItem(render domain.PaymentReadsRender) (map[string]any, error) {
	payment := render.Payment
	payment.Attachments = render.Attachments
	item := InvoicePartyPaymentItem(payment)
	delete(item, "user")
	item["user"] = paymentReadsOptional(payment.UserID)
	if render.Associate != nil {
		item["associate"] = paymentReadsAssociate(*render.Associate, render.Relations, render.Second, render.Third)
	}
	if render.Instructor != nil {
		instructor, err := InstructorItemFrom(*render.Instructor)
		if err != nil {
			return nil, err
		}
		item["instructor"], item["instructor_id"] = instructor, render.Instructor.ID
	}
	if render.Invoice != nil {
		invoice, err := paymentReadsInvoice(*render.Invoice)
		if err != nil {
			return nil, err
		}
		item["invoice"] = invoice
	}
	switch {
	case payment.Subject != 2:
		item["course"] = nil
	case render.Course != nil:
		item["course"] = map[string]any{"title": paymentReadsOptional(render.Course.Title), "course_id": render.Course.ID}
	case PaymentReadsJSONTruthy(payment.Course):
		item["course"] = json.RawMessage(*payment.Course)
	default:
		item["course"] = nil
	}
	return item, nil
}

// PaymentReadsCompressedItem is one compressed values() row (DRF's encoder renders Decimal as float).
func PaymentReadsCompressedItem(row domain.PaymentReadsCompressed) (map[string]any, error) {
	amount, err := strconv.ParseFloat(row.Amount, 64)
	if err != nil {
		return nil, err
	}
	return map[string]any{"payment_id": row.ID, "amount": json.Number(PythonFloatStr(amount)), "payment_date": invoicePartyTime(row.PaymentDate),
		"paid": row.Paid, "associate": paymentReadsOptional(row.AssociateID), "supplier": paymentReadsOptional(row.SupplierID),
		"instructor": paymentReadsOptional(row.InstructorID), "associate__first_name": paymentReadsOptional(row.AssociateFirstName),
		"associate__last_name": paymentReadsOptional(row.AssociateLastName), "supplier__name": paymentReadsOptional(row.SupplierName),
		"instructor__first_name": paymentReadsOptional(row.InstructorFirstName),
		"instructor__last_name":  paymentReadsOptional(row.InstructorLastName), "custom_accounts__name": paymentReadsOptional(row.AccountName)}, nil
}

// PaymentReadsDecimal parses a PostgreSQL numeric text exactly.
func PaymentReadsDecimal(text string) (*big.Rat, bool) {
	return new(big.Rat).SetString(text)
}

// PaymentReadsFloat is json.dumps(float(Decimal)).
func PaymentReadsFloat(value *big.Rat) json.Number {
	number, _ := value.Float64()
	return json.Number(PythonFloatStr(number))
}

// PythonStrptimeYMD is datetime.strptime(text, '%Y/%m/%d') (UTC midnight).
func PythonStrptimeYMD(text string) (time.Time, bool) {
	runes := []rune(text)
	at := 0
	ascii := func(i int, low, high rune) bool { return i < len(runes) && runes[i] >= low && runes[i] <= high }
	year := 0
	for i := 0; i < 4; i++ {
		if at >= len(runes) {
			return time.Time{}, false
		}
		value, ok := pythonDigit(runes[at])
		if !ok {
			return time.Time{}, false
		}
		year, at = year*10+value, at+1
	}
	if !ascii(at, '/', '/') {
		return time.Time{}, false
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
		return time.Time{}, false
	}
	if !ascii(at, '/', '/') {
		return time.Time{}, false
	}
	at++
	// %d: 3[01]|[12]\d|0[1-9]|[1-9]| [1-9], the first alternative that matches.
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
		return time.Time{}, false
	}
	if at != len(runes) || year < 1 || day > auditDaysIn(year, month) {
		return time.Time{}, false
	}
	return time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC), true
}

// PythonStrptimeDMYTime is datetime.strptime(text, '%d/%m/%Y') (UTC midnight).
func PythonStrptimeDMYTime(text string) (time.Time, bool) {
	value, ok := PythonStrptimeDMY(text)
	if !ok {
		return time.Time{}, false
	}
	moment, err := time.Parse("2006-01-02", value)
	return moment, err == nil
}
