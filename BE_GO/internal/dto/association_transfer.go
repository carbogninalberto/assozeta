package dto

import (
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"strings"
	"unicode"
	"unicode/utf8"
)

// ErrAssociationExportDocumentInvalid is UUIDField.to_python's ValidationError
// during the archive lookup: Django does not handle it (a 500 page).
var ErrAssociationExportDocumentInvalid = errors.New("export document_id is not a UUID")

// AssociationExportDocument is UUIDField.to_python for document_id: an int
// (bool included) is uuid.UUID(int=n), a string is parsed as hex; anything
// else is the unhandled ValidationError. Strings Python's int(hex, 16) reads
// differently from a plain hex check (underscores, signs, spaces) are refused.
func AssociationExportDocument(value any) (string, error) {
	switch v := value.(type) {
	case bool:
		if v {
			return "00000000-0000-0000-0000-000000000001", nil
		}
		return "00000000-0000-0000-0000-000000000000", nil
	case string:
		if strings.Trim(v, "0123456789abcdefABCDEF-") != "" {
			if id, ok := pythonUUIDHex(v); ok {
				return id, nil
			}
			return "", ErrAssociationExportDocumentInvalid
		}
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "", ErrAssociationExportDocumentInvalid
		}
	default:
		return "", ErrAssociationExportDocumentInvalid
	}
	id, ok := supplierUUID(value)
	if !ok {
		return "", ErrAssociationExportDocumentInvalid
	}
	return id, nil
}

// pythonUUIDHex is uuid.UUID(hex=text): urn:/uuid: prefixes and braces dropped, every
// hyphen removed, exactly 32 characters left, then int(text, 16) (surrounding
// whitespace, a plus sign, a 0x prefix, single underscores between digits and any
// Unicode decimal digit are read).
func pythonUUIDHex(text string) (string, bool) {
	text = strings.ReplaceAll(strings.ReplaceAll(text, "urn:", ""), "uuid:", "")
	text = strings.Trim(text, "{}")
	text = strings.ReplaceAll(text, "-", "")
	if utf8.RuneCountInString(text) != 32 {
		return "", false
	}
	text = strings.TrimFunc(text, pythonSpace)
	text = strings.TrimPrefix(text, "+")
	if len(text) >= 2 && text[0] == '0' && (text[1] == 'x' || text[1] == 'X') {
		text = text[2:]
		text = strings.TrimPrefix(text, "_")
	}
	value, previousDigit := new(big.Int), false
	if text == "" {
		return "", false
	}
	for _, r := range text {
		if r == '_' {
			if !previousDigit {
				return "", false
			}
			previousDigit = false
			continue
		}
		digit := -1
		switch {
		case r >= 'a' && r <= 'f':
			digit = int(r-'a') + 10
		case r >= 'A' && r <= 'F':
			digit = int(r-'A') + 10
		default:
			if d, ok := pythonDigit(r); ok {
				digit = d
			}
		}
		if digit < 0 {
			return "", false
		}
		value.Mul(value, big.NewInt(16)).Add(value, big.NewInt(int64(digit)))
		previousDigit = true
	}
	if !previousDigit || value.BitLen() > 128 {
		return "", false
	}
	hexText := fmt.Sprintf("%032x", value)
	return hexText[:8] + "-" + hexText[8:12] + "-" + hexText[12:16] + "-" + hexText[16:20] + "-" + hexText[20:], true
}

func pythonSpace(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) }
