// Package pystr holds CPython str methods shared by packages that cannot
// depend on internal/dto.
package pystr

import "unicode"

// casedLetter and caseIgnorable are the Unicode properties str.lower()
// consults for the final sigma rule.
func casedLetter(r rune) bool {
	return unicode.IsUpper(r) || unicode.IsLower(r) || unicode.IsTitle(r) || unicode.Is(unicode.Other_Lowercase, r) || unicode.Is(unicode.Other_Uppercase, r)
}

func caseIgnorable(r rune) bool {
	switch r {
	case 0x27, 0x2E, 0x3A, 0x5E, 0x60, 0xA8, 0xAD, 0xAF, 0xB4, 0xB7, 0xB8, 0x387, 0x55F, 0x5F4, 0x2018, 0x2019, 0x2024, 0x2027,
		0xFE13, 0xFE52, 0xFE55, 0xFF07, 0xFF0E, 0xFF1A, 0xFF3E, 0xFF40, 0xFFE3:
		return true
	}
	return unicode.In(r, unicode.Mn, unicode.Me, unicode.Cf, unicode.Lm, unicode.Sk)
}

// Lower is str.lower(): the simple lower-case mapping, U+0130 as 'i' with a
// combining dot, and the final sigma rule (SpecialCasing).
func Lower(text string) string { return string(LowerRunes([]rune(text))) }

// LowerRunes is Lower on code points (lone surrogates map to themselves).
func LowerRunes(runes []rune) []rune {
	out := make([]rune, 0, len(runes))
	for i, r := range runes {
		switch {
		case r == 0x130:
			out = append(out, 'i', 0x307)
		case r == 0x3A3:
			before := false
			for j := i - 1; j >= 0; j-- {
				if caseIgnorable(runes[j]) {
					continue
				}
				before = casedLetter(runes[j])
				break
			}
			after := false
			for j := i + 1; j < len(runes); j++ {
				if caseIgnorable(runes[j]) {
					continue
				}
				after = casedLetter(runes[j])
				break
			}
			if before && !after {
				out = append(out, 0x3C2)
			} else {
				out = append(out, 0x3C3)
			}
		case r >= 0xd800 && r <= 0xdfff:
			out = append(out, r)
		default:
			out = append(out, unicode.ToLower(r))
		}
	}
	return out
}
