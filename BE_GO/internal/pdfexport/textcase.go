package pdfexport

import (
	"strings"
	"unicode"
)

// The case mappings of CPython 3.13's str.upper(), str.lower() and str.title():
// the full mappings of Unicode 15.1 with SpecialCasing ('ß' is "SS", 'ﬁ' is
// "FI", 'İ' lowercases to "i" and a combining dot). A code point whose mapping
// is one character is the one of Go's unicode tables (Unicode 15.0), which
// agree with CPython for every code point (TestCaseMappings); the mappings of
// more than one character are the tables of textcase_gen.go.

// casedRune is _PyUnicode_IsCased: the Cased property.
func casedRune(r rune) bool {
	return unicode.IsUpper(r) || unicode.IsLower(r) || unicode.IsTitle(r) ||
		unicode.Is(unicode.Other_Uppercase, r) || unicode.Is(unicode.Other_Lowercase, r)
}

// caseIgnorableRune is _PyUnicode_IsCaseIgnorable.
func caseIgnorableRune(r rune) bool {
	switch r {
	case 0x27, 0x2E, 0x3A, 0x5E, 0x60, 0xA8, 0xAD, 0xAF, 0xB4, 0xB7, 0xB8, 0x387, 0x55F, 0x5F4, 0x2018, 0x2019, 0x2024,
		0x2027, 0xFE13, 0xFE52, 0xFE55, 0xFF07, 0xFF0E, 0xFF1A, 0xFF3E, 0xFF40, 0xFFE3:
		return true
	}
	return unicode.In(r, unicode.Mn, unicode.Me, unicode.Cf, unicode.Lm, unicode.Sk)
}

// finalSigma is handle_capital_sigma: U+03A3 is final when a cased letter
// precedes it (over case-ignorable characters) and no cased letter follows.
func finalSigma(runes []rune, i int) rune {
	j := i - 1
	for ; j >= 0; j-- {
		if !caseIgnorableRune(runes[j]) {
			break
		}
	}
	final := j >= 0 && casedRune(runes[j])
	if final {
		for j = i + 1; j < len(runes); j++ {
			if !caseIgnorableRune(runes[j]) {
				break
			}
		}
		final = j == len(runes) || !casedRune(runes[j])
	}
	if final {
		return 0x3C2
	}
	return 0x3C3
}

// lowerRune is lower_ucs4 of the code point at i of runes: the full lower
// case mapping of the code point, U+03A3 through the final sigma rule.
func lowerRune(runes []rune, i int) []rune {
	r := runes[i]
	if r == 0x3A3 {
		return []rune{finalSigma(runes, i)}
	}
	if mapped, ok := caseLowerExtra[r]; ok {
		return []rune(mapped)
	}
	return []rune{unicode.ToLower(r)}
}

// pythonLower is str.lower().
func pythonLower(text string) string {
	runes := []rune(text)
	out := make([]rune, 0, len(runes))
	for i := range runes {
		out = append(out, lowerRune(runes, i)...)
	}
	return string(out)
}

// pythonUpper is str.upper().
func pythonUpper(text string) string {
	var out strings.Builder
	for _, r := range text {
		if mapped, ok := caseUpperExtra[r]; ok {
			out.WriteString(mapped)
		} else {
			out.WriteRune(unicode.ToUpper(r))
		}
	}
	return out.String()
}

// pythonTitle is str.title(): the first cased character of every word in its
// title case, the cased characters after it in their lower case.
func pythonTitle(text string) string {
	runes := []rune(text)
	out := make([]rune, 0, len(runes))
	previousIsCased := false
	for i, r := range runes {
		if previousIsCased {
			out = append(out, lowerRune(runes, i)...)
		} else if mapped, ok := caseTitleExtra[r]; ok {
			out = append(out, []rune(mapped)...)
		} else {
			out = append(out, unicode.ToTitle(r))
		}
		previousIsCased = casedRune(r)
	}
	return string(out)
}

// transformFrags is textTransformFrags: the case transform of the style
// applied to the text of each fragment.
func transformFrags(frags []*frag, style paraStyle) {
	tt := pythonLower(style.textTransform)
	if tt == "" {
		return
	}
	var mapping func(string) string
	switch tt {
	case "lowercase":
		mapping = pythonLower
	case "uppercase":
		mapping = pythonUpper
	case "capitalize":
		mapping = pythonTitle
	case "none":
		return
	default:
		panic(paraFailure{"ParaStyle.textTransform value is invalid"})
	}
	if len(frags) == 1 {
		frags[0].text = mapping(frags[0].text)
		return
	}
	if tt == "capitalize" {
		// The first fragment and each one that starts a word are titlecased
		// whole; a later fragment titlecases only from its first space.
		pb := true
		for _, f := range frags {
			u := f.text
			if u == "" {
				continue
			}
			if strings.HasPrefix(u, " ") || pb {
				u = mapping(u)
			} else if i := strings.Index(u, " "); i >= 0 {
				u = u[:i] + mapping(u[i:])
			}
			pb = strings.HasSuffix(u, " ")
			f.text = u
		}
		return
	}
	for _, f := range frags {
		if f.text == "" {
			continue
		}
		f.text = mapping(f.text)
	}
}
