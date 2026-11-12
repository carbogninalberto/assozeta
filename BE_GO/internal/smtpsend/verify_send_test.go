package smtpsend

import (
	"crypto/tls"
	"fmt"
	"net"
	"testing"
)

func TestPythonMessage(t *testing.T) {
	cases := []struct {
		err  error
		want string
		ok   bool
	}{
		{&Error{Kind: "SMTPAuthenticationError", Code: 535, Msg: "it's \"no\"\n\xff"}, `(535, b'it\'s "no"\n\xff')`, true},
		{&Error{Kind: "SMTPAuthenticationError", Code: 535, Msg: "it's"}, `(535, b"it's")`, true},
		{&Error{Kind: "SMTPSenderRefused", Code: 550, Msg: "no", Sender: "L'Associazione è <a@b.test>"}, `(550, b'no', "L'Associazione è <a@b.test>")`, true},
		{&Error{Kind: "SMTPSenderRefused", Code: 550, Msg: "no", Sender: "a\tb 🏐"}, `(550, b'no', 'a\tb 🏐')`, true},
		{&Error{Kind: "SMTPSenderRefused", Code: 550, Msg: "no", Sender: "a\U000e0001b"}, `(550, b'no', 'a\U000e0001b')`, true},
		{&Error{Kind: "SMTPSenderRefused", Code: 550, Msg: "no", Sender: "a\U0002ebf0b"}, "", false},
		{&Error{Kind: "SMTPRecipientsRefused", Refused: []Refusal{{Address: "a@b.test", Code: 550, Msg: "refused"}}}, `{'a@b.test': (550, b'refused')}`, true},
		{&Error{Kind: "SMTPNotSupportedError", Text: "STARTTLS extension not supported by server."}, "STARTTLS extension not supported by server.", true},
		{&Error{Kind: "SMTPServerDisconnected"}, "", false},
		{ErrUnsupported, "", false},
	}
	for _, c := range cases {
		got, ok := PythonMessage(c.err)
		if got != c.want || ok != c.ok {
			t.Errorf("PythonMessage(%v) = %q, %v; want %q, %v", c.err, got, ok, c.want, c.ok)
		}
	}
}

func TestPythonMessagePlatformTexts(t *testing.T) {
	greeting := tls.RecordHeaderError{Msg: "first record does not look like a TLS handshake", RecordHeader: [5]byte{'2', '2', '0', ' ', 'f'}}
	unknown := &net.DNSError{Err: "no such host", Name: "smtp.mistyped.invalid", IsNotFound: true}
	cases := []struct {
		err  error
		goos string
		want string
		ok   bool
	}{
		{greeting, "linux", "[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1032)", true},
		{fmt.Errorf("handshake: %w", greeting), "darwin", "[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1032)", true},
		// OpenSSL words an HTTP answer and an oversized record differently.
		{tls.RecordHeaderError{Msg: "first record does not look like a TLS handshake", RecordHeader: [5]byte{'H', 'T', 'T', 'P', '/'}}, "linux", "", false},
		{tls.RecordHeaderError{Msg: "oversized record received with length 20000", RecordHeader: [5]byte{'2', '2', '0', ' ', 'f'}}, "linux", "", false},
		{unknown, "darwin", "[Errno 8] nodename nor servname provided, or not known", true},
		{&net.OpError{Op: "dial", Net: "tcp", Err: unknown}, "linux", "[Errno -2] Name or service not known", true},
		{unknown, "windows", "", false},
		{&net.DNSError{Err: "i/o timeout", Name: "smtp.mistyped.invalid", IsTimeout: true, IsTemporary: true}, "linux", "", false},
		{&net.DNSError{Err: "server misbehaving", Name: "smtp.mistyped.invalid", IsTemporary: true}, "linux", "", false},
		// The idna codec rejects these before the resolver is asked.
		{&net.DNSError{Err: "no such host", Name: "smtp..invalid", IsNotFound: true}, "linux", "", false},
		{&net.DNSError{Err: "no such host", Name: "smtp.m\u00fcnchen.invalid", IsNotFound: true}, "linux", "", false},
	}
	for _, c := range cases {
		got, ok := pythonMessage(c.err, c.goos)
		if got != c.want || ok != c.ok {
			t.Errorf("pythonMessage(%v, %s) = %q, %v; want %q, %v", c.err, c.goos, got, ok, c.want, c.ok)
		}
	}
}
