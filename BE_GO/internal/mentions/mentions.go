// Package mentions ports application.models.utils.filter_mentions: the
// rich-text header, footer and template bodies parsed by BeautifulSoup 4.13
// (html.parser, Python 3.13), mention spans replaced with context values,
// legacy @alias words substituted in the text nodes and the tree serialized
// again with str(soup) (BE/application/models/utils.py:151-216).
//
// The parse, the find_all navigation, the replacement in place and the
// rendering all come from internal/bs4prettify, which ports html.parser and
// bs4's tree builder, so any markup Django answers is answered here too
// (ErrPythonRaises only where bs4 itself raises).
package mentions

import (
	"sort"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/bs4prettify"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrAliasKeyError is LEGACY_MENTION_ALIASES[group.lower()] raising KeyError:
// an alias matched case-insensitively through a character whose lower() is
// not the alias itself (U+0130, U+0131 for 'i'; U+017F for 's'). Django does
// not catch it (BE/application/models/utils.py:205-210), so the view answers
// its 500 page.
var ErrAliasKeyError = refusal.AppState("mentions: legacy alias lookup raises KeyError")

// errKeyIsList is _resolve_mention receiving a list key, where key.partition
// raises AttributeError (BE/application/models/utils.py:145-149). A key
// attribute cannot be a cdata-list one: bs4 splits only class, accesskey,
// dropzone, rel, rev, headers, accept-charset, archive, sizes, sandbox and
// for (TreeBuilder.DEFAULT_CDATA_LIST_ATTRIBUTES), so `key` is always a str.
var errKeyIsList = refusal.AppState("mentions: mention key is a list")

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
	soup, err := filterTree(content, context)
	if err != nil {
		return "", err
	}
	return soup.Decode(), nil
}

// filterTree parses content and substitutes the mentions in place.
func filterTree(content string, context Context) (*bs4prettify.Node, error) {
	soup, err := bs4prettify.Parse(content)
	if err != nil {
		return nil, err
	}
	// Mention spans in document order (find_all materializes the list before
	// the first replacement). A span detached with an ancestor is still
	// replaced inside that subtree, which str(soup) never reaches.
	var spans []*bs4prettify.Node
	for _, descendant := range soup.Descendants() {
		if isMentionSpan(descendant) {
			spans = append(spans, descendant)
		}
	}
	for _, span := range spans {
		key, err := mentionKey(span)
		if err != nil {
			return nil, err
		}
		if key == "" {
			continue
		}
		value, err := resolve(key, context)
		if err != nil {
			return nil, err
		}
		if err := span.ReplaceWithString(value); err != nil {
			return nil, err
		}
	}
	// Legacy @alias words in the text nodes that are left, in document order
	// and computed after the span replacement. find_all(string=True) matches
	// every NavigableString subclass (NavigableString, Comment, CData,
	// Declaration, Doctype, ProcessingInstruction, Script, Stylesheet,
	// TemplateString), so comments and attribute-free declarations are
	// rewritten too.
	var texts []*bs4prettify.Node
	for _, descendant := range soup.Descendants() {
		if descendant.IsTag() {
			continue
		}
		parent := descendant.Parent()
		if parent == nil || parent.TagName() == "script" || parent.TagName() == "style" {
			continue
		}
		if parent.TagName() == "span" && htmlClassContains(parent, "mention") {
			continue
		}
		texts = append(texts, descendant)
	}
	for _, text := range texts {
		original := text.Text()
		replaced, err := substituteAliases(original, context)
		if err != nil {
			return nil, err
		}
		if replaced != original {
			// A replaced Comment, CData section or other string subclass
			// becomes a plain NavigableString in bs4.
			if err := text.ReplaceWithString(replaced); err != nil {
				return nil, err
			}
		}
	}
	return soup, nil
}

// isMentionSpan is soup.find_all('span', {'class': 'mention', 'data-type':
// 'mention'}), including SoupStrainer's attribute matching.
func isMentionSpan(n *bs4prettify.Node) bool {
	if !n.IsTag() || n.TagName() != "span" {
		return false
	}
	return attributeMatches(n, "class", "mention") && attributeMatches(n, "data-type", "mention")
}

// attributeMatches is SoupStrainer._attribute_match: one value of a
// cdata-list attribute or its space-joined form equals the filter string,
// and a plain attribute is compared as a whole.
func attributeMatches(n *bs4prettify.Node, name, match string) bool {
	attr, ok := n.Attr(name)
	if !ok {
		return false
	}
	if !attr.IsList {
		return attr.Value == match
	}
	for _, value := range attr.List {
		if value == match {
			return true
		}
	}
	return len(attr.List) > 1 && strings.Join(attr.List, " ") == match
}

// htmlClassContains is `'mention' in (tag.get('class') or [])`: membership
// in the class list, or a substring test if the attribute were not a list.
func htmlClassContains(n *bs4prettify.Node, class string) bool {
	attr, ok := n.Attr("class")
	if !ok {
		return false
	}
	if !attr.IsList {
		return strings.Contains(attr.Value, class)
	}
	for _, value := range attr.List {
		if value == class {
			return true
		}
	}
	return false
}

// mentionKey is span.get('key', default): the attribute value, or an empty
// string when the span has none.
func mentionKey(span *bs4prettify.Node) (string, error) {
	attr, ok := span.Attr("key")
	if !ok {
		return "", nil
	}
	if attr.IsList {
		return "", errKeyIsList
	}
	return attr.Value, nil
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

// pythonWord is \w in a str pattern: str.isalnum() or '_'. CPython 3.13 uses
// Unicode 15.1.0 and Go's tables are 15.0.0, which lacks the 627 CJK unified
// ideographs 15.1 added at U+2EBF0-U+2EE5D (all of them letters).
func pythonWord(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r) || r >= 0x2ebf0 && r <= 0x2ee5d
}

// foldMatches is re.IGNORECASE's character match for a lower-case ASCII
// pattern letter. Besides the letter's two cases CPython 3.13.16 (Unicode
// 15.1.0) matches U+0130 and U+0131 for 'i' and U+017F for 's'; that is the
// whole set for the letters the aliases use, checked against every code
// point.
func foldMatches(c rune, letter byte) bool {
	if c == rune(letter) || c == rune(letter-'a'+'A') {
		return true
	}
	switch letter {
	case 'i':
		return c == 0x130 || c == 0x131
	case 's':
		return c == 0x17f
	}
	return false
}

// substituteAliases is alias_pattern.sub(...) over one text node:
// (?<![\w@])@(alias...)\b, IGNORECASE, longest alias first.
func substituteAliases(text string, context Context) (string, error) {
	if !strings.Contains(text, "@") {
		return text, nil
	}
	runes := pystr.Runes(text)
	var out strings.Builder
	last := 0
	for i := 0; i < len(runes); i++ {
		if runes[i] != '@' {
			continue
		}
		if i > 0 && (pythonWord(runes[i-1]) || runes[i-1] == '@') {
			continue
		}
		alias := matchAlias(runes[i+1:])
		if alias == "" {
			continue
		}
		group := pystr.String(runes[i+1 : i+1+len(alias)])
		path, ok := LegacyAliases[pystr.Lower(group)]
		if !ok {
			return "", ErrAliasKeyError
		}
		value, err := resolve(path, context)
		if err != nil {
			return "", err
		}
		out.WriteString(pystr.String(runes[last:i]))
		out.WriteString(value)
		i += len(alias)
		last = i + 1
	}
	out.WriteString(pystr.String(runes[last:]))
	return out.String(), nil
}

// matchAlias returns the first alias (longest first) matching at runes[0]
// and followed by a word boundary, or "".
func matchAlias(runes []rune) string {
	for _, alias := range aliasOrder {
		if len(runes) < len(alias) {
			continue
		}
		matches := true
		for i := 0; i < len(alias); i++ {
			if !foldMatches(runes[i], alias[i]) {
				matches = false
				break
			}
		}
		if !matches {
			continue
		}
		if len(runes) > len(alias) && pythonWord(runes[len(alias)]) {
			continue
		}
		return alias
	}
	return ""
}

// FilterBlank is filter_mentions(content, context) of a non-empty content,
// and whether BeautifulSoup(result, "html.parser").get_text().strip() is
// empty.
func FilterBlank(content string, context Context) (string, bool, error) {
	result, err := Filter(content, true, context)
	if err != nil {
		return "", false, err
	}
	soup, err := bs4prettify.Parse(result)
	if err != nil {
		return "", false, err
	}
	// get_text() joins _all_strings with no separator; for a document the
	// types considered are NavigableString and CData only, so comments,
	// doctypes, script and style text do not count.
	var text strings.Builder
	for _, descendant := range soup.Descendants() {
		if descendant.IsTag() {
			continue
		}
		if kind := descendant.Kind(); kind != "NavigableString" && kind != "CData" {
			continue
		}
		text.WriteString(descendant.Text())
	}
	return result, strings.TrimFunc(text.String(), dto.PythonIsSpace) == "", nil
}
