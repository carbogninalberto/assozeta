package smtpsend

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"unicode"
)

// VerifySend mirrors verify_smtp(send_email=True): connect (SMTP_SSL, or SMTP
// and starttls() for "TLS"), login, sendmail(given, to, message) with a bytes
// message and quit(). given is the from_addr text ("Name <address>") and
// address the envelope address smtplib.quoteaddr parses out of it; address
// and to must pass CheckEnvelope, otherwise ErrUnsupported is returned before
// connecting. On a failure the socket is closed without QUIT, as the release
// of Python's SMTP object does.
func VerifySend(ctx context.Context, s Server, given, address, to string, message []byte) error {
	if err := CheckEnvelope(address, []string{to}); err != nil {
		return err
	}
	c, err := open(ctx, s)
	if err != nil {
		return err
	}
	defer c.close()
	if err = c.login(s.User, s.Password); err != nil {
		return err
	}
	if err = c.sendmailFrom(given, address, []string{to}, message); err != nil {
		return err
	}
	return c.quit()
}

// PythonMessage is str(exception) for the failures whose Python text is
// fixed: smtplib reply exceptions (the args tuple), the fixed-text smtplib
// errors, a refused connection, a plain-text SMTP reply where the TLS
// handshake was expected and a host name the resolver does not know. ok is
// false for every other failure (other TLS errors, temporary resolver
// failures, connect timeouts, resets).
func PythonMessage(err error) (string, bool) {
	return pythonMessage(err, runtime.GOOS)
}

// wrongVersionNumber is str(ssl.SSLError) of CPython 3.13.16 when the peer
// answers the ClientHello with plain text (OpenSSL's SSL_R_WRONG_VERSION_NUMBER;
// the suffix is the handshake call site in that release's _ssl.c).
const wrongVersionNumber = "[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1032)"

// unknownHostText is str(socket.gaierror) for EAI_NONAME: the C library's
// code and gai_strerror text (macOS, and glibc as in the Django image).
var unknownHostText = map[string]string{
	"darwin": "[Errno 8] nodename nor servname provided, or not known",
	"linux":  "[Errno -2] Name or service not known",
}

// plainHostName reports a host the 'idna' codec passes through unchanged:
// ASCII, no empty label, every label under 64 bytes.
func plainHostName(host string) bool {
	return PlainHostName(host)
}

// PlainHostName reports a host text the 'idna' codec passes through
// unchanged (see plainHostName).
func PlainHostName(host string) bool {
	if host == "" || len(host) > 253 {
		return false
	}
	for _, label := range strings.Split(host, ".") {
		if label == "" || len(label) > 63 {
			return false
		}
		for i := 0; i < len(label); i++ {
			if label[i] < 0x20 || label[i] > 0x7e {
				return false
			}
		}
	}
	return true
}

func pythonMessage(err error, goos string) (string, bool) {
	var header tls.RecordHeaderError
	if errors.As(err, &header) && header.Msg == "first record does not look like a TLS handshake" &&
		header.RecordHeader[0] >= '0' && header.RecordHeader[0] <= '9' && header.RecordHeader[1] != 3 {
		// An SMTP reply line where the ServerHello was expected.
		return wrongVersionNumber, true
	}
	var dns *net.DNSError
	if errors.As(err, &dns) && dns.IsNotFound && !dns.IsTimeout && !dns.IsTemporary && plainHostName(dns.Name) {
		text, ok := unknownHostText[goos]
		return text, ok
	}
	var smtp *Error
	if errors.As(err, &smtp) {
		switch {
		case smtp.Kind == "SMTPRecipientsRefused":
			parts := make([]string, 0, len(smtp.Refused))
			for _, refusal := range smtp.Refused {
				address, ok := PyStrRepr(refusal.Address)
				if !ok {
					return "", false
				}
				parts = append(parts, address+": ("+strconv.Itoa(refusal.Code)+", "+pythonBytesRepr([]byte(refusal.Msg))+")")
			}
			return "{" + strings.Join(parts, ", ") + "}", true
		case smtp.Kind == "SMTPSenderRefused":
			sender, ok := PyStrRepr(smtp.Sender)
			if !ok {
				return "", false
			}
			return "(" + strconv.Itoa(smtp.Code) + ", " + pythonBytesRepr([]byte(smtp.Msg)) + ", " + sender + ")", true
		case smtp.Code != 0:
			return "(" + strconv.Itoa(smtp.Code) + ", " + pythonBytesRepr([]byte(smtp.Msg)) + ")", true
		case smtp.Text != "":
			return smtp.Text, true
		}
		return "", false
	}
	if errors.Is(err, syscall.ECONNREFUSED) {
		// str(ConnectionRefusedError): the platform errno and strerror text.
		return "[Errno " + strconv.Itoa(int(syscall.ECONNREFUSED)) + "] Connection refused", true
	}
	return "", false
}

// pythonBytesRepr is repr() of a bytes object.
func pythonBytesRepr(b []byte) string {
	quote := byte('\'')
	if strings.IndexByte(string(b), '\'') >= 0 && strings.IndexByte(string(b), '"') < 0 {
		quote = '"'
	}
	var out strings.Builder
	out.WriteByte('b')
	out.WriteByte(quote)
	const hex = "0123456789abcdef"
	for _, c := range b {
		switch {
		case c == quote || c == '\\':
			out.WriteByte('\\')
			out.WriteByte(c)
		case c == '\t':
			out.WriteString(`\t`)
		case c == '\n':
			out.WriteString(`\n`)
		case c == '\r':
			out.WriteString(`\r`)
		case c < 0x20 || c >= 0x7f:
			out.WriteString(`\x`)
			out.WriteByte(hex[c>>4])
			out.WriteByte(hex[c&15])
		default:
			out.WriteByte(c)
		}
	}
	out.WriteByte(quote)
	return out.String()
}

// pythonStrRepr is repr() of a str made of printable ASCII and letters; ok is
// false for any other text.
func pythonStrRepr(s string) (string, bool) {
	quote := '\''
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		quote = '"'
	}
	var out strings.Builder
	out.WriteRune(quote)
	for _, r := range s {
		switch {
		case r == quote || r == '\\':
			out.WriteRune('\\')
			out.WriteRune(r)
		case r >= 0x20 && r < 0x7f, r >= 0x80 && unicode.IsLetter(r):
			out.WriteRune(r)
		default:
			return "", false
		}
	}
	out.WriteRune(quote)
	return out.String(), true
}
