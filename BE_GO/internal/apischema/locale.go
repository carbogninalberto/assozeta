package apischema

import (
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// CatalogKey mirrors DjangoTranslation(language) for the "django" domain: for
// every catalog directory, the catalogs gettext.find(..., all=True) selects for
// to_locale(language), and whether the default language is chained as the
// fallback. Documents are generated per key. ok is false for a language
// containing a path separator. ("", "." and ".." expansions select nothing:
// the generator asserts no catalog sits directly in or above a directory.)
//
// No catalog directory name carries a codeset or a modifier (the generator
// asserts it), so those parts only have to be split off at the right place;
// their exact spelling never selects a catalog.
func CatalogKey(language string) (string, bool) {
	d := loaded()
	if strings.Contains(language, "/") {
		return "", false
	}
	var expansions []string
	seen := map[string]bool{}
	for _, expansion := range expandLanguage(normalizeLocale(toLocale(language), d.LocaleAlias)) {
		if !seen[expansion] {
			seen[expansion] = true
			expansions = append(expansions, expansion)
		}
	}
	var key strings.Builder
	if language == d.LanguageCode || strings.HasPrefix(language, "en") {
		key.WriteString("0")
	} else {
		key.WriteString("1")
	}
	for _, directory := range d.directories {
		key.WriteString("|")
		first := true
		for _, expansion := range expansions {
			if expansion == "C" {
				break
			}
			if _, ok := directory[expansion]; ok {
				if !first {
					key.WriteString(",")
				}
				key.WriteString(expansion)
				first = false
			}
		}
	}
	return key.String(), true
}

// toLocale is django.utils.translation.to_locale.
func toLocale(language string) string {
	lang, country, _ := strings.Cut(dto.PythonLower(language), "-")
	if country == "" {
		runes := []rune(language)
		if len(runes) <= 3 {
			return dto.PythonLower(language)
		}
		return dto.PythonLower(string(runes[:3])) + string(runes[3:])
	}
	country, tail, hasTail := strings.Cut(country, "-")
	if len([]rune(country)) > 2 {
		country = pythonTitle(country)
	} else {
		country = dto.PythonUpper(country)
	}
	if hasTail && tail != "" {
		country += "-" + tail
	}
	return lang + "_" + country
}

// pythonTitle is str.title(): a cased character after a cased one is lower
// case, any other cased character is title case (with the full case mappings
// that expand to several characters).
func pythonTitle(value string) string {
	var out strings.Builder
	previousCased := false
	for _, r := range value {
		cased := unicode.IsUpper(r) || unicode.IsLower(r) || unicode.IsTitle(r)
		switch {
		case cased && previousCased:
			out.WriteString(dto.PythonLower(string(r)))
		case cased:
			if expansion, ok := titleExpansions[r]; ok {
				out.WriteString(expansion)
			} else {
				out.WriteRune(unicode.ToTitle(r))
			}
		default:
			out.WriteRune(r)
		}
		previousCased = cased
	}
	return out.String()
}

// titleExpansions are the SpecialCasing title-case mappings into ASCII letters.
var titleExpansions = map[rune]string{'ß': "Ss", 'ﬀ': "Ff", 'ﬁ': "Fi", 'ﬂ': "Fl", 'ﬃ': "Ffi", 'ﬄ': "Ffl", 'ﬅ': "St", 'ﬆ': "St"}

// normalizeLocale is locale.normalize. Encodings that _replace_encoding would
// rewrite are replaced by a placeholder: only their position matters here.
func normalizeLocale(name string, alias map[string]string) string {
	code := dto.PythonLower(name)
	code = strings.ReplaceAll(code, ":", ".")
	modifier := ""
	if before, after, found := strings.Cut(code, "@"); found {
		code, modifier = before, after
	}
	langname, encoding := code, ""
	if strings.Contains(code, ".") {
		parts := strings.Split(code, ".")
		langname, encoding = parts[0], parts[1]
	}
	langEnc := langname
	if encoding != "" {
		langEnc += "." + strings.ReplaceAll(strings.ReplaceAll(encoding, "-", ""), "_", "")
	}
	lookup := langEnc
	if modifier != "" {
		lookup += "@" + modifier
	}
	if value, ok := alias[lookup]; ok {
		return value
	}
	if modifier != "" {
		if value, ok := alias[langEnc]; ok {
			if !strings.Contains(value, "@") {
				return appendModifier(value, modifier)
			}
			if _, defined, _ := strings.Cut(value, "@"); strings.ToLower(defined) == modifier {
				return value
			}
		}
	}
	if encoding != "" {
		lookup = langname
		if modifier != "" {
			lookup += "@" + modifier
		}
		if value, ok := alias[lookup]; ok {
			if !strings.Contains(value, "@") {
				return replaceEncoding(value)
			}
			value, defined, _ := strings.Cut(value, "@")
			return replaceEncoding(value) + "@" + defined
		}
		if modifier != "" {
			if value, ok := alias[langname]; ok {
				if !strings.Contains(value, "@") {
					return appendModifier(replaceEncoding(value), modifier)
				}
				value, defined, _ := strings.Cut(value, "@")
				if strings.ToLower(defined) == modifier {
					return replaceEncoding(value) + "@" + defined
				}
			}
		}
	}
	return name
}

func replaceEncoding(code string) string {
	langname, _, _ := strings.Cut(code, ".")
	return langname + ".codeset"
}

// appendModifier is locale._append_modifier up to the codeset spelling: the
// "euro" variants it returns differ only in codeset and modifier.
func appendModifier(code, modifier string) string {
	if modifier == "euro" {
		return code
	}
	return code + "@" + modifier
}

// expandLanguage is gettext._expand_lang after normalization.
func expandLanguage(locale string) []string {
	const (
		codesetBit   = 1 << 0
		territoryBit = 1 << 1
		modifierBit  = 1 << 2
	)
	mask := 0
	modifier, codeset, territory := "", "", ""
	if position := strings.Index(locale, "@"); position >= 0 {
		modifier, locale = locale[position:], locale[:position]
		mask |= modifierBit
	}
	if position := strings.Index(locale, "."); position >= 0 {
		codeset, locale = locale[position:], locale[:position]
		mask |= codesetBit
	}
	if position := strings.Index(locale, "_"); position >= 0 {
		territory, locale = locale[position:], locale[:position]
		mask |= territoryBit
	}
	var result []string
	for i := mask; i >= 0; i-- {
		if i&^mask != 0 {
			continue
		}
		value := locale
		if i&territoryBit != 0 {
			value += territory
		}
		if i&codesetBit != 0 {
			value += codeset
		}
		if i&modifierBit != 0 {
			value += modifier
		}
		result = append(result, value)
	}
	return result
}
