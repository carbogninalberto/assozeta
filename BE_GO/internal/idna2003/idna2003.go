// Package idna2003 is Python's 'idna' codec for encoding: nameprep (RFC 3491 on the
// stringprep tables) and Punycode (RFC 3492) per label, as str.encode('idna') does.
package idna2003

import (
	"errors"
	"strings"
	"unicode/utf8"

	"golang.org/x/text/unicode/norm"
)

// ErrUnicode stands for Python's UnicodeError (invalid domain text).
var ErrUnicode = errors.New("invalid domain name")

func inRanges(table [][2]rune, r rune) bool {
	low, high := 0, len(table)-1
	for low <= high {
		mid := (low + high) / 2
		switch {
		case r < table[mid][0]:
			high = mid - 1
		case r > table[mid][1]:
			low = mid + 1
		default:
			return true
		}
	}
	return false
}

func isASCII(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] >= utf8.RuneSelf {
			return false
		}
	}
	return true
}

// nameprep is encodings.idna.nameprep.
func nameprep(label string) (string, error) {
	var mapped strings.Builder
	for _, r := range label {
		if inRanges(mappedToNothing, r) {
			continue
		}
		if folded, ok := caseFolding[r]; ok {
			mapped.WriteString(folded)
		} else {
			mapped.WriteRune(r)
		}
	}
	result := norm.NFKC.String(mapped.String())
	runes := []rune(result)
	randAL := make([]bool, len(runes))
	any := false
	for i, r := range runes {
		if inRanges(prohibited, r) {
			return "", ErrUnicode
		}
		randAL[i] = inRanges(randALTable, r)
		any = any || randAL[i]
	}
	if any {
		for _, r := range runes {
			if inRanges(lCat, r) {
				return "", ErrUnicode
			}
		}
		if !randAL[0] || !randAL[len(randAL)-1] {
			return "", ErrUnicode
		}
	}
	return result, nil
}

// toASCIILabel is encodings.idna.ToASCII.
func toASCIILabel(label string) (string, error) {
	size := func(text string) bool { return len(text) > 0 && len(text) < 64 }
	if isASCII(label) {
		if size(label) {
			return label, nil
		}
		return "", ErrUnicode
	}
	label, err := nameprep(label)
	if err != nil {
		return "", err
	}
	if isASCII(label) {
		if size(label) {
			return label, nil
		}
		return "", ErrUnicode
	}
	if len(label) >= 4 && strings.EqualFold(label[:4], "xn--") {
		return "", ErrUnicode
	}
	encoded, err := punycode(label)
	if err != nil {
		return "", err
	}
	if label = "xn--" + encoded; size(label) {
		return label, nil
	}
	return "", ErrUnicode
}

// ToASCII is str.encode('idna'): an ASCII text only has its label lengths checked; any other text is split at the four
// dots (U+002E, U+3002, U+FF0E, U+FF61), a trailing dot is kept, and each label is encoded.
func ToASCII(domain string) (string, error) {
	if domain == "" {
		return "", nil
	}
	if isASCII(domain) {
		labels := strings.Split(domain, ".")
		for _, label := range labels[:len(labels)-1] {
			if len(label) == 0 || len(label) >= 64 {
				return "", ErrUnicode
			}
		}
		if len(labels[len(labels)-1]) >= 64 {
			return "", ErrUnicode
		}
		return domain, nil
	}
	var parts []string
	var current strings.Builder
	for _, r := range domain {
		if r == '.' || r == 0x3002 || r == 0xFF0E || r == 0xFF61 {
			parts = append(parts, current.String())
			current.Reset()
			continue
		}
		current.WriteRune(r)
	}
	parts = append(parts, current.String())
	trailing := ""
	if parts[len(parts)-1] == "" {
		trailing = "."
		parts = parts[:len(parts)-1]
	}
	encoded := make([]string, len(parts))
	for i, label := range parts {
		var err error
		if encoded[i], err = toASCIILabel(label); err != nil {
			return "", err
		}
	}
	return strings.Join(encoded, ".") + trailing, nil
}

// punycode is RFC 3492 encoding as Python's 'punycode' codec writes it.
func punycode(text string) (string, error) {
	const (
		base        = 36
		tMin, tMax  = 1, 26
		skew, damp  = 38, 700
		initialBias = 72
		initialN    = 128
	)
	runes := []rune(text)
	var out []byte
	basic := 0
	for _, r := range runes {
		if r < 128 {
			out = append(out, byte(r))
			basic++
		}
	}
	handled := basic
	if basic > 0 {
		out = append(out, '-')
	}
	digit := func(d int) byte {
		if d < 26 {
			return byte('a' + d)
		}
		return byte('0' + d - 26)
	}
	adapt := func(delta, points int, first bool) int {
		if first {
			delta /= damp
		} else {
			delta /= 2
		}
		delta += delta / points
		k := 0
		for delta > ((base-tMin)*tMax)/2 {
			delta /= base - tMin
			k += base
		}
		return k + (base-tMin+1)*delta/(delta+skew)
	}
	n, delta, bias := initialN, 0, initialBias
	for handled < len(runes) {
		next := 0x110000
		for _, r := range runes {
			if int(r) >= n && int(r) < next {
				next = int(r)
			}
		}
		delta += (next - n) * (handled + 1)
		n = next
		for _, r := range runes {
			switch {
			case int(r) < n:
				delta++
			case int(r) == n:
				q := delta
				for k := base; ; k += base {
					t := k - bias
					if t < tMin {
						t = tMin
					} else if t > tMax {
						t = tMax
					}
					if q < t {
						break
					}
					out = append(out, digit(t+(q-t)%(base-t)))
					q = (q - t) / (base - t)
				}
				out = append(out, digit(q))
				bias = adapt(delta, handled+1, handled == basic)
				delta = 0
				handled++
			}
		}
		delta++
		n++
	}
	return string(out), nil
}
