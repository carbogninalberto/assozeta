package service

import (
	"fmt"
	"math/big"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/google/uuid"
)

// Python's uuid.UUID(hex=...) first checks the normalized text length, then
// delegates to int(text,16). Public checkout accepts that integer grammar,
// including signs, separators and Unicode decimal digits. Keep this bounded
// compatibility local to payment-info rather than changing shared scope gates.
func paymentInfoParseUUID(raw string) (uuid.UUID, error) {
	text := strings.ReplaceAll(strings.ReplaceAll(raw, "urn:", ""), "uuid:", "")
	text = strings.ReplaceAll(strings.Trim(text, "{}"), "-", "")
	if utf8.RuneCountInString(text) != 32 {
		return uuid.Nil, &PaymentInfoUUIDError{Detail: "badly formed hexadecimal UUID string"}
	}
	invalid := func() (uuid.UUID, error) {
		return uuid.Nil, &PaymentInfoUUIDError{Detail: "invalid literal for int() with base 16: " + paymentInfoPythonQuote(text)}
	}
	normalized := strings.Map(func(r rune) rune {
		if r <= '9' || !unicode.IsDigit(r) {
			return r
		}
		for _, entry := range unicode.Digit.R16 {
			if uint32(r) >= uint32(entry.Lo) && uint32(r) <= uint32(entry.Hi) && (uint32(r)-uint32(entry.Lo))%uint32(entry.Stride) == 0 {
				return '0' + rune(((uint32(r)-uint32(entry.Lo))/uint32(entry.Stride))%10)
			}
		}
		for _, entry := range unicode.Digit.R32 {
			if uint32(r) >= entry.Lo && uint32(r) <= entry.Hi && (uint32(r)-entry.Lo)%entry.Stride == 0 {
				return '0' + rune(((uint32(r)-entry.Lo)/entry.Stride)%10)
			}
		}
		return r
	}, strings.TrimSpace(text))
	normalized = strings.TrimPrefix(normalized, "+")
	if strings.HasPrefix(normalized, "0x") || strings.HasPrefix(normalized, "0X") {
		normalized = normalized[2:]
		normalized = strings.TrimPrefix(normalized, "_")
	}
	if normalized == "" {
		return invalid()
	}
	digit := false
	for _, r := range normalized {
		if r == '_' {
			if !digit {
				return invalid()
			}
			digit = false
			continue
		}
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'f' || r >= 'A' && r <= 'F') {
			return invalid()
		}
		digit = true
	}
	if !digit {
		return invalid()
	}
	number, ok := new(big.Int).SetString(strings.ReplaceAll(normalized, "_", ""), 16)
	if !ok || number.Sign() < 0 || number.BitLen() > 128 {
		return invalid()
	}
	var id uuid.UUID
	number.FillBytes(id[:])
	return id, nil
}

func paymentInfoPythonQuote(text string) string {
	quote := rune('\'')
	if strings.ContainsRune(text, '\'') && !strings.ContainsRune(text, '"') {
		quote = '"'
	}
	var out strings.Builder
	out.WriteRune(quote)
	for _, r := range text {
		switch {
		case r == quote || r == '\\':
			out.WriteRune('\\')
			out.WriteRune(r)
		case r == '\n':
			out.WriteString(`\n`)
		case r == '\r':
			out.WriteString(`\r`)
		case r == '\t':
			out.WriteString(`\t`)
		case paymentInfoPythonPrintable(r):
			out.WriteRune(r)
		case r <= 0xff:
			fmt.Fprintf(&out, `\x%02x`, r)
		case r <= 0xffff:
			fmt.Fprintf(&out, `\u%04x`, r)
		default:
			fmt.Fprintf(&out, `\U%08x`, r)
		}
	}
	out.WriteRune(quote)
	return out.String()
}

func paymentInfoPythonPrintable(r rune) bool {
	// Python 3.13.16 uses Unicode 15.1, while this Go toolchain uses 15.0.
	// An exhaustive scalar comparison finds exactly these 627 new printable
	// characters and no removals. Preserve Python's error repr classification.
	return unicode.IsPrint(r) || r >= 0x2ffc && r <= 0x2fff || r == 0x31ef || r >= 0x2ebf0 && r <= 0x2ee5d
}
