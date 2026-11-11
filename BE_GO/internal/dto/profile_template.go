package dto

import (
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/bs4prettify"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrProfileTemplatePython is an exception profile_update_subscription_template
// raises before save(): KeyError, TypeError, AttributeError or ValueError. The
// view answers the 500 page and nothing is written.
var ErrProfileTemplatePython = errors.New("profile template view exception")

// ErrProfileTemplateNotPorted refuses, before any write, a value whose Python
// conversion this port does not reproduce (see profile-template.md).
var ErrProfileTemplateNotPorted = refusal.New("profile template input not ported")

// ProfileTemplateKind is the model field type that decides str()/json.dumps()
// for auditlog and the column conversion.
type ProfileTemplateKind int

const (
	ProfileTemplateText ProfileTemplateKind = iota
	ProfileTemplateJSON
	ProfileTemplateBoolean
	ProfileTemplateDecimal
)

// ProfileTemplateField is one attribute the view assigns, with its in-memory
// Python value (request values, prettified strings, rebuilt lists, floats).
type ProfileTemplateField struct {
	Name  string
	Kind  ProfileTemplateKind
	Value any
}

// ProfileTemplatePlan is the view's assignments before compress_base64: Logo
// is the logo to compress (nil assigns None).
type ProfileTemplatePlan struct {
	Fields []ProfileTemplateField
	Logo   *string
	// RawLogo is a logo that is not a string: compress_base64 returns it
	// unchanged and the TextField stores its str().
	RawLogo any
}

var profileTemplateKeys = []string{"additional_sections", "regulation", "demand", "show_demand_to_athletes", "show_demand_to_members",
	"show_demand_to_both", "show_regulation_to_athletes", "show_regulation_to_members", "show_regulation_to_both", "logo",
	"subscription_fee", "multiple_subscription_fee", "subscription_fee_plans", "membership_fee", "multiple_membership_fee",
	"membership_fee_plans", "enable_quotes_management", "enabled_for", "additional_fields", "stripe_available_methods",
	"invoice_template", "subscription_template", "extra_text_invoices"}

// PlanProfileTemplate follows the view from the first initial_data access to
// the assignments before sport_association.save().
func PlanProfileTemplate(data domain.RequestObject) (*ProfileTemplatePlan, error) {
	values := map[string]any{}
	for _, key := range profileTemplateKeys {
		value, ok := data.Lookup(key)
		if !ok {
			return nil, ErrProfileTemplatePython // KeyError
		}
		values[key] = value
	}
	plan := &ProfileTemplatePlan{}
	assign := func(name string, kind ProfileTemplateKind, value any) {
		for i := range plan.Fields {
			if plan.Fields[i].Name == name {
				plan.Fields[i].Value = value
				return
			}
		}
		plan.Fields = append(plan.Fields, ProfileTemplateField{name, kind, value})
	}
	if sections := values["additional_sections"]; sections != nil {
		items, err := profileTemplateItems(sections)
		if err != nil {
			return nil, err
		}
		kept := []any{}
		for _, item := range items {
			section, ok := item.(domain.RequestObject)
			if !ok {
				return nil, ErrProfileTemplatePython // section.keys()
			}
			name, hasName := section.Lookup("name")
			text, hasText := section.Lookup("text")
			if !hasName || !hasText || name == "" || text == "" {
				continue
			}
			pretty, err := profileTemplatePrettify(text)
			if err != nil {
				return nil, err
			}
			label, ok := name.(string)
			if !ok {
				return nil, ErrProfileTemplatePython // .upper()
			}
			upper, ok := ProfileTemplateUpper(label)
			if !ok {
				return nil, ErrProfileTemplateNotPorted
			}
			copied := append(domain.RequestObject(nil), section...)
			copied = PythonSet(copied, "text", pretty)
			copied = PythonSet(copied, "name", upper)
			kept = append(kept, copied)
		}
		assign("additional_sections", ProfileTemplateJSON, kept)
	}
	for _, name := range []string{"regulation", "demand"} {
		if value := values[name]; value != nil {
			pretty, err := profileTemplatePrettify(value)
			if err != nil {
				return nil, err
			}
			assign(name, ProfileTemplateText, pretty)
		}
	}
	for _, name := range []string{"show_demand_to_athletes", "show_demand_to_members", "show_demand_to_both",
		"show_regulation_to_athletes", "show_regulation_to_members", "show_regulation_to_both"} {
		if value := values[name]; value != nil {
			assign(name, ProfileTemplateBoolean, value)
		}
	}
	switch logo := values["logo"].(type) {
	case nil:
	case string:
		plan.Logo = &logo
	default:
		plan.RawLogo = logo
	}
	for _, kind := range []string{"subscription", "membership"} {
		fee := values[kind+"_fee"]
		if kind == "membership" && fee == "" {
			assign("membership_fee", ProfileTemplateDecimal, nil)
		} else if fee != nil {
			number, err := ProfileTemplateFloat(fee)
			if err != nil {
				return nil, err
			}
			if number >= 0 {
				assign(kind+"_fee", ProfileTemplateDecimal, fee)
			}
		}
		multiple := "multiple_" + kind + "_fee"
		if value := values[multiple]; value != nil {
			assign(multiple, ProfileTemplateBoolean, value)
		}
		plans := values[kind+"_fee_plans"]
		if plans == nil {
			continue
		}
		items, err := profileTemplateItems(plans)
		if err != nil {
			return nil, err
		}
		if len(items) == 0 {
			assign(multiple, ProfileTemplateBoolean, false)
			assign(kind+"_fee_plans", ProfileTemplateJSON, []any{})
			continue
		}
		kept := []any{}
		for _, item := range items {
			plan, ok := item.(domain.RequestObject)
			if !ok {
				return nil, ErrProfileTemplatePython // plan['..._fee'] on a non-dict
			}
			key := kind + "_fee"
			fee, present := plan.Lookup(key)
			if !present {
				return nil, ErrProfileTemplatePython
			}
			if fee == "" {
				fee = json.Number("0")
				plan = PythonSet(append(domain.RequestObject(nil), plan...), key, fee)
			}
			name, present := plan.Lookup("name")
			if !present {
				return nil, ErrProfileTemplatePython
			}
			if name == nil || fee == nil {
				continue
			}
			number, err := ProfileTemplateFloat(fee)
			if err != nil {
				return nil, err
			}
			if number >= 0 {
				kept = append(kept, PythonSet(append(domain.RequestObject(nil), plan...), key, number))
			}
		}
		assign(kind+"_fee_plans", ProfileTemplateJSON, kept)
	}
	if value := values["enable_quotes_management"]; value != nil {
		assign("enable_quotes_management", ProfileTemplateBoolean, value)
	}
	for _, name := range []string{"enabled_for", "additional_fields", "stripe_available_methods"} {
		assign(name, ProfileTemplateJSON, values[name])
	}
	for _, name := range []string{"invoice_template", "subscription_template", "extra_text_invoices"} {
		assign(name, ProfileTemplateText, values[name])
	}
	for _, field := range plan.Fields {
		// The audit entry is the first statement of the save and cannot hold a
		// NUL or a lone surrogate. JSON fields enter it as escaped JSON text and
		// containers as their repr, so only plain strings fail there.
		if text, ok := field.Value.(string); ok && field.Kind != ProfileTemplateJSON && !profileTemplateValidText(text) {
			return nil, ErrProfileTemplatePython
		}
	}
	if plan.Logo != nil && !profileTemplateValidText(*plan.Logo) {
		// compress_base64 returns a value without a comma, or with a lone
		// surrogate in its data, unchanged. Other spellings are not ported.
		_, data, found := strings.Cut(*plan.Logo, ",")
		if !found || profileTemplateHasSurrogate(data) {
			return nil, ErrProfileTemplatePython
		}
		return nil, ErrProfileTemplateNotPorted
	}
	return plan, nil
}

// profileTemplateItems is len(value) and iteration: lists yield elements,
// dicts their keys and strings their characters; other types raise TypeError.
func profileTemplateItems(value any) ([]any, error) {
	switch v := value.(type) {
	case []any:
		return v, nil
	case domain.RequestObject:
		items := make([]any, len(v))
		for i, field := range v {
			items[i] = field.Name
		}
		return items, nil
	case string:
		items := []any{}
		for _, r := range pythonCodePoints(v) {
			items = append(items, string(r))
		}
		return items, nil
	}
	return nil, ErrProfileTemplatePython
}

// profileTemplatePrettify is BeautifulSoup(value, features="html.parser").prettify().
func profileTemplatePrettify(value any) (string, error) {
	markup, ok := value.(string)
	if !ok {
		return "", ErrProfileTemplatePython // bs4 raises TypeError for non-string markup
	}
	pretty, err := bs4prettify.Prettify(markup)
	if err != nil {
		if errors.Is(err, bs4prettify.ErrPythonRaises) {
			return "", ErrProfileTemplatePython
		}
		return "", ErrProfileTemplateNotPorted
	}
	return pretty, nil
}

func profileTemplateHasSurrogate(text string) bool {
	for _, r := range pythonCodePoints(text) {
		if r >= 0xd800 && r <= 0xdfff {
			return true
		}
	}
	return false
}

func profileTemplateValidText(text string) bool {
	for _, r := range pythonCodePoints(text) {
		if r == 0 || r >= 0xd800 && r <= 0xdfff {
			return false
		}
	}
	return true
}

// profileTemplateNumber is the Python value of a JSON number token: an int
// (big.Int) or a finite float64.
func profileTemplateNumber(token json.Number) (any, bool) {
	text := string(token)
	if !strings.ContainsAny(text, ".eE") {
		integer, ok := new(big.Int).SetString(text, 10)
		return integer, ok
	}
	number, err := strconv.ParseFloat(text, 64)
	if err != nil || math.IsInf(number, 0) {
		return nil, false
	}
	return number, true
}

var profileTemplateFloatSpecial = regexp.MustCompile(`(?i)^[+-]?(?:nan|inf|infinity)$`)

// ProfileTemplateFloat is float(value); ErrProfileTemplatePython is its
// TypeError, ValueError or OverflowError.
func ProfileTemplateFloat(value any) (float64, error) {
	number, ok := PythonFloatOf(value)
	if !ok {
		return 0, ErrProfileTemplatePython
	}
	return number, nil
}

// ProfileTemplateStr is str() of a request value (auditlog's smart_str and
// TextField's stored value); a container is its Python repr.
func ProfileTemplateStr(value any) string { return PythonStr(value) }

// ProfileTemplateAudit is auditlog's value for the in-memory attribute.
func ProfileTemplateAudit(field ProfileTemplateField) string {
	if field.Kind == ProfileTemplateJSON {
		return string(PythonJSONDumpsSorted(field.Value))
	}
	return ProfileTemplateStr(field.Value)
}

// ProfileTemplateBooleanValue is BooleanField.to_python; ok is false for its
// ValidationError (raised inside save(), after auditlog's pre_save entry).
func ProfileTemplateBooleanValue(value any) (bool, bool) {
	switch v := value.(type) {
	case bool:
		return v, true
	case string:
		switch v {
		case "t", "True", "1":
			return true, true
		case "f", "False", "0":
			return false, true
		}
		return false, false
	case json.Number:
		number, _ := profileTemplateNumber(v)
		switch n := number.(type) {
		case *big.Int:
			if n.IsInt64() && (n.Int64() == 0 || n.Int64() == 1) {
				return n.Int64() == 1, true
			}
		case float64:
			if n == 0 || n == 1 {
				return n == 1, true
			}
		}
	}
	return false, false
}

// ProfileTemplateDecimalLiteral is DecimalField.to_python as a numeric literal
// for PostgreSQL, which applies numeric(9, 2) rounding and overflow. Floats go
// through Context(prec=9).create_decimal_from_float; ok is false for a
// non-finite value (ValidationError inside save()).
func ProfileTemplateDecimalLiteral(value any) (string, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return "1", true
		}
		return "0", true
	case float64:
		return strconv.FormatFloat(v, 'e', 8, 64), !math.IsInf(v, 0) && !math.IsNaN(v)
	case json.Number:
		number, finite := profileTemplateNumber(v)
		if !finite {
			return "", false // a JSON number past the float range is inf
		}
		if integer, ok := number.(*big.Int); ok {
			return integer.String(), true
		}
		return ProfileTemplateDecimalLiteral(number.(float64))
	case string:
		// Decimal(v) reads what float(v) accepted: white space stripped,
		// underscores dropped, any Unicode decimal digit.
		v = strings.Map(func(r rune) rune {
			if r == '_' {
				return -1
			}
			if digit, ok := pythonDigit(r); ok {
				return '0' + rune(digit)
			}
			return r
		}, strings.TrimFunc(v, PythonIsSpace))
		if profileTemplateFloatSpecial.MatchString(v) {
			return "", false
		}
		// Decimal(v) is exact; spell it as a plain PostgreSQL numeric literal.
		mantissa, exponent, scientific := strings.Cut(strings.ToLower(v), "e")
		mantissa = strings.TrimPrefix(mantissa, "+")
		sign := ""
		if strings.HasPrefix(mantissa, "-") {
			sign, mantissa = "-", mantissa[1:]
		}
		if strings.HasPrefix(mantissa, ".") {
			mantissa = "0" + mantissa
		}
		if strings.HasSuffix(mantissa, ".") {
			mantissa += "0"
		}
		if scientific {
			return sign + mantissa + "e" + exponent, true
		}
		return sign + mantissa, true
	}
	panic("unsupported profile template decimal")
}

// ProfileTemplateUpper is str.upper() (CPython 3.13: SpecialCasing included, from pythonUpperTable); ok is false only for
// bytes that are not UTF-8 (JSON text always is). Lone surrogates, which the request parser keeps as their three-byte
// form, stay unchanged as Python leaves them.
func ProfileTemplateUpper(text string) (string, bool) {
	var out strings.Builder
	for i := 0; i < len(text); {
		r, width := utf8.DecodeRuneInString(text[i:])
		if r == utf8.RuneError && width == 1 {
			if i+3 <= len(text) && text[i] == 0xed && text[i+1] >= 0xa0 && text[i+1] <= 0xbf && text[i+2] >= 0x80 && text[i+2] <= 0xbf {
				out.WriteString(text[i : i+3])
				i += 3
				continue
			}
			return "", false
		}
		if upper, found := pythonUpperTable[r]; found {
			out.WriteString(upper)
		} else {
			out.WriteRune(r)
		}
		i += width
	}
	return out.String(), true
}
