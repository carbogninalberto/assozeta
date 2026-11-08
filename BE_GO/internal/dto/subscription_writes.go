package dto

import (
	"regexp"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// AssociateTutorWrite is AssociateSerializer's validated data before the
// related lookups: present keys only, Text nil for None, IDs canonical.
type AssociateTutorWrite struct {
	AssociateID        *string
	Text               map[string]*string
	BornDate           time.Time
	Sex                *string
	IsMinor            *bool
	AssociationPresent bool
	AssociationID      *string
	FamilyPresent      bool
	FamilyID           *string
}

// AssociateTutorField is one field error in the serializer's field order.
type AssociateTutorField struct {
	Name  string
	Codes []string
}

// AssociateTutorInvalid is serializer.errors (codes rendered by the handler).
type AssociateTutorInvalid struct{ Fields []AssociateTutorField }

func (e *AssociateTutorInvalid) Error() string { return "invalid associate tutor data" }

func (e *AssociateTutorInvalid) add(name string, codes ...string) {
	e.Fields = append(e.Fields, AssociateTutorField{Name: name, Codes: codes})
}

// AssociateTutorTextFields are the model CharFields/EmailField of
// AssociateSerializer (all null=True, blank=True), in Meta.fields order with
// their position among the other fields.
var AssociateTutorTextFields = []SupplierField{
	{Name: "address", Max: 150, Null: true, Blank: true},
	{Name: "address_cap", Max: 5, Null: true, Blank: true},
	{Name: "address_city", Max: 150, Null: true, Blank: true},
	{Name: "born_city", Max: 150, Null: true, Blank: true},
	{Name: "email", Max: 254, Null: true, Blank: true, Email: true},
	{Name: "first_name", Max: 150, Null: true, Blank: true},
	{Name: "last_name", Max: 150, Null: true, Blank: true},
	{Name: "tax_code", Max: 25, Null: true, Blank: true},
	{Name: "phone", Max: 30, Null: true, Blank: true},
	{Name: "family_role", Max: 100, Null: true, Blank: true},
}

// AssociateCapMessage is the address_cap RegexValidator message (not translated).
const AssociateCapMessage = "Enter exactly 5 digits"

var associateCap = regexp.MustCompile(`^\p{Nd}{5}$`)

var associateSexChoices = map[string]bool{"A": true, "M": true, "F": true}

func associateText(field SupplierField, value any) (*string, []string) {
	if field.Name != "address_cap" || value == nil {
		return modelTextValue(field, value, false)
	}
	text, codes := parsePaymentCategoryString(value, field.Max)
	if len(codes) == 1 && codes[0] == PaymentCategoryValidationBlank {
		empty := ""
		return &empty, nil
	}
	if len(codes) == 1 && codes[0] == PaymentCategoryValidationString {
		return nil, codes
	}
	// The model RegexValidator runs before the CharField's own validators.
	if !associateCap.MatchString(text) {
		codes = append([]string{AssociateCapMessage}, codes...)
	}
	if len(codes) > 0 {
		return nil, codes
	}
	return &text, nil
}

// ParseAssociateTutor ports AssociateSerializer(data=object) field validation
// in Meta.fields order (born_date was already parsed by the view). related
// is called for sport_association before the later fields, so the tenant
// scope's PermissionDenied can stop validation; existence of the family is
// checked by the caller through familyExists.
func ParseAssociateTutor(object domain.RequestObject, born time.Time,
	association func(id string) (exists bool, err error), familyExists func(id string) (bool, error)) (AssociateTutorWrite, *AssociateTutorInvalid, error) {
	write := AssociateTutorWrite{Text: map[string]*string{}, BornDate: born}
	invalid := &AssociateTutorInvalid{}
	text := func(name string) {
		for _, field := range AssociateTutorTextFields {
			if field.Name != name {
				continue
			}
			value, present := object.Lookup(name)
			if !present {
				return
			}
			parsed, codes := associateText(field, value)
			if len(codes) > 0 {
				invalid.add(name, codes...)
				return
			}
			write.Text[name] = parsed
		}
	}
	related := func(name string, present *bool, target **string, exists func(string) (bool, error)) error {
		value, found := object.Lookup(name)
		if !found {
			return nil
		}
		*present = true
		if value == nil {
			return nil
		}
		id, code := supplierRelated(value)
		if code != "" {
			invalid.add(name, code)
			return nil
		}
		ok, err := exists(id)
		if err != nil {
			return err
		}
		if !ok {
			invalid.add(name, "related-does-not-exist:"+PythonStr(value))
			return nil
		}
		*target = &id
		return nil
	}
	if value, present := object.Lookup("associate_id"); present {
		if value == nil {
			invalid.add("associate_id", PaymentCategoryValidationNull)
		} else if parsed, codes := parsePaymentCategoryString(value, int(^uint(0)>>1)); len(codes) > 0 {
			invalid.add("associate_id", codes...)
		} else {
			write.AssociateID = &parsed
		}
	}
	for _, name := range []string{"address", "address_cap", "address_city", "born_city", "email"} {
		text(name)
	}
	if value, present := object.Lookup("sex"); present {
		switch v := value.(type) {
		case nil:
			invalid.add("sex", PaymentCategoryValidationNull)
		case string:
			if v != "" && !associateSexChoices[v] {
				invalid.add("sex", "choice:"+v)
			} else {
				write.Sex = &v
			}
		default:
			invalid.add("sex", "choice:"+PythonStr(value))
		}
	}
	text("first_name")
	text("last_name")
	if value, present := object.Lookup("is_minor"); present {
		if flag, code := drfBoolean(value, false); code != "" {
			invalid.add("is_minor", code)
		} else {
			write.IsMinor = flag
		}
	}
	if err := related("sport_association", &write.AssociationPresent, &write.AssociationID, association); err != nil {
		return write, nil, err
	}
	text("tax_code")
	text("phone")
	if err := related("family", &write.FamilyPresent, &write.FamilyID, familyExists); err != nil {
		return write, nil, err
	}
	text("family_role")
	if len(invalid.Fields) > 0 {
		return write, invalid, nil
	}
	return write, nil, nil
}

// AssociateTaxCodeASCII reports whether Python's str.upper() and
// PostgreSQL's UPPER agree on the tax code (ASCII only).
func AssociateTaxCodeASCII(text string) bool {
	for _, r := range text {
		if r > 0x7f {
			return false
		}
	}
	return true
}

// AssociateUpper is str.upper() for ASCII text.
func AssociateUpper(text string) string { return strings.ToUpper(text) }
