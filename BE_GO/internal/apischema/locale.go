package apischema

import (
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// CatalogKey mirrors DjangoTranslation(language) for the "django" domain: for
// every catalog directory, the catalogs gettext.find(..., all=True) selects for
// to_locale(language), and whether the default language is chained as the
// fallback. Documents are rendered per key. The language string decides the
// fallback flag, the resolved catalogs the per-directory lists.
//
// gettext joins every expansion to each catalog directory as a path
// (os.path.join(localedir, language, "LC_MESSAGES", "django.mo")), so a
// language with a path separator walks the file system. The port models the
// directories that exist in a catalog directory (see catalogPath): the
// directory itself, <dir>/<name> and <dir>/<name>/LC_MESSAGES for every catalog
// name. A path that leaves that model or ends anywhere else selects nothing
// (the generator asserts no catalog sits directly in or above a directory).
//
// No catalog directory name carries a codeset or a modifier (the generator
// asserts it), so those parts only have to be split off at the right place;
// their exact spelling never selects a catalog. A rewritten encoding never
// contains a path separator (locale._replace_encoding normalizes it to
// alphanumerics), so the placeholder in normalizeLocale keeps the walk intact.
func CatalogKey(language string) (string, bool) {
	d := loaded()
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
			name, ok := catalogPath(expansion, directory)
			if !ok {
				continue
			}
			if !first {
				key.WriteString(",")
			}
			key.WriteString(name)
			first = false
		}
	}
	return key.String(), true
}

// catalogPath resolves one gettext expansion against the directories that exist
// in a catalog directory, as the kernel resolves a path. Empty and "." components
// stay where they are, ".." goes up one level and leaves the model above the
// directory, and any other component must be a directory at that point. The
// expansion selects a catalog exactly when the walk ends at <dir>/<name>, where
// <name>/LC_MESSAGES/django.mo exists. An absolute expansion (starting with "/")
// replaces the directory in os.path.join: nothing outside the model exists.
func catalogPath(expansion string, names map[string]struct{}) (string, bool) {
	if strings.HasPrefix(expansion, "/") {
		return "", false
	}
	const (
		atDirectory = iota // <dir>
		atCatalog          // <dir>/<name>
		atMessages         // <dir>/<name>/LC_MESSAGES
	)
	depth, name := atDirectory, ""
	for _, component := range strings.Split(expansion, "/") {
		switch component {
		case "", ".":
			// The kernel keeps empty and "." components where they are.
		case "..":
			if depth == atDirectory {
				return "", false
			}
			depth--
			if depth == atDirectory {
				name = ""
			}
		default:
			switch depth {
			case atDirectory:
				if _, ok := names[component]; !ok {
					return "", false
				}
				depth, name = atCatalog, component
			case atCatalog:
				if component != "LC_MESSAGES" {
					return "", false
				}
				depth = atMessages
			default:
				// <dir>/<name>/LC_MESSAGES has no entries in the model.
				return "", false
			}
		}
	}
	if depth != atCatalog {
		return "", false
	}
	return name, true
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
