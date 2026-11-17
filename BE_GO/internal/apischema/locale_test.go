package apischema

import (
	"reflect"
	"strings"
	"testing"
)

// The expected keys come from catalog_key() in TESTS/adapters/django/api_schema.py
// (the reference Python with Django 5.2, run against this worktree). A language
// with a path separator walks the catalog directories gettext joins to
// to_locale(language); the port models only the directories that exist there.
// Two samples are not parity cases because the Django host's file system
// decides them:
//
//   - "de-ch/../fr": to_locale gives "de_Ch/../Fr"; a case-insensitive volume
//     finds a directory for "de_Ch" and the catalog "Fr". The port matches names
//     exactly, like the case-sensitive Linux production lookup.
//   - "it/LC_MESSAGES/..": the .mo file exists and Django loads Italian, but
//     catalog_key() names the selection with Path(...).parents[1].name, the ".."
//     component; the port names the resolved catalog (TestCatalogPathNamesTheResolvedCatalog).
func TestCatalogKeyPathSeparatorsMatchDjango(t *testing.T) {
	for language, want := range map[string]string{
		"it/../it":            "1|it,it|it,it|||it,it|it,it|it,it|it,it|it,it",
		"it/":                 "1|it|it|||it|it|it|it|it",
		"it//":                "1|it|it|||it|it|it|it|it",
		"./it":                "1|it|it|||it|it|it|it|it",
		"it/.":                "1|it,it|it,it|||it,it|it,it|it,it|it,it|it,it",
		"xx/../it":            "1|||||||||",
		"../it":               "1|||||||||",
		"/tmp":                "1|||||||||",
		"pt_BR/../de":         "1|de,pt_BR,pt|de,pt_BR|||de,pt_BR,pt|de,pt_BR,pt|de,pt_BR,pt|de,pt_BR,pt|de,pt_BR,pt",
		"en/../it":            "0|it,en||||it,en|it,en|it,en|it,en|it,en",
		"it/../../it":         "1|it|it|||it|it|it|it|it",
		"fr/./":               "1|fr,fr|fr,fr|fr,fr||fr,fr|fr,fr|fr,fr|fr,fr|fr,fr",
		"zh_Hant/../sr@latin": "1|sr,zh_Hant|zh_Hant|||zh_Hant|sr,zh_Hant|sr,zh_Hant|sr,zh_Hant|",
		"xx/yy":               "1|||||||||",
		"handoff-probe":       "1|||||||||",
		// A component that names the layout's own directories selects nothing.
		"it/LC_MESSAGES":       "1|||||||||",
		"it/LC_MESSAGES/.":     "1|||||||||",
		"it/LC_MESSAGES/../it": "1|||||||||",
		"it/..":                "1|it|it|||it|it|it|it|it",
		"it/.hidden":           "1|it|it|||it|it|it|it|it",
		"it/../it/..":          "1|it|it|||it|it|it|it|it",
		"it/../it/":            "1|it,it|it,it|||it,it|it,it|it,it|it,it|it,it",
		"it/./../it":           "1|it,it|it,it|||it,it|it,it|it,it|it,it|it,it",
		"it//../it":            "1|it,it|it,it|||it,it|it,it|it,it|it,it|it,it",
		"it/../xx":             "1|it|it|||it|it|it|it|it",
		"it/../../":            "1|it|it|||it|it|it|it|it",
		"sr@latin/../it":       "1|sr|||||sr|sr|sr|",
		".":                    "1|||||||||",
		"..":                   "1|||||||||",
		"./":                   "1|||||||||",
		"/":                    "1|||||||||",
		// A path in the encoding part is rewritten to a codeset (never a path).
		"it.UTF-8/x":     "1|it|it|||it|it|it|it|it",
		"de./etc/passwd": "1|de|de|de||de|de|de|de|de",
		"xx./tmp":        "1|||||||||",
		"fr./x":          "1|fr|fr|fr||fr|fr|fr|fr|fr",
		"it.utf-8/../it": "1|it|it|||it|it|it|it|it",
	} {
		got, ok := CatalogKey(language)
		if !ok || got != want {
			t.Errorf("CatalogKey(%q) = %q, %v; Django selects %q", language, got, ok, want)
		}
	}
}

// The resolved catalog names are the walk's end, not the expansion spelling:
// Django loads the Italian catalog for "it/LC_MESSAGES/.." too, but names it
// with the last path component of the .mo path ("..").
func TestCatalogPathNamesTheResolvedCatalog(t *testing.T) {
	for language, want := range map[string]string{
		"it/LC_MESSAGES/..":   "1|it|it|||it|it|it|it|it",
		"it/LC_MESSAGES/./..": "1|it|it|||it|it|it|it|it",
	} {
		got, ok := CatalogKey(language)
		if !ok || got != want {
			t.Errorf("CatalogKey(%q) = %q, %v, want %q", language, got, ok, want)
		}
	}
}

// gettext._expand_lang output for the same samples (deduplicated by string
// before resolving, as find does), from the reference Python.
func TestExpandLanguageKeepsPathSeparators(t *testing.T) {
	for language, want := range map[string][]string{
		"it/../it":            {"it/../it", "it/"},
		"it/":                 {"it/"},
		"it//":                {"it//"},
		"./it":                {"./it", ""},
		"it/.":                {"it/.", "it/"},
		"xx/../it":            {"xx/../it", "xx/"},
		"../it":               {"../it", ""},
		"/tmp":                {"/tmp"},
		"de-ch/../fr":         {"de_Ch/../Fr", "de_Ch/", "de../Fr", "de"},
		"pt_BR/../de":         {"pt_BR/../de", "pt_BR/", "pt../de", "pt"},
		"zh_Hant/../sr@latin": {"zh_Hant/../sr@latin", "zh_Hant/@latin", "zh../sr@latin", "zh@latin", "zh_Hant/../sr", "zh_Hant/", "zh../sr", "zh"},
		"it/LC_MESSAGES/..":   {"it/LC_MESSAGES/..", "it/LC_MESSAGES/", "it/LC..", "it/LC"},
		"handoff-probe":       {"handoff_Probe", "handoff"},
		"xx/yy":               {"xx/yy"},
		"/":                   {"/"},
		".":                   {".", ""},
		"..":                  {"..", ""},
	} {
		got := expandLanguage(normalizeLocale(toLocale(language), loaded().LocaleAlias))
		if !reflect.DeepEqual(got, want) {
			t.Errorf("expansions(%q) = %q, want %q", language, got, want)
		}
	}
}

// A rewritten encoding never contains a path separator: locale._replace_encoding
// normalizes it to alphanumerics and a codeset, so normalizeLocale's placeholder
// cannot move the walk (the reference Python shows the same expansions).
func TestNormalizeLocaleEncodingPlaceholder(t *testing.T) {
	alias := loaded().LocaleAlias
	for _, language := range []string{"it.UTF-8/x", "de./etc/passwd", "it.utf-8/../it", "fr./x"} {
		locale := normalizeLocale(toLocale(language), alias)
		if strings.Contains(locale, "/") {
			t.Errorf("normalize(%q) = %q keeps a path separator", language, locale)
		}
		if !strings.Contains(locale, "codeset") {
			t.Errorf("normalize(%q) = %q does not use the codeset placeholder", language, locale)
		}
	}
}

// A resolved key the generator never rendered maps to the first selected
// catalog's key: Django loads that catalog first in each directory and chains
// the rest as fallbacks, so it is the generated document closest to the answer.
// A key without a selection keeps its fallback flag and drops the catalogs:
// Django then renders the chain alone, the document the generator stores for
// the bare key.
func TestDocumentKeyUsesTheFirstSelection(t *testing.T) {
	for key, want := range map[string]string{
		"1|it,it|it,it|||it,it|it,it|it,it|it,it|it,it":         mustCatalogKey(t, "it"),
		"0|it,en||||it,en|it,en|it,en|it,en|it,en":              mustCatalogKey(t, "it"),
		"1|de,pt_BR,pt|de,pt_BR|||de,pt_BR,pt|de,pt_BR,pt":      mustCatalogKey(t, "de"),
		"1|sr,zh_Hant|zh_Hant|||zh_Hant|sr,zh_Hant|sr,zh_Hant|": mustCatalogKey(t, "sr"),
		// No catalog selected, flag kept ("en/xx" and "xx/yy" select nothing).
		"0|||||||||": "0|||||||||",
		"1|||||||||": "1|||||||||",
		"1|":         "1|||||||||",
		"0|":         "0|||||||||",
	} {
		got, ok := DocumentKey(key)
		if !ok || got != want {
			t.Errorf("DocumentKey(%q) = %q, %v, want %q", key, got, ok, want)
		}
	}
	unknown := mustCatalogKey(t, "xx")
	if got, ok := DocumentKey(unknown); !ok || got != unknown {
		t.Errorf("DocumentKey(%q) = %q, %v, want itself", unknown, got, ok)
	}
	// Every catalog name is one of the generator's candidate languages, so only a
	// key with a flag the generator never rendered has no document at all.
	if _, ok := DocumentKey("2|"); ok {
		t.Errorf("DocumentKey found a document for an unknown fallback flag")
	}
}

// The generator always renders the bare selection ("xx" and "enx" are
// candidates), so the fallback only matters if a future table drops it: the
// document Django renders for the chain alone is still a generated key, the
// default language's with a fallback flag and the English (msgid) document
// without one.
func TestDocumentKeyAnswersTheChainWithoutTheBareKey(t *testing.T) {
	original := loaded
	defer func() { loaded = original }()
	real := original()
	trimmed := *real
	trimmed.VariantOf = make(map[string]int, len(real.VariantOf))
	for key, variant := range real.VariantOf {
		if key == "0|||||||||" || key == "1|||||||||" {
			continue
		}
		trimmed.VariantOf[key] = variant
	}
	loaded = func() *data { return &trimmed }
	for key, want := range map[string]string{
		"0|":         mustCatalogKey(t, "en"),
		"0|||||||||": mustCatalogKey(t, "en"),
		"1|":         mustCatalogKey(t, "it"),
		"1|||||||||": mustCatalogKey(t, "it"),
	} {
		got, ok := DocumentKey(key)
		if !ok || got != want {
			t.Errorf("DocumentKey(%q) = %q, %v, want %q", key, got, ok, want)
		}
	}
}

func mustCatalogKey(t *testing.T, language string) string {
	t.Helper()
	key, ok := CatalogKey(language)
	if !ok {
		t.Fatalf("CatalogKey(%q) is refused", language)
	}
	return key
}
