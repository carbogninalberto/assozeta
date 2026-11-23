package dto

import (
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	textunicode "golang.org/x/text/encoding/unicode"
)

// ParseQueryPairs decodes a query string or urlencoded body; withAll also keeps every value of a repeated key (QueryDict.getlist).
func ParseQueryPairs(raw string, withAll bool) domain.RequestObject {
	decode := func(value string) string {
		hex := func(c byte) (byte, bool) {
			switch {
			case c >= '0' && c <= '9':
				return c - '0', true
			case c >= 'a' && c <= 'f':
				return c - 'a' + 10, true
			case c >= 'A' && c <= 'F':
				return c - 'A' + 10, true
			}
			return 0, false
		}
		decoded := make([]byte, 0, len(value))
		for i := 0; i < len(value); i++ {
			if value[i] == '+' {
				decoded = append(decoded, ' ')
				continue
			}
			if value[i] == '%' && i+2 < len(value) {
				hi, hiOK := hex(value[i+1])
				lo, loOK := hex(value[i+2])
				if hiOK && loOK {
					decoded = append(decoded, hi<<4|lo)
					i += 2
					continue
				}
			}
			decoded = append(decoded, value[i])
		}
		// The UTF-8 decoder replaces malformed sequences without returning an error.
		text, _ := textunicode.UTF8.NewDecoder().String(string(decoded))
		return text
	}
	result := domain.RequestObject{}
	positions := map[string]int{}
	for _, pair := range strings.Split(raw, "&") {
		if pair == "" {
			continue
		}
		key, value, _ := strings.Cut(pair, "=")
		key, value = decode(key), decode(value)
		if position, ok := positions[key]; ok {
			if withAll {
				if result[position].All == nil {
					result[position].All = []any{result[position].Value}
				}
				result[position].All = append(result[position].All, value)
			}
			result[position].Value = value
		} else {
			positions[key] = len(result)
			result = append(result, domain.RequestField{Name: key, Value: value})
		}
	}
	return result
}
