package apischema

import "strings"

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
	if strings.ContainsAny(language, "/\x00") {
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

// toLocale is django.utils.translation.to_locale for ASCII input.
func toLocale(language string) string {
	lang, country, _ := strings.Cut(strings.ToLower(language), "-")
	if country == "" {
		if len(language) <= 3 {
			return strings.ToLower(language)
		}
		return strings.ToLower(language[:3]) + language[3:]
	}
	country, tail, hasTail := strings.Cut(country, "-")
	if len(country) > 2 {
		country = pythonTitle(country)
	} else {
		country = strings.ToUpper(country)
	}
	if hasTail && tail != "" {
		country += "-" + tail
	}
	return lang + "_" + country
}

// pythonTitle is str.title() for ASCII: a letter is upper case after a
// non-letter and lower case after a letter.
func pythonTitle(value string) string {
	out := []byte(value)
	previousCased := false
	for i, c := range out {
		lower, upper := c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z'
		switch {
		case (lower || upper) && previousCased:
			out[i] = strings.ToLower(string(c))[0]
		case lower || upper:
			out[i] = strings.ToUpper(string(c))[0]
		}
		previousCased = lower || upper
	}
	return string(out)
}

// normalizeLocale is locale.normalize. Encodings that _replace_encoding would
// rewrite are replaced by a placeholder: only their position matters here.
func normalizeLocale(name string, alias map[string]string) string {
	code := strings.ToLower(name)
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
