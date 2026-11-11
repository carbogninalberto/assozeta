// Package pymime reproduces Python's module-level mimetypes.guess_type(name)
// with strict=True, as Django views call it: the built-in table, then every
// existing file of mimetypes.knownfiles read in order (later entries override
// earlier ones), plus the suffix and encoding maps. Like Python, the files are
// read once, at the first guess of the process.
package pymime

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
	"unicode"

	"golang.org/x/text/unicode/norm"
)

// ErrURLName is kept for callers that refuse a name read as a URL; guessing
// now follows Python's URL branch and reports a refused name as a *ValueError.
var ErrURLName = errors.New("name parses as a URL")

// ValueError is a ValueError that urllib.parse raised for a name: its text is
// what the caller's exception handler prints.
type ValueError struct{ Text string }

func (e *ValueError) Error() string { return e.Text }

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
	kind, ok, _, err := t.GuessType(name)
	return kind, ok, err
}

// GuessType is mimetypes.guess_type(name) with the process-wide table: the
// type (ok false for None) and the encoding ("" for None).
func GuessType(name string) (string, bool, string, error) {
	once.Do(func() { process, loadErr = Load(KnownFiles) })
	if loadErr != nil {
		return "", false, "", loadErr
	}
	return process.GuessType(name)
}

// GuessType is MimeTypes.guess_type(name, strict=True) for a path-like name:
// the type (ok false for None) and the case-sensitive encoding ("" for None).
func (t *Table) GuessType(name string) (string, bool, string, error) {
	if urlScheme(name) {
		scheme, path, err := urlPath(name)
		if err != nil {
			return "", false, "", err
		}
		if scheme == "data" {
			kind, ok, err := dataType(path)
			return kind, ok, "", err
		}
		name = path
	}
	base, ext := splitext(name)
	for {
		mapped, ok := suffixes[strings.ToLower(ext)]
		if !ok {
			break
		}
		base, ext = splitext(base + mapped)
	}
	encoding, encoded := encodings[ext]
	if encoded {
		base, ext = splitext(base)
	}
	kind, ok := t.types[strings.ToLower(ext)]
	return kind, ok, encoding, nil
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

// usesParams is urllib.parse.uses_params.
var usesParams = map[string]bool{"ftp": true, "hdl": true, "prospero": true, "http": true, "imap": true, "https": true, "shttp": true,
	"rtsp": true, "rtsps": true, "rtspu": true, "sip": true, "sips": true, "mms": true, "sftp": true, "tel": true}

// urlPath is urllib.parse.urlparse(name) for a name urlScheme accepts: the
// lower-cased scheme and the path (after the network location, before the
// fragment, the query and, for the schemes that take them, the parameters of
// the last segment). Network locations urlsplit validates further (brackets,
// non-ASCII text) are not modelled: ErrURLName.
func urlPath(name string) (string, string, error) {
	name = strings.TrimLeftFunc(name, func(r rune) bool { return r <= ' ' })
	name = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(name)
	colon := strings.IndexByte(name, ':')
	scheme, rest := strings.ToLower(name[:colon]), name[colon+1:]
	if strings.HasPrefix(rest, "//") {
		end := len(rest)
		if at := strings.IndexAny(rest[2:], "/?#"); at >= 0 {
			end = at + 2
		}
		if err := checkNetloc(rest[2:end]); err != nil {
			return "", "", err
		}
		rest = rest[end:]
	}
	if at := strings.IndexByte(rest, '#'); at >= 0 {
		rest = rest[:at]
	}
	if at := strings.IndexByte(rest, '?'); at >= 0 {
		rest = rest[:at]
	}
	if usesParams[scheme] {
		from := strings.LastIndexByte(rest, '/')
		if from < 0 {
			from = 0
		}
		if at := strings.IndexByte(rest[from:], ';'); at >= 0 {
			rest = rest[:from+at]
		}
	}
	return scheme, rest, nil
}

// dataType is guess_type's answer for a data: URL: the text before the first
// ';' or ',' of the path, or text/plain when that text has an '=' or no '/';
// None without a comma.
func dataType(path string) (string, bool, error) {
	comma := strings.IndexByte(path, ',')
	if comma < 0 {
		return "", false, nil
	}
	kind := path[:comma]
	if semi := strings.IndexByte(kind, ';'); semi >= 0 {
		kind = kind[:semi]
	}
	if strings.Contains(kind, "=") || !strings.Contains(kind, "/") {
		return "text/plain", true, nil
	}
	return kind, true, nil
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

// checkNetloc is the validation urlsplit runs on the network location: the
// bracket checks first, then the NFKC check.
func checkNetloc(netloc string) error {
	opens, closes := strings.Contains(netloc, "["), strings.Contains(netloc, "]")
	if opens != closes {
		return &ValueError{"Invalid IPv6 URL"}
	}
	if opens {
		hostPort := netloc[strings.LastIndexByte(netloc, '@')+1:]
		var host string
		if before, bracketed, found := strings.Cut(hostPort, "["); found {
			if before != "" {
				return &ValueError{"Invalid IPv6 URL"}
			}
			var port string
			host, port, _ = strings.Cut(bracketed, "]")
			if port != "" && !strings.HasPrefix(port, ":") {
				return &ValueError{"Invalid IPv6 URL"}
			}
		} else {
			host, _, _ = strings.Cut(hostPort, ":")
		}
		if err := checkBracketedHost(host); err != nil {
			return err
		}
	}
	return checkNFKC(netloc)
}

func checkBracketedHost(host string) error {
	if strings.HasPrefix(host, "v") || strings.HasPrefix(host, "V") {
		dot := strings.IndexByte(host, '.')
		valid := dot > 1 && len(host) > dot+1 && !strings.ContainsRune(host[dot+1:], '\n')
		for i := 1; valid && i < dot; i++ {
			c := host[i]
			valid = c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F'
		}
		if !valid {
			return &ValueError{"IPvFuture address is invalid"}
		}
		return nil
	}
	if ipv4(host) {
		return &ValueError{"An IPv4 address cannot be in brackets"}
	}
	if !ipv6(host) {
		return &ValueError{pythonRepr(host) + " does not appear to be an IPv4 or IPv6 address"}
	}
	return nil
}

// checkNFKC is _checknetloc.
func checkNFKC(netloc string) error {
	ascii := true
	for i := 0; i < len(netloc); i++ {
		if netloc[i] > 0x7f {
			ascii = false
		}
	}
	if ascii {
		return nil
	}
	n := strings.NewReplacer("@", "", ":", "", "#", "", "?", "").Replace(netloc)
	normalized := norm.NFKC.String(n)
	if n == normalized {
		return nil
	}
	if strings.ContainsAny(normalized, "/?#@:") {
		return &ValueError{"netloc '" + netloc + "' contains invalid characters under NFKC normalization"}
	}
	return nil
}

// ipv4 is ipaddress.IPv4Address(text) succeeding: four decimal octets of up
// to three ASCII digits, no leading zeros, at most 255.
func ipv4(text string) bool {
	parts := strings.Split(text, ".")
	if len(parts) != 4 {
		return false
	}
	for _, part := range parts {
		if part == "" || len(part) > 3 || (len(part) > 1 && part[0] == '0') {
			return false
		}
		value := 0
		for i := 0; i < len(part); i++ {
			if part[i] < '0' || part[i] > '9' {
				return false
			}
			value = value*10 + int(part[i]-'0')
		}
		if value > 255 {
			return false
		}
	}
	return true
}

// ipv6 is ipaddress.IPv6Address(text) succeeding (a scope id after '%' is
// allowed when it is not empty and holds no second '%').
func ipv6(text string) bool {
	if addr, scope, found := strings.Cut(text, "%"); found {
		if scope == "" || strings.Contains(scope, "%") {
			return false
		}
		text = addr
	}
	parts := strings.Split(text, ":")
	if len(parts) < 3 {
		return false
	}
	if last := parts[len(parts)-1]; strings.Contains(last, ".") {
		if !ipv4(last) {
			return false
		}
		parts = append(parts[:len(parts)-1], "0", "0")
	}
	if len(parts) > 9 {
		return false
	}
	skip := -1
	for i := 1; i < len(parts)-1; i++ {
		if parts[i] == "" {
			if skip >= 0 {
				return false
			}
			skip = i
		}
	}
	hi, lo := len(parts), 0
	if skip >= 0 {
		hi, lo = skip, len(parts)-skip-1
		if parts[0] == "" {
			if hi--; hi != 0 {
				return false
			}
		}
		if parts[len(parts)-1] == "" {
			if lo--; lo != 0 {
				return false
			}
		}
		if 8-(hi+lo) < 1 {
			return false
		}
	} else if len(parts) != 8 || parts[0] == "" || parts[len(parts)-1] == "" {
		return false
	}
	hextets := append(append([]string{}, parts[:hi]...), parts[len(parts)-lo:]...)
	for _, part := range hextets {
		if part == "" || len(part) > 4 {
			return false
		}
		for j := 0; j < len(part); j++ {
			c := part[j]
			if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F') {
				return false
			}
		}
	}
	return true
}

// pythonRepr is repr(text) for a str.
func pythonRepr(text string) string {
	quote := byte('\'')
	if strings.Contains(text, "'") && !strings.Contains(text, `"`) {
		quote = '"'
	}
	var out strings.Builder
	out.WriteByte(quote)
	for _, r := range text {
		switch {
		case r == rune(quote) || r == '\\':
			out.WriteByte('\\')
			out.WriteRune(r)
		case r == '\n':
			out.WriteString(`\n`)
		case r == '\r':
			out.WriteString(`\r`)
		case r == '\t':
			out.WriteString(`\t`)
		case r < 0x20 || r == 0x7f:
			out.WriteString(fmt.Sprintf(`\x%02x`, r))
		case r < 0x7f || unicode.IsPrint(r):
			out.WriteRune(r)
		case r <= 0xff:
			out.WriteString(fmt.Sprintf(`\x%02x`, r))
		case r <= 0xffff:
			out.WriteString(fmt.Sprintf(`\u%04x`, r))
		default:
			out.WriteString(fmt.Sprintf(`\U%08x`, r))
		}
	}
	out.WriteByte(quote)
	return out.String()
}
