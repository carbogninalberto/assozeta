package dto

import (
	"encoding/json"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// InstructorTextFields are InstructorSerializer's text fields.
var InstructorTextFields = []SupplierField{
	{Name: "first_name", Max: 150, Required: true},
	{Name: "last_name", Max: 150, Required: true},
	{Name: "email", Max: 254, Null: true, Blank: true, Email: true},
	{Name: "born_city", Max: 150, Null: true, Blank: true},
	{Name: "born_province", Max: 150, Null: true, Blank: true},
	{Name: "tax_code", Max: 25, Null: true, Blank: true},
	{Name: "address_city", Max: 150, Null: true, Blank: true},
	{Name: "address", Max: 150, Null: true, Blank: true},
	{Name: "address_province", Max: 150, Null: true, Blank: true},
	{Name: "civic_number", Max: 10, Null: true, Blank: true},
	{Name: "study_title", Max: 150, Null: true, Blank: true},
	{Name: "role", Max: 150, Null: true},
	{Name: "phone", Max: 20, Null: true, Blank: true},
	{Name: "phone_2", Max: 20, Null: true, Blank: true},
	{Name: "phone_3", Max: 20, Null: true, Blank: true},
	{Name: "phone_4", Max: 20, Null: true, Blank: true},
}

// InstructorDecimals are the model's DecimalFields.
var InstructorDecimals = []InstructorHoursDecimal{
	{Name: "default_hourly_billing", MaxDigits: 9, Places: 2},
	{Name: "default_percentage_billing", MaxDigits: 4, Places: 2, Null: true},
}

// InstructorWrite is InstructorSerializer's validated data before the
// related lookups; maps hold present fields (nil for None). The read-only
// instructor_id is not read from the body.
type InstructorWrite struct {
	Text              map[string]*string
	Dates             map[string]*string
	Booleans          map[string]bool
	Decimals          map[string]*string
	AssociatedPresent bool
	Associated        *string
	DocumentsPresent  bool
	// Documents are the parsed keys with the str() of each raw value.
	Documents    []string
	DocumentText []string
}

// DRFUUIDValue is serializers.UUIDField.to_internal_value (and Django's
// UUIDField.to_python): an int (bool included) is UUID(int=...), a str
// UUID(hex=...); anything else is invalid.
func DRFUUIDValue(value any) (string, bool) { return instructorUUIDField(value) }

func instructorUUIDField(value any) (string, bool) {
	if flag, ok := value.(bool); ok {
		if flag {
			return "00000000-0000-0000-0000-000000000001", true
		}
		return "00000000-0000-0000-0000-000000000000", true
	}
	if number, ok := value.(json.Number); ok && strings.ContainsAny(string(number), ".eE") {
		return "", false
	}
	return supplierUUID(value)
}

// ParseInstructorWrite ports InstructorSerializer(data=...) field validation
// for a JSON object (user is set by the view and always valid).
func ParseInstructorWrite(object domain.RequestObject) (InstructorWrite, map[string]any) {
	write := InstructorWrite{Text: map[string]*string{}, Dates: map[string]*string{}, Booleans: map[string]bool{}, Decimals: map[string]*string{}}
	errs := map[string]any{}
	for _, field := range InstructorTextFields {
		value, present := object.Lookup(field.Name)
		if !present {
			if field.Required {
				errs[field.Name] = []string{PaymentCategoryValidationRequired}
			}
			continue
		}
		text, codes := modelTextValue(field, value, false)
		if len(codes) > 0 {
			errs[field.Name] = codes
			continue
		}
		write.Text[field.Name] = text
	}
	for _, name := range []string{"born_date", "stipulated_contract_in"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		if value == nil {
			write.Dates[name] = nil
		} else if day, ok := ParseSubscriptionMembershipDate(value); ok {
			write.Dates[name] = &day
		} else {
			errs[name] = []string{SubscriptionMembershipDate}
		}
	}
	for _, name := range []string{"draft", "is_volunteer"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		if value == nil {
			errs[name] = []string{PaymentCategoryValidationNull}
		} else if flag, err := parsePaymentCategoryBoolean(value); err != nil {
			errs[name] = []string{PaymentCategoryValidationBoolean}
		} else {
			write.Booleans[name] = flag
		}
	}
	if value, present := object.Lookup("associated_user_id"); present {
		write.AssociatedPresent = true
		if value != nil {
			if id, ok := instructorUUIDField(value); ok {
				write.Associated = &id
			} else {
				errs["associated_user_id"] = []string{PaymentCategoryValidationUUID}
			}
		}
	}
	for _, field := range InstructorDecimals {
		value, present := object.Lookup(field.Name)
		if !present {
			continue
		}
		switch {
		case field.Null && (value == nil || PythonStrip(PythonStr(value)) == ""):
			write.Decimals[field.Name] = nil
		case value == nil:
			errs[field.Name] = []string{PaymentCategoryValidationNull}
		default:
			decimal, code := DRFDecimal(value, field.MaxDigits, field.Places)
			if code != "" {
				errs[field.Name] = []string{code}
				continue
			}
			write.Decimals[field.Name] = &decimal
		}
	}
	if value, present := object.Lookup("documents"); present {
		var items []any
		switch v := value.(type) {
		case nil:
			errs["documents"] = []string{PaymentCategoryValidationNull}
		case []any:
			items = v
		case domain.RequestObject:
			// A dict is iterable: its keys are the values.
			for _, field := range v {
				items = append(items, field.Name)
			}
		default:
			errs["documents"] = []string{"not-a-list:" + pythonTypeName(value)}
		}
		if _, failed := errs["documents"]; !failed {
			write.DocumentsPresent = true
			for _, item := range items {
				id, code := supplierRelated(item)
				if code != "" {
					errs["documents"] = []string{code}
					break
				}
				write.Documents = append(write.Documents, id)
				write.DocumentText = append(write.DocumentText, PythonStr(item))
			}
		}
	}
	if len(errs) > 0 {
		return write, errs
	}
	return write, nil
}

// ApplyInstructorWrite sets the validated fields on row.
func ApplyInstructorWrite(row *domain.InstructorRecord, write InstructorWrite) {
	texts := map[string]**string{"email": &row.Email, "born_city": &row.BornCity, "born_province": &row.BornProvince, "tax_code": &row.TaxCode,
		"address_city": &row.AddressCity, "address": &row.Address, "address_province": &row.AddressProvince, "civic_number": &row.CivicNumber,
		"study_title": &row.StudyTitle, "role": &row.Role, "phone": &row.Phone, "phone_2": &row.Phone2, "phone_3": &row.Phone3,
		"phone_4": &row.Phone4}
	for name, value := range write.Text {
		switch name {
		case "first_name":
			row.FirstName = *value
		case "last_name":
			row.LastName = *value
		default:
			*texts[name] = value
		}
	}
	for name, value := range write.Dates {
		if name == "born_date" {
			row.BornDate = value
		} else {
			row.StipulatedContractIn = value
		}
	}
	for name, value := range write.Booleans {
		if name == "draft" {
			row.Draft = value
		} else {
			row.IsVolunteer = value
		}
	}
	if write.AssociatedPresent {
		row.AssociatedUserID = write.Associated
	}
	for name, value := range write.Decimals {
		if name == "default_hourly_billing" {
			row.DefaultHourlyBilling = *value
		} else {
			row.DefaultPercentageBilling = value
		}
	}
}

func instructorText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func instructorBool(value bool) string {
	if value {
		return "True"
	}
	return "False"
}

// InstructorFieldTexts is smart_str() of every concrete field, keyed by
// field name (auditlog's update diff compares these).
func InstructorFieldTexts(row domain.InstructorRecord) map[string]string {
	return map[string]string{"instructor_id": row.ID, "user": row.UserID, "first_name": row.FirstName, "last_name": row.LastName,
		"email": instructorText(row.Email), "born_date": instructorText(row.BornDate), "born_city": instructorText(row.BornCity),
		"born_province": instructorText(row.BornProvince), "tax_code": instructorText(row.TaxCode), "address_city": instructorText(row.AddressCity),
		"address": instructorText(row.Address), "address_province": instructorText(row.AddressProvince),
		"civic_number": instructorText(row.CivicNumber), "study_title": instructorText(row.StudyTitle), "role": instructorText(row.Role),
		"phone": instructorText(row.Phone), "phone_2": instructorText(row.Phone2), "phone_3": instructorText(row.Phone3),
		"phone_4": instructorText(row.Phone4), "draft": instructorBool(row.Draft), "associated_user_id": instructorText(row.AssociatedUserID),
		"is_volunteer": instructorBool(row.IsVolunteer), "stipulated_contract_in": instructorText(row.StipulatedContractIn),
		"default_hourly_billing": row.DefaultHourlyBilling, "default_percentage_billing": instructorText(row.DefaultPercentageBilling)}
}

// InstructorChanges is auditlog's update diff between the stored row and the
// saved instance.
func InstructorChanges(old, new domain.InstructorRecord) map[string][2]string {
	before, after := InstructorFieldTexts(old), InstructorFieldTexts(new)
	changes := map[string][2]string{}
	for name, value := range after {
		if before[name] != value {
			changes[name] = [2]string{before[name], value}
		}
	}
	return changes
}

// compensationDatePattern is _strptime's regex for '%Y-%m-%dT%H:%M:%S.%f'
// (case-insensitive; \d matches any Unicode decimal digit, %f only ASCII).
var compensationDatePattern = regexp.MustCompile(`(?i)\A(\p{Nd}{4})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\p{Nd}|0[1-9]|[1-9]| [1-9])T` +
	`(2[0-3]|[0-1]\p{Nd}|\p{Nd}):([0-5]\p{Nd}|\p{Nd}):(6[0-1]|[0-5]\p{Nd}|\p{Nd})\.([0-9]{1,6})\z`)

// CompensationPaymentDate is instructor_hours_add_compensation's
// payment_date: strptime(value.split('+')[0], '%Y-%m-%dT%H:%M:%S.%f') as a
// naive (UTC) datetime; ok=false is the except branch (now).
func CompensationPaymentDate(value any) (time.Time, bool) {
	text, ok := value.(string)
	if !ok {
		return time.Time{}, false
	}
	text, _, _ = strings.Cut(text, "+")
	match := compensationDatePattern.FindStringSubmatch(text)
	if match == nil {
		return time.Time{}, false
	}
	number := func(digits string) int {
		result := 0
		for _, r := range strings.TrimSpace(digits) {
			digit, _ := pythonDigit(r)
			result = result*10 + digit
		}
		return result
	}
	year, month, day := number(match[1]), number(match[2]), number(match[3])
	hour, minute, second := number(match[4]), number(match[5]), number(match[6])
	fraction := match[7] + strings.Repeat("0", 6-len(match[7]))
	micros, _ := strconv.Atoi(fraction)
	if year < 1 || second > 59 {
		return time.Time{}, false
	}
	moment := time.Date(year, time.Month(month), day, hour, minute, second, micros*1000, time.UTC)
	if moment.Day() != day {
		return time.Time{}, false
	}
	return moment, true
}

// InstructorDecimal is a decimal.Decimal: sign, coefficient and exponent.
type InstructorDecimal struct {
	Negative    bool
	Coefficient *big.Int
	Exponent    int64
	// Special is "inf", "nan" or "snan" (Coefficient is then nil); Payload is a NaN's diagnostic digits.
	Special string
	Payload string
}

// Python's default decimal context: 28 digits, exponents from Emin to Emax;
// the constructor's limits are decimal.MAX_EMAX and decimal.MIN_ETINY.
const (
	instructorDecimalPrecision = 28
	instructorDecimalEmax      = 999999
	instructorDecimalEtiny     = -1000026
	instructorDecimalMaxEmax   = 999999999999999999
	instructorDecimalMinEtiny  = -1999999999999999997
)

// DecimalOutcome classifies Decimal(text).
type DecimalOutcome int

const (
	DecimalFinite DecimalOutcome = iota
	// DecimalInvalid is decimal.InvalidOperation.
	DecimalInvalid
)

// ParseInstructorDecimal is decimal.Decimal(text), read back from str() of
// ParsePythonDecimal's finite value.
func ParseInstructorDecimal(text string) (InstructorDecimal, DecimalOutcome) {
	parsed := ParsePythonDecimal(text)
	switch parsed.Kind {
	case "":
		return InstructorDecimal{}, DecimalInvalid
	case "finite":
	case "inf":
		return InstructorDecimal{Negative: parsed.Negative, Special: "inf"}, DecimalFinite
	default:
		special := "nan"
		if parsed.Signaling {
			special = "snan"
		}
		return InstructorDecimal{Negative: parsed.Negative, Special: special, Payload: parsed.Payload}, DecimalFinite
	}
	value := strings.TrimPrefix(parsed.Text, "-")
	mantissa, exponentText, scientific := strings.Cut(value, "E")
	exponent := int64(0)
	if scientific {
		number, err := strconv.ParseInt(exponentText, 10, 64)
		if err != nil {
			return InstructorDecimal{}, DecimalInvalid
		}
		exponent = number
	}
	whole, fraction, _ := strings.Cut(mantissa, ".")
	digits := strings.TrimLeft(whole+fraction, "0")
	if digits == "" {
		digits = "0"
	}
	coefficient, ok := new(big.Int).SetString(digits, 10)
	if !ok {
		return InstructorDecimal{}, DecimalInvalid
	}
	if int64(len(fraction)) > 0 {
		var ok bool
		if exponent, ok = instructorDecimalAdd(exponent, -int64(len(fraction))); !ok {
			return InstructorDecimal{}, DecimalInvalid
		}
	}
	// Decimal(text) accepts an exponent at or above MIN_ETINY and an
	// adjusted exponent at or below MAX_EMAX.
	if exponent < instructorDecimalMinEtiny {
		return InstructorDecimal{}, DecimalInvalid
	}
	adjusted, ok := instructorDecimalAdd(exponent, int64(len(digits))-1)
	if !ok || adjusted > instructorDecimalMaxEmax {
		return InstructorDecimal{}, DecimalInvalid
	}
	return InstructorDecimal{Negative: parsed.Negative, Coefficient: coefficient, Exponent: exponent}, DecimalFinite
}

// String is Decimal.__str__ (to-scientific-string).
func (d InstructorDecimal) String() string {
	switch d.Special {
	case "inf":
		return map[bool]string{false: "Infinity", true: "-Infinity"}[d.Negative]
	case "nan", "snan":
		out := map[string]string{"nan": "NaN", "snan": "sNaN"}[d.Special] + d.Payload
		if d.Negative {
			out = "-" + out
		}
		return out
	}
	digits := d.Coefficient.String()
	adjusted := d.Exponent + int64(len(digits)) - 1
	var out string
	switch {
	case d.Exponent <= 0 && adjusted >= -6:
		if d.Exponent == 0 {
			out = digits
		} else if point := int64(len(digits)) + d.Exponent; point > 0 {
			out = digits[:point] + "." + digits[point:]
		} else {
			out = "0." + strings.Repeat("0", int(-point)) + digits
		}
	default:
		out = digits[:1]
		if len(digits) > 1 {
			out += "." + digits[1:]
		}
		sign := "+"
		if adjusted < 0 {
			sign = "-"
		}
		out += "E" + sign + strconv.FormatInt(abs64(adjusted), 10)
	}
	if d.Negative {
		out = "-" + out
	}
	return out
}

func abs64(value int64) int64 {
	if value < 0 {
		return -value
	}
	return value
}

// IsZero reports a zero coefficient (a falsy Decimal).
func (d InstructorDecimal) IsZero() bool { return d.Special == "" && d.Coefficient.Sign() == 0 }

// PercentOf is d / 100: the exact quotient at the exponent closest to the
// ideal one (d's exponent).
func (d InstructorDecimal) PercentOf() InstructorDecimal {
	coefficient, exponent := new(big.Int).Set(d.Coefficient), d.Exponent-2
	ten, remainder := big.NewInt(10), new(big.Int)
	for exponent < d.Exponent && coefficient.Sign() != 0 {
		quotient, rest := new(big.Int).QuoRem(coefficient, ten, remainder)
		if rest.Sign() != 0 {
			break
		}
		coefficient, exponent = quotient, exponent+1
	}
	if coefficient.Sign() == 0 {
		exponent = d.Exponent
	}
	return InstructorDecimal{Negative: d.Negative, Coefficient: coefficient, Exponent: exponent}
}

// TimesResult classifies d * other in Python's default context.
type TimesResult int

const (
	TimesOK TimesResult = iota
	// TimesInvalid is decimal.InvalidOperation (trapped: an exception).
	TimesInvalid
	// TimesOverflow is decimal.Overflow (trapped: an exception).
	TimesOverflow
)

// Times is d * other in Python's default context: rounded to 28 digits half
// even, a subnormal result rounded at exponent Etiny, a zero's exponent
// clamped to [Etiny, Emax]. other is the percentage scaled by 1/100, a finite
// value.
func (d InstructorDecimal) Times(other InstructorDecimal) (InstructorDecimal, TimesResult) {
	switch {
	case d.Special == "snan" || other.Special == "snan":
		return InstructorDecimal{}, TimesInvalid
	case d.Special == "nan":
		return d.quietNaN(), TimesOK
	case other.Special == "nan":
		return other.quietNaN(), TimesOK
	case d.Special == "inf" || other.Special == "inf":
		if d.Special == "" && d.Coefficient.Sign() == 0 {
			return InstructorDecimal{}, TimesInvalid
		}
		if other.Special == "" && other.Coefficient.Sign() == 0 {
			return InstructorDecimal{}, TimesInvalid
		}
		return InstructorDecimal{Negative: d.Negative != other.Negative, Special: "inf"}, TimesOK
	}
	coefficient := new(big.Int).Mul(d.Coefficient, other.Coefficient)
	negative := d.Negative != other.Negative
	exponent, ok := instructorDecimalAdd(d.Exponent, other.Exponent)
	if !ok {
		// The parsed operands cannot reach this; keep the context's answer.
		if other.Exponent > 0 {
			return InstructorDecimal{}, TimesOverflow
		}
		return InstructorDecimal{Negative: negative, Coefficient: new(big.Int), Exponent: instructorDecimalEtiny}, TimesOK
	}
	if coefficient.Sign() == 0 {
		return InstructorDecimal{Negative: negative, Coefficient: coefficient, Exponent: instructorDecimalClamp(exponent)}, TimesOK
	}
	digits := int64(len(coefficient.String()))
	drop := int64(0)
	if exponent < instructorDecimalEtiny {
		drop = instructorDecimalEtiny - exponent
	}
	if digits-drop > instructorDecimalPrecision {
		drop = digits - instructorDecimalPrecision
	}
	if drop > 0 {
		coefficient, exponent = instructorDecimalRound(coefficient, exponent, drop)
		digits = int64(len(coefficient.String()))
		if digits > instructorDecimalPrecision {
			// The rounding carried into a new digit.
			coefficient.Quo(coefficient, big.NewInt(10))
			exponent++
			digits--
		}
		if coefficient.Sign() == 0 {
			return InstructorDecimal{Negative: negative, Coefficient: coefficient, Exponent: instructorDecimalClamp(exponent)}, TimesOK
		}
	}
	if exponent > instructorDecimalEmax {
		return InstructorDecimal{}, TimesOverflow
	}
	if adjusted := exponent + digits - 1; adjusted > instructorDecimalEmax {
		return InstructorDecimal{}, TimesOverflow
	}
	return InstructorDecimal{Negative: negative, Coefficient: coefficient, Exponent: exponent}, TimesOK
}

// quietNaN is the NaN a multiplication returns: the payload cut to the
// context's 28 digits, then without leading zeros.
func (d InstructorDecimal) quietNaN() InstructorDecimal {
	payload := d.Payload
	if len(payload) > instructorDecimalPrecision {
		payload = strings.TrimLeft(payload[len(payload)-instructorDecimalPrecision:], "0")
	}
	return InstructorDecimal{Negative: d.Negative, Special: d.Special, Payload: payload}
}

// instructorDecimalRound divides coefficient by 10^drop, rounding the
// remainder half even, and adds drop to the exponent. A drop wider than the
// coefficient rounds to zero without building the divisor.
func instructorDecimalRound(coefficient *big.Int, exponent, drop int64) (*big.Int, int64) {
	digits := int64(len(coefficient.String()))
	if drop > digits {
		return new(big.Int), exponent + drop
	}
	if drop == digits {
		// The quotient is zero; half of 10^drop is 5*10^(drop-1), so the
		// coefficient rounds up above that and stays even on the tie.
		rendered := coefficient.String()
		if rendered > "5"+strings.Repeat("0", len(rendered)-1) {
			return big.NewInt(1), exponent + drop
		}
		return new(big.Int), exponent + drop
	}
	unit := new(big.Int).Exp(big.NewInt(10), big.NewInt(drop), nil)
	quotient, rest := new(big.Int).QuoRem(coefficient, unit, new(big.Int))
	half := new(big.Int).Quo(unit, big.NewInt(2))
	switch rest.Cmp(half) {
	case 1:
		quotient.Add(quotient, big.NewInt(1))
	case 0:
		if quotient.Bit(0) == 1 {
			quotient.Add(quotient, big.NewInt(1))
		}
	}
	return quotient, exponent + drop
}

// instructorDecimalAdd adds two exponents, reporting an int64 overflow.
func instructorDecimalAdd(left, right int64) (int64, bool) {
	sum := left + right
	if (right > 0 && sum < left) || (right < 0 && sum > left) {
		return 0, false
	}
	return sum, true
}

func instructorDecimalClamp(exponent int64) int64 {
	if exponent < instructorDecimalEtiny {
		return instructorDecimalEtiny
	}
	if exponent > instructorDecimalEmax {
		return instructorDecimalEmax
	}
	return exponent
}
