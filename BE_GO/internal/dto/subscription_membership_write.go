package dto

import (
	"errors"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// SubscriptionMembershipTextFields are SubscriptionMembership's CharFields in
// declaration order.
var SubscriptionMembershipTextFields = []SupplierField{
	{Name: "membership_type", Max: 255, Null: true},
	{Name: "membership_number", Max: 50, Null: true, Blank: true},
	{Name: "description", Max: 255, Null: true, Blank: true},
}

const (
	SubscriptionMembershipUnique = "unique:subscription membership:subscription membership id"
	// SubscriptionMembershipDate is DateField's 'invalid' with the project's
	// DATE_INPUT_FORMATS ['%d/%m/%Y'].
	SubscriptionMembershipDate = "date:DD/MM/YYYY"
)

// SubscriptionMembershipRelation is a present related-field value: nil ID is
// None.
type SubscriptionMembershipRelation struct {
	Present bool
	ID      *string
}

// SubscriptionMembershipWrite is SubscriptionMembershipSerializer's validated
// data before the related-field lookups. Text, Dates and Price hold present
// fields (nil for None).
type SubscriptionMembershipWrite struct {
	ID           *string
	Association  SubscriptionMembershipRelation
	Associate    SubscriptionMembershipRelation
	Payment      SubscriptionMembershipRelation
	CreationDate *time.Time
	Dates        map[string]*string
	Text         map[string]*string
	PricePresent bool
	Price        *string
	Documents    []domain.RequestObject
	// FormDocuments marks form keys for documents_data (ListField HTML input,
	// not ported).
	FormDocuments bool
}

// SubscriptionMembershipQueryUUID is UUIDField.to_python for a query value.
func SubscriptionMembershipQueryUUID(value string) (string, bool) { return supplierUUID(value) }

// subscriptionMembershipDatePattern is _strptime's regex for '%d/%m/%Y'
// (\d is any Unicode decimal digit); the whole input must be consumed.
var subscriptionMembershipDatePattern = regexp.MustCompile(`^(3[01]|[12]\p{Nd}|0[1-9]|[1-9]| [1-9])/(1[0-2]|0[1-9]|[1-9])/(\p{Nd}{4})$`)

// ParseSubscriptionMembershipDate parses DateField input with
// datetime.strptime(value, '%d/%m/%Y') and renders date.isoformat().
func ParseSubscriptionMembershipDate(value any) (string, bool) {
	text, ok := value.(string)
	if !ok {
		return "", false
	}
	match := subscriptionMembershipDatePattern.FindStringSubmatch(text)
	if match == nil {
		return "", false
	}
	number := func(digits string) int {
		result := 0
		for _, r := range strings.TrimSpace(digits) {
			digit, _ := pythonDigit(r)
			result = result*10 + digit
		}
		return result
	}
	day, month, year := number(match[1]), number(match[2]), number(match[3])
	if year < 1 {
		return "", false
	}
	date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if date.Day() != day || int(date.Month()) != month {
		return "", false
	}
	return date.Format("2006-01-02"), true
}

// subscriptionMembershipRelated is a PrimaryKeyRelatedField value: ” is
// None (RelatedField.run_validation), None needs allow_null.
func subscriptionMembershipRelated(value any, null bool) (SubscriptionMembershipRelation, []string) {
	if value == "" {
		value = nil
	}
	if value == nil {
		if !null {
			return SubscriptionMembershipRelation{}, []string{PaymentCategoryValidationNull}
		}
		return SubscriptionMembershipRelation{Present: true}, nil
	}
	id, code := supplierRelated(value)
	if code != "" {
		return SubscriptionMembershipRelation{}, []string{code}
	}
	return SubscriptionMembershipRelation{Present: true, ID: &id}, nil
}

// subscriptionMembershipFiles reads a multipart file the way each field does: the related fields and the price
// see str(file) (the uploaded name, DRF quotes it or reads it as a number), every other field sees an object it
// rejects (a list stands in: not a string, not a date, not a UUID).
func subscriptionMembershipFiles(object domain.RequestObject) domain.RequestObject {
	var copied domain.RequestObject
	for index, field := range object {
		file, ok := field.Value.(domain.RequestFile)
		if !ok {
			continue
		}
		if copied == nil {
			copied = append(domain.RequestObject(nil), object...)
		}
		switch field.Name {
		case "sport_association", "associate", "payment", "price":
			copied[index].Value = file.Name
		default:
			copied[index].Value = []any{}
		}
	}
	if copied == nil {
		return object
	}
	return copied
}

// SubscriptionMembershipHasFile reports whether a parsed multipart body carries a file part (DRF then copies the
// data into a mutable dict that add can assign to).
func SubscriptionMembershipHasFile(body any) bool {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return false
	}
	for _, field := range object {
		if _, isFile := field.Value.(domain.RequestFile); isFile {
			return true
		}
	}
	return false
}

// ParseSubscriptionMembershipWrite ports SubscriptionMembershipSerializer
// field validation (partial or not; every field is optional except
// sport_association, which the add view always supplies). Uniqueness, the
// related querysets and the tenant scope are checked by the service.
func ParseSubscriptionMembershipWrite(body any, form bool) (SubscriptionMembershipWrite, map[string]any) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return SubscriptionMembershipWrite{}, map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(body)}}
	}
	object = subscriptionMembershipFiles(object)
	write := SubscriptionMembershipWrite{Dates: map[string]*string{}, Text: map[string]*string{}}
	errs := map[string]any{}
	if value, present := object.Lookup("subscription_membership_id"); present {
		if value == nil {
			errs["subscription_membership_id"] = []string{PaymentCategoryValidationNull}
		} else if id, ok := supplierUUID(value); ok {
			write.ID = &id
		} else {
			errs["subscription_membership_id"] = []string{PaymentCategoryValidationUUID}
		}
	}
	relations := []struct {
		name   string
		null   bool
		target *SubscriptionMembershipRelation
	}{{"sport_association", false, &write.Association}, {"associate", true, &write.Associate}, {"payment", true, &write.Payment}}
	for _, relation := range relations {
		if value, present := object.Lookup(relation.name); present {
			parsed, codes := subscriptionMembershipRelated(value, relation.null)
			if codes != nil {
				errs[relation.name] = codes
			} else {
				*relation.target = parsed
			}
		}
	}
	if value, present := object.Lookup("creation_date"); present && !(form && value == "") {
		// An empty HTML value of this non-null, non-required field is skipped.
		if value == nil {
			errs["creation_date"] = []string{PaymentCategoryValidationNull}
		} else if text, ok := value.(string); !ok {
			errs["creation_date"] = []string{PaymentCategoryValidationDateTime}
		} else if parsed, err := parsePaymentCategoryDate(text); err != nil {
			if errors.Is(err, errPaymentCategoryDateTimeOverflow) {
				errs["creation_date"] = []string{PaymentCategoryValidationDateTimeOverflow}
			} else {
				errs["creation_date"] = []string{PaymentCategoryValidationDateTime}
			}
		} else {
			write.CreationDate = &parsed
		}
	}
	for _, name := range []string{"start_date", "end_date"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		if form && value == "" {
			value = nil
		}
		if value == nil {
			write.Dates[name] = nil
		} else if date, ok := ParseSubscriptionMembershipDate(value); ok {
			write.Dates[name] = &date
		} else {
			errs[name] = []string{SubscriptionMembershipDate}
		}
	}
	for _, field := range SubscriptionMembershipTextFields {
		value, present := object.Lookup(field.Name)
		if !present {
			continue
		}
		if form && value == "" && field.Blank {
			empty := ""
			write.Text[field.Name] = &empty
			continue
		}
		text, codes := modelTextValue(field, value, form)
		if len(codes) > 0 {
			errs[field.Name] = codes
			continue
		}
		write.Text[field.Name] = text
	}
	if value, present := object.Lookup("price"); present {
		write.PricePresent = true
		// DecimalField.validate_empty_values: a blank value of an allow_null
		// decimal is None.
		if value == nil || PythonStrip(PythonStr(value)) == "" {
			write.Price = nil
		} else if price, code := DRFDecimal(value, 9, 2); code != "" {
			errs["price"] = []string{code}
			write.PricePresent = false
		} else {
			write.Price = &price
		}
	}
	if form {
		for _, field := range object {
			write.FormDocuments = write.FormDocuments || strings.HasPrefix(field.Name, "documents_data")
		}
	} else if value, present := object.Lookup("documents_data"); present {
		switch items := value.(type) {
		case nil:
			errs["documents_data"] = []string{PaymentCategoryValidationNull}
		case []any:
			children := map[string]any{}
			for index, item := range items {
				switch child := item.(type) {
				case domain.RequestObject:
					write.Documents = append(write.Documents, child)
				case nil:
					children[strconv.Itoa(index)] = []string{PaymentCategoryValidationNull}
				default:
					children[strconv.Itoa(index)] = []string{courseLocationDict + pythonTypeName(item)}
				}
			}
			if len(children) > 0 {
				errs["documents_data"] = children
			}
		default:
			errs["documents_data"] = []string{courseLocationList + pythonTypeName(value)}
		}
	}
	if len(errs) > 0 {
		return write, errs
	}
	return write, nil
}
