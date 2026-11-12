package jwt

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"io"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf16"
)

type claimField struct {
	name  string
	value any
}
type claimObject []claimField

// SimpleJWT re-encodes a refresh token without rotating its claims. Preserve
// Python dictionary order, last duplicate values and JSON scalar encoding, then
// sign with the default header; retaining the incoming bytes differs for valid
// padded, whitespace-bearing or custom-header credentials.
func (m *Manager) resignRefresh(encoded string) (string, error) {
	parts := strings.Split(encoded, ".")
	if len(parts) != 3 {
		return "", ErrInvalidToken
	}
	data, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	if err != nil {
		return "", ErrInvalidToken
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	value, err := readClaimValue(d)
	if err != nil {
		return "", ErrInvalidToken
	}
	if _, ok := value.(claimObject); !ok {
		return "", ErrInvalidToken
	}
	if _, err = d.Token(); err != io.EOF {
		return "", ErrInvalidToken
	}
	var out strings.Builder
	if !writeClaimValue(&out, value) {
		return "", ErrInvalidToken
	}
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"EdDSA","typ":"JWT"}`))
	message := header + "." + base64.RawURLEncoding.EncodeToString([]byte(out.String()))
	return message + "." + base64.RawURLEncoding.EncodeToString(ed25519.Sign(m.private, []byte(message))), nil
}

func readClaimValue(d *json.Decoder) (any, error) {
	token, err := d.Token()
	if err != nil {
		return nil, err
	}
	delim, container := token.(json.Delim)
	if !container {
		return token, nil
	}
	switch delim {
	case '{':
		object := claimObject{}
		positions := map[string]int{}
		for d.More() {
			token, err := d.Token()
			if err != nil {
				return nil, err
			}
			key, ok := token.(string)
			if !ok {
				return nil, ErrInvalidToken
			}
			value, err := readClaimValue(d)
			if err != nil {
				return nil, err
			}
			if index, exists := positions[key]; exists {
				object[index].value = value
			} else {
				positions[key] = len(object)
				object = append(object, claimField{key, value})
			}
		}
		end, err := d.Token()
		if err != nil || end != json.Delim('}') {
			return nil, ErrInvalidToken
		}
		return object, nil
	case '[':
		array := []any{}
		for d.More() {
			value, err := readClaimValue(d)
			if err != nil {
				return nil, err
			}
			array = append(array, value)
		}
		end, err := d.Token()
		if err != nil || end != json.Delim(']') {
			return nil, ErrInvalidToken
		}
		return array, nil
	}
	return nil, ErrInvalidToken
}

func writeClaimValue(out *strings.Builder, value any) bool {
	switch v := value.(type) {
	case nil:
		out.WriteString("null")
	case bool:
		out.WriteString(strconv.FormatBool(v))
	case string:
		writeClaimString(out, v)
	case claimObject:
		out.WriteByte('{')
		for i, field := range v {
			if i > 0 {
				out.WriteByte(',')
			}
			writeClaimString(out, field.name)
			out.WriteByte(':')
			if !writeClaimValue(out, field.value) {
				return false
			}
		}
		out.WriteByte('}')
	case []any:
		out.WriteByte('[')
		for i, item := range v {
			if i > 0 {
				out.WriteByte(',')
			}
			if !writeClaimValue(out, item) {
				return false
			}
		}
		out.WriteByte(']')
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			integer, ok := new(big.Int).SetString(text, 10)
			if !ok {
				return false
			}
			out.WriteString(integer.String())
			return true
		}
		number, err := strconv.ParseFloat(text, 64)
		if err != nil || math.IsInf(number, 0) || math.IsNaN(number) {
			return false
		}
		format := byte('f')
		absolute := math.Abs(number)
		if absolute != 0 && (absolute < 1e-4 || absolute >= 1e16) {
			format = 'e'
		}
		text = strconv.FormatFloat(number, format, -1, 64)
		if format == 'f' && !strings.Contains(text, ".") {
			text += ".0"
		}
		out.WriteString(text)
	default:
		return false
	}
	return true
}

func writeClaimString(out *strings.Builder, value string) {
	const hex = "0123456789abcdef"
	escape := func(r rune) {
		out.WriteString(`\u`)
		for shift := 12; shift >= 0; shift -= 4 {
			out.WriteByte(hex[(r>>shift)&15])
		}
	}
	out.WriteByte('"')
	for _, r := range value {
		switch r {
		case '"', '\\':
			out.WriteByte('\\')
			out.WriteRune(r)
		case '\b':
			out.WriteString(`\b`)
		case '\f':
			out.WriteString(`\f`)
		case '\n':
			out.WriteString(`\n`)
		case '\r':
			out.WriteString(`\r`)
		case '\t':
			out.WriteString(`\t`)
		default:
			if r >= 32 && r < 127 {
				out.WriteRune(r)
			} else if r <= 0xffff {
				escape(r)
			} else {
				high, low := utf16.EncodeRune(r)
				escape(high)
				escape(low)
			}
		}
	}
	out.WriteByte('"')
}
