// Package mentions ports application.models.utils.filter_mentions: the
// rich-text header, footer and template bodies parsed by BeautifulSoup
// 4.13 (html.parser, Python 3.13), mention spans replaced with context
// values, legacy @alias words substituted in the text nodes and the tree
// serialized again with str(soup).
//
// The parse is restricted to a proven subset of HTML: well-formed elements
// (tags lowercased; void elements; a missing end tag at the end of the
// input), double- or single-quoted or bare attributes, text with the
// &amp; &lt; &gt; &quot; &apos; &nbsp; and numeric references. Comments,
// declarations, processing instructions, script/style/raw-text elements,
// <pre> and <textarea> (which keep whitespace), mismatched end tags and
// other ampersands are refused with ErrUnsupported rather than imitating
// html.parser's recovery rules.
package mentions

import (
	"errors"
	"fmt"
	"sort"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrUnsupported marks markup or a mention outside the port.
var ErrUnsupported = errors.New("mentions: unsupported input")

func unsupported(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ErrUnsupported, fmt.Sprintf(format, args...))
}

// Resolver is get_nested_attr(context_objects[name], path) for one context
// object: the attribute path's str() ("" for None and missing attributes).
type Resolver func(path string) (string, error)

// Context is filter_mentions' context_objects: the objects present by name.
type Context map[string]Resolver

// LegacyAliases is LEGACY_MENTION_ALIASES.
var LegacyAliases = map[string]string{
	"nome": "associate.first_name", "cognome": "associate.last_name", "codicefiscale": "associate.tax_code",
	"cf": "associate.tax_code", "datadinascita": "associate.born_date", "datanascita": "associate.born_date",
	"cittadinascita": "associate.born_city", "indirizzo": "associate.address", "citta": "associate.address_city",
	"cap": "associate.address_cap", "nazionalita": "associate.nationality", "email": "associate.email",
	"telefono": "associate.phone", "note": "associate.notes", "nometutore": "main_tutor.first_name",
	"cognometutore": "main_tutor.last_name", "codicefiscaletutore": "main_tutor.tax_code", "emailtutore": "main_tutor.email",
	"telefonotutore": "main_tutor.phone", "numeroricevuta": "invoice.number", "dataricevuta": "invoice.creation_date",
	"importoattivita": "invoice.activity_fee", "importoiscrizione": "invoice.membership_fee",
	"importototale": "invoice.total_amount", "dataodierna": "other.today", "listacorsi": "other.courses_list",
}

// aliasOrder is sorted(aliases, key=len, reverse=True) (stable).
var aliasOrder = func() []string {
	names := []string{"nome", "cognome", "codicefiscale", "cf", "datadinascita", "datanascita", "cittadinascita", "indirizzo",
		"citta", "cap", "nazionalita", "email", "telefono", "note", "nometutore", "cognometutore", "codicefiscaletutore",
		"emailtutore", "telefonotutore", "numeroricevuta", "dataricevuta", "importoattivita", "importoiscrizione",
		"importototale", "dataodierna", "listacorsi"}
	sort.SliceStable(names, func(i, j int) bool { return len(names[i]) > len(names[j]) })
	return names
}()

// Filter is filter_mentions(content, context_objects). present is false
// for None.
func Filter(content string, present bool, context Context) (string, error) {
	if !present || content == "" {
		return "", nil
	}
	root, err := parse(content)
	if err != nil {
		return "", err
	}
	// Mention spans, in document order (find_all), replaced by their value.
	for _, span := range root.findAll(isMentionSpan) {
		key, _ := span.attr("key")
		if key == "" {
			continue
		}
		value, err := resolve(key, context)
		if err != nil {
			return "", err
		}
		span.replaceWithText(value)
	}
	// Legacy @alias words in the remaining text nodes.
	for _, text := range root.findAllText() {
		parent := text.parent
		if parent.name == "span" && parent.hasClass("mention") {
			continue
		}
		replaced, err := substituteAliases(text.text, context)
		if err != nil {
			return "", err
		}
		text.text = replaced
	}
	var out strings.Builder
	for _, child := range root.children {
		child.write(&out)
	}
	return out.String(), nil
}

// resolve is _resolve_mention: the object's attribute, stripped, with an
// ISO date (YYYY-MM-DD) shown as DD/MM/YYYY.
func resolve(key string, context Context) (string, error) {
	name, path, found := strings.Cut(key, ".")
	if !found {
		return "", nil
	}
	resolver, ok := context[name]
	if !ok {
		return "", nil
	}
	if path == "" {
		// get_nested_attr returns "" for an empty path.
		return "", nil
	}
	value, err := resolver(path)
	if err != nil {
		return "", err
	}
	value = dto.PythonStrip(value)
	runes := []rune(value)
	if len(runes) == 10 && runes[4] == '-' && runes[7] == '-' {
		return string(runes[8:10]) + "/" + string(runes[5:7]) + "/" + string(runes[0:4]), nil
	}
	return value, nil
}

// Runes whose Python case-insensitive match reaches an ASCII alias letter
// while .lower() does not give it back (the alias lookup would raise).
func foldsToASCII(r rune) bool { return r == 0x130 || r == 0x131 || r == 0x17F || r == 0x212A }

// pythonWord is \w in a Python str pattern: str.isalnum() or '_'.
func pythonWord(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r)
}

// substituteAliases is alias_pattern.sub(...) over one text node:
// (?<![\w@])@(alias...)\b, IGNORECASE, longest alias first.
func substituteAliases(text string, context Context) (string, error) {
	if !strings.Contains(text, "@") {
		return text, nil
	}
	for _, r := range text {
		if foldsToASCII(r) {
			return "", unsupported("case-folding rune %U near an alias", r)
		}
	}
	runes := []rune(text)
	var out strings.Builder
	for i := 0; i < len(runes); i++ {
		if runes[i] == '@' && (i == 0 || !(pythonWord(runes[i-1]) || runes[i-1] == '@')) {
			if alias := matchAlias(runes, i+1); alias != "" {
				value, err := resolve(LegacyAliases[alias], context)
				if err != nil {
					return "", err
				}
				out.WriteString(value)
				i += len(alias)
				continue
			}
		}
		out.WriteRune(runes[i])
	}
	return out.String(), nil
}

func matchAlias(runes []rune, start int) string {
	for _, alias := range aliasOrder {
		end := start + len(alias)
		if end > len(runes) {
			continue
		}
		if !strings.EqualFold(string(runes[start:end]), alias) || !isASCII(runes[start:end]) {
			continue
		}
		if end < len(runes) && pythonWord(runes[end]) {
			continue
		}
		return alias
	}
	return ""
}

func isASCII(runes []rune) bool {
	for _, r := range runes {
		if r > 0x7f {
			return false
		}
	}
	return true
}
