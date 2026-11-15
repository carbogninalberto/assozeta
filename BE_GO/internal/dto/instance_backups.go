package dto

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// InstanceBackupsArchive is backups.ARCHIVE (fullmatch). Python's str \d is
// any Unicode decimal digit (category Nd).
var InstanceBackupsArchive = regexp.MustCompile(`^assozeta-\p{Nd}{8}T\p{Nd}{6}Z(-\p{Nd}+)?\.tar\.gz$`)

// InstanceBackupsToken is a valid DownloadTokenSerializer.
type InstanceBackupsToken struct{ Name, Password string }

// ParseInstanceBackupsToken is DownloadTokenSerializer(data=...).is_valid():
// name = RegexField(ARCHIVE) (a CharField whose 'invalid' message is the
// pattern message), password = CharField(max_length=256,
// trim_whitespace=False). Errors are keyed in field order.
func ParseInstanceBackupsToken(body any) (*InstanceBackupsToken, domain.RequestObject) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, domain.RequestObject{{Name: "non_field_errors", Value: []any{
			"Invalid data. Expected a dictionary, but got " + pythonTypeName(body) + "."}}}
	}
	const pattern = "This value does not match the required pattern."
	var errs domain.RequestObject
	name, present := object.Lookup("name")
	nameText, nameErrors, validated := instanceBackupsChar(name, present, true, 0, pattern)
	if validated && !InstanceBackupsArchive.MatchString(nameText) {
		nameErrors = append(nameErrors, pattern)
	}
	if nameErrors != nil {
		errs = append(errs, domain.RequestField{Name: "name", Value: nameErrors})
	}
	password, present := object.Lookup("password")
	passwordText, passwordErrors, _ := instanceBackupsChar(password, present, false, 256, "Not a valid string.")
	if passwordErrors != nil {
		errs = append(errs, domain.RequestField{Name: "password", Value: passwordErrors})
	}
	if errs != nil {
		return nil, errs
	}
	return &InstanceBackupsToken{Name: nameText, Password: passwordText}, nil
}

// instanceBackupsChar is CharField.run_validation: the blank check, required
// and null, to_internal_value (str() of str/int/float, then strip when
// trim_whitespace) and the generic validators (max length, null characters and
// lone surrogate code points, in DRF's order), whose errors are collected.
// validated reports that the validators ran, so the caller appends its regex
// result.
func instanceBackupsChar(value any, present, trim bool, maxLength int, invalid string) (string, []any, bool) {
	if text, ok := value.(string); ok && (text == "" || trim && pythonStrip(text) == "") {
		return "", []any{"This field may not be blank."}, false
	}
	if !present {
		return "", []any{"This field is required."}, false
	}
	var text string
	switch v := value.(type) {
	case nil:
		return "", []any{"This field may not be null."}, false
	case string:
		text = v
	case json.Number:
		text = instanceBackupsNumberStr(string(v))
	default:
		return "", []any{invalid}, false
	}
	if trim {
		text = pythonStrip(text)
	}
	length, surrogate := 0, rune(0)
	for _, r := range pythonCodePoints(text) {
		if r >= 0xd800 && r <= 0xdfff && surrogate == 0 {
			surrogate = r
		}
		length++
	}
	var errs []any
	if maxLength > 0 && length > maxLength {
		errs = append(errs, "Ensure this field has no more than "+strconv.Itoa(maxLength)+" characters.")
	}
	if strings.ContainsRune(text, 0) {
		errs = append(errs, "Null characters are not allowed.")
	}
	if surrogate != 0 {
		errs = append(errs, "Surrogate characters are not allowed: U+"+strings.ToUpper(strconv.FormatInt(int64(surrogate), 16))+".")
	}
	return text, errs, true
}

// instanceBackupsNumberStr is str() of a JSON number: the exact int, or the
// float's repr (inf for an overflowing literal).
func instanceBackupsNumberStr(token string) string {
	text := pythonJSONNumber(token)
	switch text {
	case "Infinity":
		return "inf"
	case "-Infinity":
		return "-inf"
	}
	return text
}
