// Package bs4prettify reproduces BeautifulSoup(markup, features="html.parser").prettify()
// of beautifulsoup4 4.13.3 on CPython 3.13.16.
//
// Parsing is the html.parser port in htmlparser.go, driven the way bs4 drives
// it (builder/_htmlparser.py) into the tree BeautifulSoup builds (__init__.py,
// builder/__init__.py, element.py). Rendering is Tag.decode(indent_level=0,
// formatter="minimal") with eventual_encoding="utf-8", also iteratively.
//
// Python strings may hold lone surrogates; this package converts with
// pystr.Runes on the way in and pystr.String on the way out.
package bs4prettify

import (
	"errors"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrUnsupported refuses markup only when prettify() would produce more than
// maxOutputBytes of output.
var ErrUnsupported = refusal.New("prettify output exceeds the supported size")

// ErrPythonRaises reports that bs4 itself raises on this markup: the integer
// digit limit or a surrogate in the constructor's pre-check (ValueError /
// UnicodeEncodeError), or an AssertionError from html.parser
// (ParserRejectedMarkup). Callers answer the 500 page.
var ErrPythonRaises = errors.New("beautifulsoup raises on this markup")

// Prettify returns BeautifulSoup(markup, "html.parser").prettify().
func Prettify(markup string) (string, error) {
	root, err := buildTree(markup)
	if err != nil {
		return "", err
	}
	return render(root)
}

// isPythonSpace is str.isspace() (also re's \s for str patterns).
func isPythonSpace(r rune) bool { return pystr.IsSpace(r) }

func set(words string) map[string]bool {
	result := map[string]bool{}
	for _, word := range strings.Fields(words) {
		result[word] = true
	}
	return result
}
