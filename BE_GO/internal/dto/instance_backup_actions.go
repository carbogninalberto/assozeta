package dto

import (
	"encoding/json"
	"errors"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ErrInstanceBackupActionsNotPorted marks input whose DRF handling Go does not
// carry (lone surrogate code points in a CharField).
var ErrInstanceBackupActionsNotPorted = errors.New("instance backup actions input not ported")

// InstanceBackupActionsArchive is backups.ARCHIVE (fullmatch); Python's str
// \d is any Unicode decimal digit (category Nd).
var InstanceBackupActionsArchive = regexp.MustCompile(`^assozeta-\p{Nd}{8}T\p{Nd}{6}Z(-\p{Nd}+)?\.tar\.gz$`)

// Error codes of the serializer fields below; the handler renders them in the
// request's language.
const (
	BackupActionsRequired       = "required"
	BackupActionsNull           = "null"
	BackupActionsBlank          = "blank"
	BackupActionsString         = "string"
	BackupActionsBoolean        = "boolean"
	BackupActionsInteger        = "integer"
	BackupActionsStringTooLarge = "max-string-length"
	BackupActionsPattern        = "pattern"
	BackupActionsNullCharacter  = "null-character"
	BackupActionsNoData         = "no-data"
	// Prefixed codes carry their argument after the colon.
	BackupActionsMaxLength  = "max-length:"
	BackupActionsMinLength  = "min-length:"
	BackupActionsMaxValue   = "max-value:"
	BackupActionsMinValue   = "min-value:"
	BackupActionsChoice     = "choice:"
	BackupActionsDictionary = "dictionary:"
)

type BackupActionsKind int

const (
	BackupActionsChar BackupActionsKind = iota
	BackupActionsBool
	BackupActionsInt
	BackupActionsChoices
)

// BackupActionsField is one DRF serializer field: CharField/RegexField
// (Trim is trim_whitespace), BooleanField, IntegerField or ChoiceField.
type BackupActionsField struct {
	Name                 string
	Kind                 BackupActionsKind
	Optional             bool // required=False (or a default)
	Default              any  // the default's internal value, when set
	AllowBlank, Trim     bool
	MinLength, MaxLength int
	Pattern              *regexp.Regexp
	Min, Max             *big.Int
	Choices              []string
}

// Field sets of the backup views' serializers.
var (
	backupActionsTime  = regexp.MustCompile(`^([01]\p{Nd}|2[0-3]):[0-5]\p{Nd}$`)
	backupActionsKeyID = regexp.MustCompile(`^[0-9a-f]{8}$`)

	BackupScheduleFields = []BackupActionsField{
		{Name: "enabled", Kind: BackupActionsBool},
		{Name: "frequency", Kind: BackupActionsChoices, Choices: []string{"daily", "weekly"}},
		{Name: "time", Kind: BackupActionsChar, Trim: true, Pattern: backupActionsTime},
		{Name: "weekday", Kind: BackupActionsInt, Optional: true, Default: json.Number("6"), Min: big.NewInt(0), Max: big.NewInt(6)},
		{Name: "keep", Kind: BackupActionsInt, Min: big.NewInt(1), Max: big.NewInt(30)},
	}
	BackupRestoreFields = []BackupActionsField{
		{Name: "password", Kind: BackupActionsChar, MaxLength: 256},
		{Name: "confirm", Kind: BackupActionsChar, Trim: true, MaxLength: 32},
	}
	BackupRemoteSettingsFields = []BackupActionsField{
		{Name: "endpoint", Kind: BackupActionsChar, Optional: true, AllowBlank: true, Trim: true, MaxLength: 255},
		{Name: "region", Kind: BackupActionsChar, Optional: true, AllowBlank: true, Trim: true, MaxLength: 64},
		{Name: "bucket", Kind: BackupActionsChar, Optional: true, AllowBlank: true, Trim: true, MaxLength: 63},
		{Name: "prefix", Kind: BackupActionsChar, Optional: true, AllowBlank: true, Trim: true, MaxLength: 200},
		{Name: "access_key", Kind: BackupActionsChar, Optional: true, AllowBlank: true, Trim: true, MaxLength: 128},
		{Name: "secret_key", Kind: BackupActionsChar, Optional: true, AllowBlank: true, Trim: true, MaxLength: 256},
		{Name: "path_style", Kind: BackupActionsBool, Optional: true},
		{Name: "retention", Kind: BackupActionsInt, Optional: true, Min: big.NewInt(1), Max: big.NewInt(30)},
		{Name: "enabled", Kind: BackupActionsBool, Optional: true},
	}
	BackupKeyConfirmFields = []BackupActionsField{
		{Name: "key_id", Kind: BackupActionsChar, Trim: true, Pattern: backupActionsKeyID},
		{Name: "tail", Kind: BackupActionsChar, Trim: true, MinLength: 6, MaxLength: 6},
	}
	BackupFetchFields = []BackupActionsField{
		{Name: "name", Kind: BackupActionsChar, Trim: true, Pattern: InstanceBackupActionsArchive},
		{Name: "recovery_key", Kind: BackupActionsChar, MaxLength: 4096},
	}
)

// ValidateBackupActions is Serializer(data=body).is_valid(): validated_data
// in declared field order (omitted optional fields without a default are
// absent), or the errors keyed in field order (codes, see above).
func ValidateBackupActions(body any, fields []BackupActionsField) (domain.RequestObject, domain.RequestObject, error) {
	if body == nil {
		return nil, domain.RequestObject{{Name: "non_field_errors", Value: []any{BackupActionsNoData}}}, nil
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, domain.RequestObject{{Name: "non_field_errors", Value: []any{BackupActionsDictionary + pythonTypeName(body)}}}, nil
	}
	var validated, errs domain.RequestObject
	for _, field := range fields {
		value, present := object.Lookup(field.Name)
		internal, set, fieldErrors, err := backupActionsRun(field, value, present)
		if err != nil {
			return nil, nil, err
		}
		if fieldErrors != nil {
			errs = append(errs, domain.RequestField{Name: field.Name, Value: fieldErrors})
		} else if set {
			validated = append(validated, domain.RequestField{Name: field.Name, Value: internal})
		}
	}
	if errs != nil {
		return nil, errs, nil
	}
	if validated == nil {
		validated = domain.RequestObject{}
	}
	return validated, nil, nil
}

// backupActionsRun is Field.run_validation for one field: set=false for an
// omitted optional field without default (SkipField).
func backupActionsRun(field BackupActionsField, value any, present bool) (any, bool, []any, error) {
	if field.Kind == BackupActionsChar {
		if text, ok := value.(string); ok && (text == "" || field.Trim && pythonStrip(text) == "") {
			if !field.AllowBlank {
				return nil, false, []any{BackupActionsBlank}, nil
			}
			return "", true, nil, nil
		}
	}
	if !present {
		if !field.Optional {
			return nil, false, []any{BackupActionsRequired}, nil
		}
		if field.Default != nil {
			return field.Default, true, nil, nil
		}
		return nil, false, nil, nil
	}
	if value == nil {
		return nil, false, []any{BackupActionsNull}, nil
	}
	switch field.Kind {
	case BackupActionsBool:
		result, ok := backupActionsBoolean(value)
		if !ok {
			return nil, false, []any{BackupActionsBoolean}, nil
		}
		return result, true, nil, nil
	case BackupActionsChoices:
		text := PythonStr(value)
		for _, choice := range field.Choices {
			if text == choice {
				return choice, true, nil, nil
			}
		}
		return nil, false, []any{BackupActionsChoice + text}, nil
	case BackupActionsInt:
		number, code := backupActionsInteger(value)
		if code != "" {
			return nil, false, []any{code}, nil
		}
		var errs []any
		if field.Max != nil && number.Cmp(field.Max) > 0 {
			errs = append(errs, BackupActionsMaxValue+field.Max.String())
		}
		if field.Min != nil && number.Cmp(field.Min) < 0 {
			errs = append(errs, BackupActionsMinValue+field.Min.String())
		}
		if errs != nil {
			return nil, false, errs, nil
		}
		return json.Number(number.String()), true, nil, nil
	}
	return backupActionsChar(field, value)
}

// backupActionsChar is CharField.to_internal_value (str() of str, int and
// float; strip when trim_whitespace) and its validators, whose errors are
// collected: max and min length, null characters, then the RegexField pattern
// (whose message also replaces 'invalid').
func backupActionsChar(field BackupActionsField, value any) (any, bool, []any, error) {
	invalid := BackupActionsString
	if field.Pattern != nil {
		invalid = BackupActionsPattern
	}
	var text string
	switch v := value.(type) {
	case string:
		text = v
	case json.Number:
		text = PythonStr(v)
	default:
		return nil, false, []any{invalid}, nil
	}
	if field.Trim {
		text = pythonStrip(text)
	}
	length := 0
	for _, r := range pythonCodePoints(text) {
		if r >= 0xd800 && r <= 0xdfff {
			return nil, false, nil, ErrInstanceBackupActionsNotPorted
		}
		length++
	}
	var errs []any
	if field.MaxLength > 0 && length > field.MaxLength {
		errs = append(errs, BackupActionsMaxLength+strconv.Itoa(field.MaxLength))
	}
	if field.MinLength > 0 && length < field.MinLength {
		errs = append(errs, BackupActionsMinLength+strconv.Itoa(field.MinLength))
	}
	if strings.ContainsRune(text, 0) {
		errs = append(errs, BackupActionsNullCharacter)
	}
	if field.Pattern != nil && !field.Pattern.MatchString(text) {
		errs = append(errs, BackupActionsPattern)
	}
	if errs != nil {
		return nil, false, errs, nil
	}
	return text, true, nil, nil
}

// backupActionsBoolean is BooleanField.to_internal_value: str.lower() in the
// true/false sets, numbers equal to 1 or 0 (1.0 and 0.0 included); lists and
// dicts are unhashable and invalid.
func backupActionsBoolean(value any) (bool, bool) {
	switch v := value.(type) {
	case bool:
		return v, true
	case string:
		switch strings.ToLower(v) {
		case "t", "y", "yes", "true", "on", "1":
			return true, true
		case "f", "n", "no", "false", "off", "0":
			return false, true
		}
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			number, ok := new(big.Int).SetString(text, 10)
			if ok && number.IsInt64() && (number.Int64() == 0 || number.Int64() == 1) {
				return number.Int64() == 1, true
			}
			return false, false
		}
		number, err := strconv.ParseFloat(text, 64)
		if err == nil && (number == 0 || number == 1) {
			return number == 1, true
		}
	}
	return false, false
}

// backupActionsDecimalTail is IntegerField.re_decimal (r'\.0*\s*$'): Python's
// $ also matches before a final newline.
var backupActionsDecimalTail = regexp.MustCompile(`\.0*[\s\x1c-\x1f\x85\p{Z}]*$`)

// backupActionsInteger is IntegerField.to_internal_value:
// int(re_decimal.sub(”, str(data))), with the string length guard.
func backupActionsInteger(value any) (*big.Int, string) {
	var text string
	switch v := value.(type) {
	case string:
		if len(pythonCodePoints(v)) > 1000 {
			return nil, BackupActionsStringTooLarge
		}
		text = v
	case json.Number:
		text = PythonStr(v)
	default:
		// str() of a bool, list or dict never parses as an int.
		return nil, BackupActionsInteger
	}
	text = backupActionsDecimalTail.ReplaceAllString(text, "")
	number, ok := pythonDecimalInt(text)
	if !ok {
		return nil, BackupActionsInteger
	}
	return number, ""
}

// pythonDecimalInt is int(text) in base 10: surrounding whitespace, a sign,
// Unicode decimal digits and single underscores between digits.
func pythonDecimalInt(text string) (*big.Int, bool) {
	runes := []rune(strings.TrimFunc(text, PythonIsSpace))
	negative := false
	if len(runes) > 0 && (runes[0] == '+' || runes[0] == '-') {
		negative = runes[0] == '-'
		runes = runes[1:]
	}
	if len(runes) == 0 {
		return nil, false
	}
	var digits strings.Builder
	for i, r := range runes {
		if r == '_' {
			if i == 0 || i == len(runes)-1 || runes[i-1] == '_' {
				return nil, false
			}
			continue
		}
		if !unicode.Is(unicode.Nd, r) {
			return nil, false
		}
		digits.WriteByte(byte('0' + backupActionsDigitValue(r)))
	}
	number, ok := new(big.Int).SetString(digits.String(), 10)
	if !ok {
		return nil, false
	}
	if negative {
		number.Neg(number)
	}
	return number, true
}

// backupActionsDigitValue is unicodedata.decimal(r): Nd digits come in runs
// of ten starting at the digit zero (some runs are adjacent).
func backupActionsDigitValue(r rune) int {
	start := r
	for start > 0 && unicode.Is(unicode.Nd, start-1) {
		start--
	}
	return int(r-start) % 10
}
