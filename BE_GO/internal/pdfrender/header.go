package pdfrender

import (
	"encoding/base64"
	"fmt"
	"strings"
)

// HeaderSafe reports whether Django's ResponseHeaders and the ASGI server
// accept text as a header value the way HeaderBytes models it: no line
// breaks (BadHeaderError), no other ASCII control character but tab, and none
// of the separators str.splitlines() honours (they would split
// email.header.Header's MIME words).
func HeaderSafe(text string) bool {
	for _, r := range text {
		switch {
		case r == '\t':
		case r < 0x20 || r == 0x7f:
			return false
		case r == 0x85 || r == 0x2028 || r == 0x2029:
			return false
		}
	}
	return true
}

// HeaderBytes is the header value Django writes for text: its ISO-8859-1
// bytes when every code point fits, otherwise
// email.header.Header(text, 'utf-8', maxlinelen=sys.maxsize).encode(), one
// RFC 2047 word in the shorter of base64 and quoted-printable (ties choose
// quoted-printable). The result is a Go string holding the raw bytes.
func HeaderBytes(text string) string {
	latin := make([]byte, 0, len(text))
	for _, r := range text {
		if r > 0xff {
			return mimeWord([]byte(text))
		}
		latin = append(latin, byte(r))
	}
	return string(latin)
}

// headerSafe are quoprimime's header characters kept as they are.
const headerSafe = "-!*+/abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

func mimeWord(raw []byte) string {
	if len(raw) == 0 {
		return ""
	}
	base64Length := len(raw) / 3 * 4
	if len(raw)%3 != 0 {
		base64Length += 4
	}
	var quoted strings.Builder
	for _, octet := range raw {
		switch {
		case octet == ' ':
			quoted.WriteByte('_')
		case strings.IndexByte(headerSafe, octet) >= 0:
			quoted.WriteByte(octet)
		default:
			fmt.Fprintf(&quoted, "=%02X", octet)
		}
	}
	if base64Length < quoted.Len() {
		return "=?utf-8?b?" + base64.StdEncoding.EncodeToString(raw) + "?="
	}
	return "=?utf-8?q?" + quoted.String() + "?="
}
