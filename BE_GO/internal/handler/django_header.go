package handler

import (
	"bytes"
	"errors"
	"io"
	"net/textproto"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyheader"
)

type djangoHeaderOutcome = pyheader.Outcome

const (
	headerParsed  = pyheader.Parsed
	headerInvalid = pyheader.Invalid
)

func pyIsSpace(r rune) bool { return pyheader.IsSpace(r) }

func djangoParseHeader(line string) (string, map[string]string, djangoHeaderOutcome) {
	main, params, outcome := pyheader.Parse(line)
	if outcome != pyheader.Parsed {
		return main, nil, outcome
	}
	result := make(map[string]string, len(params))
	for _, p := range params {
		result[p.Name] = p.Value
	}
	return main, result, outcome
}

type djangoPart struct {
	disposition    map[string]string
	hasDisposition bool
	base64         bool
	// contentType is meta_data.get('content-type', ('', {}))[0].strip(): the
	// lower-cased main value of the last valid content-type line.
	contentType string
}

// djangoPartHeaders is the header loop of parse_boundary_stream for one part:
// each line is decoded as UTF-8 and parsed, a line that raises a ValueError is
// skipped, and the last line of a name wins.
func djangoPartHeaders(header textproto.MIMEHeader) djangoPart {
	var part djangoPart
	for key, values := range header {
		lower := strings.ToLower(key)
		if lower != "content-disposition" && lower != "content-transfer-encoding" && lower != "content-type" {
			continue
		}
		for _, value := range values {
			line := key + ": " + value
			if !utf8.ValidString(line) {
				continue
			}
			main, params, outcome := djangoParseHeader(line)
			name, rest, found := strings.Cut(main, ":")
			if outcome != headerParsed || !found || headerParamsHoldSurrogate(params) {
				// {k: v.encode() for ...} raises UnicodeEncodeError (a
				// ValueError) for a decoded value holding a lone surrogate,
				// which utf-7 and the escape codecs can produce: the line is
				// skipped like any invalid header.
				continue
			}
			switch name {
			case "content-disposition":
				part.disposition, part.hasDisposition = params, true
			case "content-transfer-encoding":
				part.base64 = strings.TrimFunc(rest, pyIsSpace) == "base64"
			case "content-type":
				part.contentType = strings.TrimFunc(rest, pyIsSpace)
			}
		}
	}
	return part
}

// headerParamsHoldSurrogate reports a parameter value holding a lone
// surrogate (pyheader writes them as their 3-byte WTF-8 form).
func headerParamsHoldSurrogate(params map[string]string) bool {
	for _, value := range params {
		for i := 0; i+2 < len(value); i++ {
			if value[i] == 0xED && value[i+1] >= 0xA0 && value[i+1] <= 0xBF {
				return true
			}
		}
	}
	return false
}

// djangoHeaderValue is the main value of the last line of a request header that
// parse_header_parameters accepts (parse_boundary_stream's outdict[name][0]):
// every line is decoded as UTF-8, a line whose parse raises a ValueError is
// skipped, and the main value is the text after its first ':'. ok is false when
// no line parses.
func djangoHeaderValue(header textproto.MIMEHeader, name string) (string, bool) {
	value, found := "", false
	for _, item := range header.Values(name) {
		line := name + ": " + item
		if !utf8.ValidString(line) {
			continue
		}
		main, _, outcome := djangoParseHeader(line)
		if outcome == headerInvalid {
			continue
		}
		_, rest, hasValue := strings.Cut(main, ":")
		if !hasValue {
			continue
		}
		value, found = strings.TrimFunc(rest, pyIsSpace), true
	}
	return value, found
}

func stripASCIISpace(data []byte) []byte {
	out := make([]byte, 0, len(data))
	for _, b := range data {
		if b != ' ' && b != '\t' && b != '\n' && b != '\r' && b != '\v' && b != '\f' {
			out = append(out, b)
		}
	}
	return out
}

var errPyBase64 = errors.New("binascii.Error")

// pyBase64Decode is base64.b64decode(data) (binascii.a2b_base64, not strict):
// characters outside the alphabet are skipped, a padding sequence ends the
// input and an incomplete group is an error.
func pyBase64Decode(data []byte) ([]byte, error) {
	return io.ReadAll(&pyBase64Reader{source: bytes.NewReader(data)})
}

// pyBase64Reader decodes in place so multipart ZIPs need only a copy buffer.
type pyBase64Reader struct {
	source           io.Reader
	quad, pads, left int
}

func (r *pyBase64Reader) Read(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	for {
		n, err := r.source.Read(p)
		out := p[:0]
		for _, c := range p[:n] {
			if c == '=' {
				if r.quad >= 2 {
					r.pads++
				}
				continue
			}
			value := strings.IndexByte("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", c)
			if value < 0 {
				continue
			}
			r.pads = 0
			switch r.quad {
			case 0:
				r.quad, r.left = 1, value
			case 1:
				out = append(out, byte(r.left<<2|value>>4))
				r.quad, r.left = 2, value&0xf
			case 2:
				out = append(out, byte(r.left<<4|value>>2))
				r.quad, r.left = 3, value&0x3
			case 3:
				out = append(out, byte(r.left<<6|value))
				r.quad, r.left = 0, 0
			}
		}
		if err == io.EOF && r.quad != 0 && !(r.quad >= 2 && r.quad+r.pads >= 4) {
			err = errPyBase64
		}
		if len(out) > 0 || err != nil {
			return len(out), err
		}
	}
}
