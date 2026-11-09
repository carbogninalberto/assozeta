// Package pymime reproduces Python's module-level mimetypes.guess_type(name)
// with strict=True, as Django views call it: the built-in table, then every
// existing file of mimetypes.knownfiles read in order (later entries override
// earlier ones), plus the suffix and encoding maps. Like Python, the files are
// read once, at the first guess of the process.
package pymime

import (
	"bufio"
	"errors"
	"io"
	"os"
	"strings"
	"sync"
	"unicode"
)

// ErrURLName marks a name urllib.parse.urlparse reads as a URL with a scheme of
// two or more characters: Python guesses from its URL path (and parses data:
// URLs); callers refuse such names instead of approximating that branch.
var ErrURLName = errors.New("name parses as a URL")

// KnownFiles is mimetypes.knownfiles of Python 3.13.
var KnownFiles = []string{
	"/etc/mime.types", "/etc/httpd/mime.types", "/etc/httpd/conf/mime.types", "/etc/apache/mime.types",
	"/etc/apache2/mime.types", "/usr/local/etc/httpd/conf/mime.types", "/usr/local/lib/netscape/mime.types",
	"/usr/local/etc/httpd/conf/mime.types", "/usr/local/etc/mime.types",
}

var encodings = map[string]string{".gz": "gzip", ".Z": "compress", ".bz2": "bzip2", ".xz": "xz", ".br": "br"}
var suffixes = map[string]string{".svgz": ".svg.gz", ".tgz": ".tar.gz", ".taz": ".tar.gz", ".tz": ".tar.gz",
	".tbz2": ".tar.bz2", ".txz": ".tar.xz"}

// Table is one initialized mimetypes database.
type Table struct{ types map[string]string }

// Load builds the table from the defaults and the given mime.types files;
// missing paths and non-regular files are skipped (os.path.isfile).
func Load(files []string) (*Table, error) {
	types := make(map[string]string, len(defaultTypes))
	for ext, kind := range defaultTypes {
		types[ext] = kind
	}
	for _, name := range files {
		info, err := os.Stat(name)
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		if err := read(types, name); err != nil {
			return nil, err
		}
	}
	return &Table{types: types}, nil
}

// read is MimeTypes.readfp: universal newlines, whitespace-separated words,
// a word starting with '#' ends the line, the first word is the type.
func read(types map[string]string, name string) error {
	file, err := os.Open(name)
	if err != nil {
		return err
	}
	defer file.Close()
	content, err := io.ReadAll(bufio.NewReader(file))
	if err != nil {
		return err
	}
	text := strings.ReplaceAll(strings.ReplaceAll(string(content), "\r\n", "\n"), "\r", "\n")
	for _, line := range strings.Split(text, "\n") {
		words := strings.FieldsFunc(line, pythonSpace)
		for i, word := range words {
			if strings.HasPrefix(word, "#") {
				words = words[:i]
				break
			}
		}
		if len(words) < 2 {
			continue
		}
		for _, suffix := range words[1:] {
			types["."+suffix] = words[0]
		}
	}
	return nil
}

// pythonSpace is str.isspace for str.split().
func pythonSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }

var (
	once    sync.Once
	process *Table
	loadErr error
)

// Guess is mimetypes.guess_type(name)[0] with the process-wide table.
func Guess(name string) (string, bool, error) {
	once.Do(func() { process, loadErr = Load(KnownFiles) })
	if loadErr != nil {
		return "", false, loadErr
	}
	return process.Guess(name)
}

// Guess is MimeTypes.guess_type(name, strict=True)[0] for a path-like name.
func (t *Table) Guess(name string) (string, bool, error) {
	if urlScheme(name) {
		return "", false, ErrURLName
	}
	base, ext := splitext(name)
	for {
		mapped, ok := suffixes[strings.ToLower(ext)]
		if !ok {
			break
		}
		base, ext = splitext(base + mapped)
	}
	if _, ok := encodings[ext]; ok {
		base, ext = splitext(base)
	}
	kind, ok := t.types[strings.ToLower(ext)]
	return kind, ok, nil
}

// urlScheme follows urllib.parse.urlsplit: leading C0/space stripped, tab and
// newlines removed, then an ASCII letter followed by scheme characters up to
// the first ':'. guess_type uses the URL branch only for schemes longer than 1.
func urlScheme(name string) bool {
	name = strings.TrimLeftFunc(name, func(r rune) bool { return r <= ' ' })
	name = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(name)
	colon := strings.IndexByte(name, ':')
	if colon <= 1 {
		return false
	}
	first := name[0]
	if !(first >= 'a' && first <= 'z' || first >= 'A' && first <= 'Z') {
		return false
	}
	for _, c := range name[:colon] {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '+' || c == '-' || c == '.') {
			return false
		}
	}
	return true
}

// splitext is posixpath.splitext: leading dots of the last component do not
// start an extension.
func splitext(p string) (string, string) {
	sep := strings.LastIndexByte(p, '/')
	dot := strings.LastIndexByte(p, '.')
	if dot > sep {
		for i := sep + 1; i < dot; i++ {
			if p[i] != '.' {
				return p[:dot], p[dot:]
			}
		}
	}
	return p, ""
}
