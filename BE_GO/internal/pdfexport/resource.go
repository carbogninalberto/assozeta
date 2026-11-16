package pdfexport

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
)

// An image of a paragraph whose src is not a data: URL is read by reportlab's
// ImageReader with open_for_read (lib/utils.py): reportlab opens the name as a
// path, and when that fails it reads a data: URL or fetches the name as a URL
// with urllib's urlopen. rl_settings trusts every host and every scheme by
// default (trustedHosts None), so open_for_read restricts nothing.
//
// Go reads such a source through Resource, so the tests can serve it
// themselves.

// ResourceOpener reads the bytes of an inline image source. An error means
// reportlab cannot open the resource: ImageReader raises
// IOError('Cannot open resource "%s"'), which is the export's failure.
// ErrNotPorted reports a source reportlab may open but this package does not
// read (an ftp: URL).
type ResourceOpener interface {
	// Open returns the bytes reportlab reads for name; ctx bounds the fetch of
	// a URL.
	Open(ctx context.Context, name string) ([]byte, error)
}

// Resource is the opener of the paragraph parser: reportlab opens the path or
// fetches the URL. A caller replaces it to answer the sources itself.
var Resource ResourceOpener = DefaultResourceOpener{}

// DefaultResourceOpener opens a source as reportlab's open_for_read does: the
// name as a path (os.ReadFile, relative to the process's working directory, as
// reportlab opens it relative to Django's), else a data: URL, else a URL.
type DefaultResourceOpener struct{}

func (DefaultResourceOpener) Open(ctx context.Context, name string) ([]byte, error) {
	if data, err := os.ReadFile(name); err == nil {
		return data, nil
	}
	if len(name) >= 5 && strings.EqualFold(name[:5], "data:") {
		return readDataURL(name)
	}
	return readURL(ctx, name)
}

// readDataURL is datareader: the bytes of a data: URL. The media type is not
// used; the data after the first comma is base64 when the media type ends with
// ';base64', and percent-decoded latin-1 text otherwise.
func readDataURL(name string) ([]byte, error) {
	typ, data, found := strings.Cut(name, ",")
	if !found {
		return nil, errors.New("datareader: bad data URL") // IOError('data error', 'bad data URL')
	}
	if semi := strings.LastIndex(typ, ";"); semi >= 0 && !strings.Contains(typ[semi:], "=") && typ[semi+1:] == "base64" {
		if !asciiOnly(data) {
			return nil, errors.New("datareader: data is not ASCII") // data.encode('ascii')
		}
		raw, ok := pythonB64Decode(data)
		if !ok {
			return nil, errors.New("datareader: data is not base64") // binascii.Error
		}
		return raw, nil
	}
	return latin1Bytes(data)
}

// latin1Bytes is unquote(data).encode('latin-1'): urllib decodes the percent
// escapes as UTF-8 text (an invalid byte as U+FFFD), and every character of
// that text must be latin-1 for a byte string to exist.
func latin1Bytes(data string) ([]byte, error) {
	text := strings.ToValidUTF8(string(unquoteToBytes(data)), "\uFFFD")
	raw := make([]byte, 0, len(text))
	for _, r := range text {
		if r > 0xFF {
			return nil, errors.New("datareader: data is not latin-1")
		}
		raw = append(raw, byte(r))
	}
	return raw, nil
}

// unquoteToBytes is urllib.parse.unquote_to_bytes of a str: the UTF-8 bytes of
// the text with every %XX of two hexadecimal digits replaced by its byte, and
// an escape that is not one left as it is.
func unquoteToBytes(text string) []byte {
	source := []byte(text)
	raw := make([]byte, 0, len(source))
	for i := 0; i < len(source); i++ {
		if source[i] == '%' && i+2 < len(source) {
			hi, hiHex := unhexDigit(source[i+1])
			lo, loHex := unhexDigit(source[i+2])
			if hiHex && loHex {
				raw = append(raw, hi<<4|lo)
				i += 2
				continue
			}
		}
		raw = append(raw, source[i])
	}
	return raw
}

func unhexDigit(b byte) (byte, bool) {
	switch {
	case b >= '0' && b <= '9':
		return b - '0', true
	case b >= 'a' && b <= 'f':
		return b - 'a' + 10, true
	case b >= 'A' && b <= 'F':
		return b - 'A' + 10, true
	}
	return 0, false
}

// resourceClient fetches a URL source: Go's default policy follows ten
// redirects, as urlopen does, and the request carries no timeout, as urlopen's
// has none.
var resourceClient = &http.Client{}

// readURL is rlUrlRead: urlopen(name).read(), the bytes of the response or the
// failure of the request.
func readURL(ctx context.Context, name string) ([]byte, error) {
	reference, err := url.Parse(name)
	if err != nil {
		return nil, err
	}
	switch reference.Scheme {
	case "http", "https":
		return fetchURL(ctx, name)
	case "file":
		return readFileURL(reference)
	case "ftp":
		// reportlab downloads an ftp: URL with urlopen.
		return nil, fmt.Errorf("%w: an ftp: URL", ErrNotPorted)
	}
	return nil, fmt.Errorf("unknown url type %q", reference.Scheme)
}

// fetchURL is the read of an http: or https: URL: the body of a 2xx response,
// or the failure of the request.
func fetchURL(ctx context.Context, name string) ([]byte, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, name, nil)
	if err != nil {
		return nil, err
	}
	response, err := resourceClient.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode > 299 {
		return nil, fmt.Errorf("HTTP %d", response.StatusCode)
	}
	return io.ReadAll(response.Body)
}

// readFileURL is urllib's local file handler: the path of a file: URL with its
// percent escapes decoded, read only on the local host.
func readFileURL(reference *url.URL) ([]byte, error) {
	if reference.Host != "" && reference.Host != "localhost" {
		return nil, errors.New("file: URL on a remote host") // URLError: only on localhost
	}
	selector := reference.EscapedPath()
	if selector == "" {
		selector = reference.Opaque
	}
	if reference.RawQuery != "" || reference.ForceQuery {
		selector += "?" + reference.RawQuery
	}
	path := strings.ToValidUTF8(string(unquoteToBytes(selector)), "\uFFFD")
	return os.ReadFile(path)
}
