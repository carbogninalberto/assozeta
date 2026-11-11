package handler

import (
	"errors"
	"net/textproto"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyheader"
)

type djangoHeaderOutcome = pyheader.Outcome

const (
	headerParsed    = pyheader.Parsed
	headerInvalid   = pyheader.Invalid
	headerNotPorted = pyheader.NotPorted
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
}

// djangoPartHeaders is the header loop of parse_boundary_stream for one part:
// each line is decoded as UTF-8 and parsed, a line that raises a ValueError is
// skipped, and the last line of a name wins.
func djangoPartHeaders(header textproto.MIMEHeader) (djangoPart, djangoHeaderOutcome) {
	var part djangoPart
	for key, values := range header {
		lower := strings.ToLower(key)
		if lower != "content-disposition" && lower != "content-transfer-encoding" {
			continue
		}
		for _, value := range values {
			line := key + ": " + value
			if !utf8.ValidString(line) {
				continue
			}
			main, params, outcome := djangoParseHeader(line)
			if outcome == headerNotPorted {
				return part, outcome
			}
			name, rest, found := strings.Cut(main, ":")
			if outcome != headerParsed || !found {
				continue
			}
			switch name {
			case "content-disposition":
				part.disposition, part.hasDisposition = params, true
			case "content-transfer-encoding":
				part.base64 = strings.TrimFunc(rest, pyIsSpace) == "base64"
			}
		}
	}
	return part, headerParsed
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
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	var table [256]int8
	for i := range table {
		table[i] = -1
	}
	for i := 0; i < len(alphabet); i++ {
		table[alphabet[i]] = int8(i)
	}
	out := make([]byte, 0, len(data)/4*3)
	quad, pads, left := 0, 0, 0
	for _, c := range data {
		if c == '=' {
			// pads only counts once the group holds two characters.
			if quad >= 2 {
				pads++
			}
			continue
		}
		value := table[c]
		if value < 0 {
			continue
		}
		pads = 0
		switch quad {
		case 0:
			quad, left = 1, int(value)
		case 1:
			out = append(out, byte(left<<2|int(value)>>4))
			quad, left = 2, int(value)&0xf
		case 2:
			out = append(out, byte(left<<4|int(value)>>2))
			quad, left = 3, int(value)&0x3
		case 3:
			out = append(out, byte(left<<6|int(value)))
			quad, left = 0, 0
		}
	}
	if quad != 0 && !(quad >= 2 && quad+pads >= 4) {
		return nil, errPyBase64
	}
	return out, nil
}

// djangoHeaderParameters is the parameter map of a Content-Disposition value;
// ok is false for a line parse_header_parameters rejects or this port does not
// decode.
func djangoHeaderParameters(line string) (map[string]string, bool) {
	_, params, outcome := djangoParseHeader("content-disposition: " + line)
	return params, outcome == headerParsed
}
