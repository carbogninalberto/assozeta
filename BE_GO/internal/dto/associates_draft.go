package dto

import (
	"encoding/json"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// associateDraftTextFields are AssociateSerializer's model CharFields (null,
// blank) and the declared associate_id CharField(required=False).
var associateDraftTextFields = map[string]SupplierField{
	"associate_id": {Name: "associate_id"},
	"address":      {Name: "address", Max: 150, Null: true, Blank: true},
	"address_cap":  {Name: "address_cap", Max: 5, Null: true, Blank: true},
	"address_city": {Name: "address_city", Max: 150, Null: true, Blank: true},
	"born_city":    {Name: "born_city", Max: 150, Null: true, Blank: true},
	"email":        {Name: "email", Max: 254, Null: true, Blank: true, Email: true},
	"first_name":   {Name: "first_name", Max: 150, Null: true, Blank: true},
	"last_name":    {Name: "last_name", Max: 150, Null: true, Blank: true},
	"tax_code":     {Name: "tax_code", Max: 25, Null: true, Blank: true},
	"phone":        {Name: "phone", Max: 30, Null: true, Blank: true},
	"family_role":  {Name: "family_role", Max: 100, Null: true, Blank: true},
}

// associateDraftCAP is the model's RegexValidator ^\d{5}$ (\d is any Unicode
// decimal digit) on the stripped value.
var associateDraftCAP = regexp.MustCompile(`^\p{Nd}{5}$`)

// AssociateDraftFields ports AssociateSerializer(data=value) field validation
// before the related lookups: ok is false when a field (or a non-mapping
// value) fails; association and family are the present related primary keys
// the caller must still find (and, for the association, tenant-check).
func AssociateDraftFields(value any) (ok bool, association, family *string) {
	object, isObject := value.(domain.RequestObject)
	if !isObject {
		return false, nil, nil
	}
	ok = true
	for _, field := range object {
		switch field.Name {
		case "born_date":
			if field.Value != nil {
				if _, valid := ParseSubscriptionMembershipDate(field.Value); !valid {
					ok = false
				}
			}
		case "sex":
			text, isText := field.Value.(string)
			if !isText || text != "" && text != "A" && text != "M" && text != "F" {
				ok = false
			}
		case "is_minor":
			if field.Value == nil {
				ok = false
			} else if _, err := parsePaymentCategoryBoolean(field.Value); err != nil {
				ok = false
			}
		case "sport_association", "family":
			// RelatedField.run_validation: '' is None; allow_null.
			if field.Value == nil || field.Value == "" {
				continue
			}
			id, code := supplierRelated(field.Value)
			if code != "" {
				ok = false
				continue
			}
			if field.Name == "family" {
				family = &id
			} else {
				association = &id
			}
		default:
			spec, known := associateDraftTextFields[field.Name]
			if !known {
				continue // read-only user and unknown keys are ignored
			}
			text, codes := modelTextValue(spec, field.Value, false)
			if len(codes) > 0 {
				ok = false
			} else if field.Name == "address_cap" && text != nil && *text != "" && !associateDraftCAP.MatchString(*text) {
				ok = false
			}
		}
	}
	return ok, association, family
}

// DateutilResult classifies dateutil.parser.parse(value) on the ported subset.
type DateutilResult int

const (
	DateutilParsed DateutilResult = iota
	// DateutilFailed: parse raises (TypeError for a non-string, ParserError for
	// an empty or impossible date).
	DateutilFailed
	// DateutilUnsupported: a format outside the ported subset (refused).
	DateutilUnsupported
)

var (
	dateutilISODate     = regexp.MustCompile(`^([0-9]{4})-([0-9]{2})-([0-9]{2})$`)
	dateutilISODateTime = regexp.MustCompile(`^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]{1,6})?(Z|[+-]([0-9]{2}):([0-9]{2}))?$`)
	dateutilSlashes     = regexp.MustCompile(`^([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})$`)
)

// DateutilDate is the calendar date of dateutil.parser.parse(value) (default
// dayfirst=False, yearfirst=False) for YYYY-MM-DD, YYYY-MM-DDTHH:MM:SS[.f][Z|±HH:MM]
// (the date as written) and A/B/YYYY (A is the month unless it exceeds 12).
// Years before 1000 are unsupported: the views round-trip the date through
// strftime('%d/%m/%Y'), whose year width is platform dependent there.
func DateutilDate(value any) (time.Time, DateutilResult) {
	text, ok := value.(string)
	if !ok {
		return time.Time{}, DateutilFailed
	}
	if strings.TrimFunc(text, unicode.IsSpace) == "" {
		return time.Time{}, DateutilFailed
	}
	number := func(digits string) int { n, _ := strconv.Atoi(digits); return n }
	var year, month, day int
	if match := dateutilISODate.FindStringSubmatch(text); match != nil {
		year, month, day = number(match[1]), number(match[2]), number(match[3])
	} else if match := dateutilISODateTime.FindStringSubmatch(text); match != nil {
		year, month, day = number(match[1]), number(match[2]), number(match[3])
		if number(match[4]) > 23 || number(match[5]) > 59 || number(match[6]) > 59 || match[9] != "" && (number(match[9]) > 23 || number(match[10]) > 59) {
			return time.Time{}, DateutilUnsupported
		}
	} else if match := dateutilSlashes.FindStringSubmatch(text); match != nil {
		first, second := number(match[1]), number(match[2])
		year, month, day = number(match[3]), first, second
		if first > 12 {
			month, day = second, first
		}
	} else {
		return time.Time{}, DateutilUnsupported
	}
	if year < 1000 {
		return time.Time{}, DateutilUnsupported
	}
	date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if month < 1 || month > 12 || date.Day() != day || int(date.Month()) != month {
		return time.Time{}, DateutilFailed
	}
	return date, DateutilParsed
}

// associateDraftWalk reports whether every string, key and number of value
// passes; uploads always fail (json.dumps cannot encode them).
func associateDraftWalk(value any, text func(string) bool) bool {
	switch v := value.(type) {
	case nil, bool:
		return true
	case string:
		return text(v)
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			number, _ := strconv.ParseFloat(string(v), 64)
			return !math.IsInf(number, 0)
		}
		return true
	case domain.RequestObject:
		for _, field := range v {
			if !text(field.Name) || !associateDraftWalk(field.Value, text) {
				return false
			}
		}
		return true
	case []any:
		for _, item := range v {
			if !associateDraftWalk(item, text) {
				return false
			}
		}
		return true
	}
	return false
}

func associateDraftSurrogate(text string) bool {
	for i := 0; i+2 < len(text); i++ {
		if text[i] == 0xed && text[i+1] >= 0xa0 && text[i+1] <= 0xbf {
			return true
		}
	}
	return false
}

// AssociateDraftStorable reports whether PostgreSQL accepts json.dumps(value)
// as jsonb: no NUL character, lone surrogate or infinite float (Infinity).
func AssociateDraftStorable(value any) bool {
	return associateDraftWalk(value, func(text string) bool { return !strings.ContainsRune(text, 0) && !associateDraftSurrogate(text) })
}

// AssociateDraftRenderable reports whether DRF's JSONRenderer encodes value:
// allow_nan=False refuses infinite floats and the UTF-8 encode refuses lone
// surrogates.
func AssociateDraftRenderable(value any) bool {
	return associateDraftWalk(value, func(text string) bool { return !associateDraftSurrogate(text) })
}

// SetRequestField is dict item assignment: an existing key keeps its position.
func SetRequestField(object domain.RequestObject, name string, value any) domain.RequestObject {
	for i := range object {
		if object[i].Name == name {
			object[i].Value = value
			return object
		}
	}
	return append(object, domain.RequestField{Name: name, Value: value})
}
