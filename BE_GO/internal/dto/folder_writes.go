package dto

import (
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// FolderPK is a Folder pk after IntegerField.get_prep_value's int(): ID is
// looked up, unless Missing (outside the integer column, so Django's
// IntegerFieldOverflow lookup matches nothing). Raw is the request value
// (DRF's does_not_exist pk_value).
type FolderPK struct {
	ID      int64
	Missing bool
	Raw     any
}

// FolderWrite is FolderSerializer's field input before the related lookups.
type FolderWrite struct {
	Name *string
	// ParentPresent with a nil Parent is None (null, or '' which RelatedField
	// turns into None).
	ParentPresent bool
	Parent        *FolderPK
	// Association is the normalized sport_association pk (partial only: add
	// overwrites the client value before validation).
	AssociationPresent bool
	Association        string
	AssociationRaw     any
	CreatedAt          *time.Time
}

// FolderValidationUnique is UniqueTogetherValidator's non-field error.
const FolderValidationUnique = "folder-unique-together"

var folderNameField = SupplierField{Name: "name", Max: 255}

const folderIntegerMin, folderIntegerMax = -2147483648, 2147483647

// ParseFolderWrite ports FolderSerializer(data=..., partial=...) field
// validation: name CharField(255) (required on create), parent a pk field
// with default None, sport_association a required pk field (validated on
// update only) and created_at an optional DateTimeField; id, level and
// children are read-only. HTML input (form) skips an empty created_at.
func ParseFolderWrite(object domain.RequestObject, partial, form bool) (FolderWrite, map[string][]string, error) {
	var write FolderWrite
	invalid := map[string][]string{}
	if value, present := object.Lookup("name"); present {
		text, codes := modelTextValue(folderNameField, value, form)
		if len(codes) > 0 {
			invalid["name"] = codes
		} else {
			write.Name = text
		}
	} else if !partial {
		invalid["name"] = []string{PaymentCategoryValidationRequired}
	}
	if value, present := object.Lookup("parent"); present {
		write.ParentPresent = true
		if value != nil && value != "" {
			pk, code, err := FolderPKValue(value)
			if err != nil {
				return write, nil, err
			}
			if code != "" {
				invalid["parent"] = []string{code}
			} else {
				write.Parent = &pk
			}
		}
	}
	if value, present := object.Lookup("sport_association"); present && partial {
		write.AssociationPresent = true
		if upload, ok := value.(domain.RequestUpload); ok {
			if _, err := folderUploadCode(upload); err != nil {
				return write, nil, err
			}
			invalid["sport_association"] = []string{"related-invalid-uuid:" + upload.Name}
		} else if value == nil || value == "" {
			invalid["sport_association"] = []string{PaymentCategoryValidationNull}
		} else if id, code := supplierRelated(value); code != "" {
			invalid["sport_association"] = []string{code}
		} else {
			write.Association, write.AssociationRaw = id, value
		}
	}
	if value, present := object.Lookup("created_at"); present && !(form && value == "") {
		if value == nil {
			invalid["created_at"] = []string{PaymentCategoryValidationNull}
		} else if text, ok := value.(string); !ok {
			invalid["created_at"] = []string{PaymentCategoryValidationDateTime}
		} else if parsed, err := parsePaymentCategoryDjangoDateTime(text); errors.Is(err, errPaymentCategoryDateTimeOverflow) {
			invalid["created_at"] = []string{PaymentCategoryValidationDateTimeOverflow}
		} else if err != nil {
			invalid["created_at"] = []string{PaymentCategoryValidationDateTime}
		} else {
			parsed = parsed.UTC()
			write.CreatedAt = &parsed
		}
	}
	return write, invalid, nil
}

// FolderPKValue is PrimaryKeyRelatedField.to_internal_value up to the
// lookup: booleans, lists and dicts are incorrect types; otherwise Python's
// int() (strings with whitespace, a sign, Unicode decimal digits and single
// underscores; floats truncated) or an incorrect type.
func FolderPKValue(value any) (FolderPK, string, error) {
	var integer *big.Int
	switch v := value.(type) {
	case bool:
		return FolderPK{}, "related-incorrect-type:bool", nil
	case domain.RequestUpload:
		code, err := folderUploadCode(v)
		return FolderPK{}, code, err
	case json.Number:
		raw := string(v)
		if strings.ContainsAny(raw, ".eE") {
			number, err := strconv.ParseFloat(raw, 64)
			if math.IsInf(number, 0) {
				return FolderPK{}, "", domain.ErrFolderPKOverflow
			}
			if err != nil && !errors.Is(err, strconv.ErrRange) {
				return FolderPK{}, "related-incorrect-type:float", nil
			}
			integer, _ = new(big.Float).SetFloat64(math.Trunc(number)).Int(nil)
		} else if parsed, ok := new(big.Int).SetString(raw, 10); ok {
			integer = parsed
		} else {
			return FolderPK{}, "related-incorrect-type:int", nil
		}
	case string:
		parsed, ok := pythonIntString(v)
		if !ok {
			return FolderPK{}, "related-incorrect-type:str", nil
		}
		integer = parsed
	default:
		return FolderPK{}, "related-incorrect-type:" + pythonTypeName(value), nil
	}
	pk := FolderPK{Raw: value}
	if integer.IsInt64() && integer.Int64() >= folderIntegerMin && integer.Int64() <= folderIntegerMax {
		pk.ID = integer.Int64()
	} else {
		pk.Missing = true
	}
	return pk, "", nil
}

// FolderLookupPK is get_object()'s pk from the URL: ok=false is the bare
// "Not found." (int() rejects it); Missing finds nothing.
func FolderLookupPK(raw string) (FolderPK, bool) {
	integer, ok := pythonIntString(raw)
	if !ok {
		return FolderPK{}, false
	}
	pk := FolderPK{Raw: raw}
	if integer.IsInt64() && integer.Int64() >= folderIntegerMin && integer.Int64() <= folderIntegerMax {
		pk.ID = integer.Int64()
	} else {
		pk.Missing = true
	}
	return pk, true
}

// pythonIntString is int(str): surrounding Unicode whitespace, an optional
// sign, then decimal digits (any Unicode Nd digit) with single underscores
// between them.
func pythonIntString(text string) (*big.Int, bool) {
	text = strings.TrimFunc(text, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
	sign := ""
	if strings.HasPrefix(text, "+") || strings.HasPrefix(text, "-") {
		sign, text = text[:1], text[1:]
	}
	var digits strings.Builder
	digits.WriteString(sign)
	separated := true // no leading underscore
	for _, r := range text {
		if r == '_' {
			if separated {
				return nil, false
			}
			separated = true
			continue
		}
		digit, ok := pythonDecimal(r)
		if !ok {
			return nil, false
		}
		digits.WriteByte(byte('0' + digit))
		separated = false
	}
	if separated {
		return nil, false
	}
	return new(big.Int).SetString(digits.String(), 10)
}

// pythonDecimal is the decimal value of a Unicode Nd digit; Nd digits come
// in contiguous runs of ten starting at zero.
func pythonDecimal(r rune) (int, bool) {
	if r >= '0' && r <= '9' {
		return int(r - '0'), true
	}
	for _, span := range unicode.Nd.R16 {
		if r >= rune(span.Lo) && r <= rune(span.Hi) {
			return int(r-rune(span.Lo)) % 10, true
		}
	}
	for _, span := range unicode.Nd.R32 {
		if r >= rune(span.Lo) && r <= rune(span.Hi) {
			return int(r-rune(span.Lo)) % 10, true
		}
	}
	return 0, false
}

// FolderNodeOf renders FolderSerializer for a folder and its descendants
// (tree order).
func FolderNodeOf(folder domain.Folder, descendants []domain.Folder) FolderNode {
	return FolderNode{ID: folder.ID, Name: folder.Name, Parent: folder.ParentID, AssociationID: folder.AssociationID,
		CreatedAt: DRFUTCDateTime(folder.CreatedAt), Level: folder.Level, Children: FolderNodes(descendants, &folder.ID)}
}

// FolderBodyType is type(request.data).__name__ for a non-dict body.
func FolderBodyType(value any) string { return pythonTypeName(value) }

// folderUploadCode is the incorrect-type code of a related field given a file
// part: the QueryDict value is an InMemoryUploadedFile up to the in-memory
// limit; larger parts are temporary files whose handling stays refused.
func folderUploadCode(upload domain.RequestUpload) (string, error) {
	if upload.Temp {
		return "", domain.ErrFolderWriteNotPorted
	}
	return "related-incorrect-type:InMemoryUploadedFile", nil
}
