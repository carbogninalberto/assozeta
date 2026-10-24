package jwt

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// golang-jwt's JSON-number decoder otherwise accepts trailing payload data and
// does not apply Python's integer-digit bound. Scan every value, including
// duplicate/unused claims, before relying on its cryptographic verification.
func ValidDocument(ctx context.Context, data []byte) bool {
	if !utf8.Valid(data) || !json.Valid(data) {
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	depth := 0
	for {
		if ctx.Err() != nil {
			return false
		}
		token, err := decoder.Token()
		if err == io.EOF {
			return true
		}
		if err != nil {
			return false
		}
		switch value := token.(type) {
		case json.Number:
			text := string(value)
			if !strings.ContainsAny(text, ".eE") && len(strings.TrimPrefix(text, "-")) > 4300 {
				return false
			}
		case json.Delim:
			if value == '{' || value == '[' {
				depth++
			} else {
				depth--
			}
			if depth > 1000 {
				return false
			}
		}
	}
}

// ValidProtectedHeader applies PyJWT's understood critical fields and key-ID shape.
func ValidProtectedHeader(header map[string]any) bool {
	if value, present := header["kid"]; present {
		if _, ok := value.(string); !ok {
			return false
		}
	}
	if value, ok := header["b64"].(bool); ok && !value {
		return false
	}
	if raw, present := header["crit"]; present {
		items, ok := raw.([]any)
		if !ok || len(items) == 0 {
			return false
		}
		for _, item := range items {
			name, ok := item.(string)
			if !ok || name != "b64" {
				return false
			}
			if _, present := header[name]; !present {
				return false
			}
		}
	}
	return true
}

// PyJWT validates optional time claims with Python int(), including numeric
// strings and truncation toward zero. This is separate from REST token policy.
func IntegerClaim(value any) (*big.Int, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewInt(1), true
		}
		return big.NewInt(0), true
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			if len(strings.TrimPrefix(text, "-")) > 4300 {
				return nil, false
			}
			n, ok := new(big.Int).SetString(text, 10)
			return n, ok
		}
		n, err := strconv.ParseFloat(text, 64)
		if err != nil || math.IsNaN(n) || math.IsInf(n, 0) {
			return nil, false
		}
		i, _ := big.NewFloat(n).Int(nil)
		return i, true
	case string:
		text := strings.TrimFunc(v, unicode.IsSpace)
		var out strings.Builder
		if strings.HasPrefix(text, "+") || strings.HasPrefix(text, "-") {
			out.WriteByte(text[0])
			text = text[1:]
		}
		previous, digits := false, 0
		for _, r := range text {
			if r == '_' {
				if !previous {
					return nil, false
				}
				previous = false
				continue
			}
			digit := -1
			for _, v := range unicode.Digit.R16 {
				if r >= rune(v.Lo) && r <= rune(v.Hi) && (r-rune(v.Lo))%rune(v.Stride) == 0 {
					digit = int((r-rune(v.Lo))/rune(v.Stride)) % 10
					break
				}
			}
			if digit < 0 {
				for _, v := range unicode.Digit.R32 {
					if uint32(r) >= v.Lo && uint32(r) <= v.Hi && (uint32(r)-v.Lo)%v.Stride == 0 {
						digit = int((uint32(r)-v.Lo)/v.Stride) % 10
						break
					}
				}
			}
			if digit < 0 {
				return nil, false
			}
			out.WriteByte(byte('0' + digit))
			digits++
			previous = true
			if digits > 4300 {
				return nil, false
			}
		}
		if !previous {
			return nil, false
		}
		n, ok := new(big.Int).SetString(out.String(), 10)
		return n, ok
	}
	return nil, false
}
