package smtpsend

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// TestDjangoMessageGolden replays testdata/django_mail.json
// (TESTS/adapters/go/django_mail_golden.py): EmailMultiAlternatives bytes as
// core.tasks.custom_send_mail builds them.
func TestDjangoMessageGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/django_mail.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		MessageID string `json:"message_id"`
		Boundary  string `json:"boundary"`
		Cases     []struct {
			Name      string   `json:"name"`
			Subject   string   `json:"subject"`
			Body      *string  `json:"body"`
			HTML      string   `json:"html"`
			From      string   `json:"from"`
			To        []string `json:"to"`
			ReplyTo   []string `json:"reply_to"`
			MessageID string   `json:"message_id"`
			Unported  bool     `json:"unsupported"`
			Bytes     string   `json:"bytes"`
		} `json:"cases"`
	}
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) < 20 {
		t.Fatal("unexpected golden file")
	}
	date := time.Date(2026, 11, 9, 8, 43, 54, 0, time.UTC)
	for _, c := range golden.Cases {
		body := ""
		if c.Body != nil {
			body = *c.Body
		}
		messageID := c.MessageID
		if messageID == "" {
			messageID = golden.MessageID
		}
		m := DjangoMessage{Subject: c.Subject, Body: body, HTML: c.HTML, From: c.From, To: c.To, ReplyTo: c.ReplyTo,
			Date: date, MessageID: messageID, Boundary: golden.Boundary}
		got, err := RenderDjangoMessage(m)
		if err != nil {
			t.Errorf("%s: %v", c.Name, err)
			continue
		}
		if want := latin1String(c.Bytes); string(got) != want {
			t.Errorf("%s:\n got %q\nwant %q", c.Name, got, want)
		}
		if CheckDjangoMessage(m) != nil {
			t.Errorf("%s: check refuses a rendered message", c.Name)
		}
	}
}

func TestDjangoEnvelope(t *testing.T) {
	for from, want := range map[string]string{"Brand <a@example.test>": "Brand <a@example.test>",
		"a@example.test": "a@example.test", "Società Tre <noreply@example.test>": "=?utf-8?q?Societ=C3=A0_Tre?= <noreply@example.test>",
		"<a@example.test>": "a@example.test"} {
		if got, err := DjangoEnvelope(from); err != nil || got != want {
			t.Errorf("%q: %q %v, want %q", from, got, err, want)
		}
	}
	// Values Django's sanitize_address raises for: a value it cannot parse, a
	// null angle address (TypeError) and a line break in a part.
	for _, from := range []string{"<a@b@c>", "<>", "Name <>", "a\nb@c.test", "Undisclosed:;"} {
		if _, err := DjangoEnvelope(from); !errors.Is(err, ErrRaises) {
			t.Errorf("%q: want an error matching ErrRaises, got %v", from, err)
		}
	}
}

func TestDjangoHeaderNewlines(t *testing.T) {
	for _, value := range []string{"a\nb", "a\rb", "\r"} {
		if _, err := RenderDjangoMessage(DjangoMessage{Subject: value, From: "a@b.test", To: []string{"c@d.test"},
			Date: time.Unix(0, 0), MessageID: "<0@localhost>", Boundary: "b"}); !errors.Is(err, ErrRaises) {
			t.Errorf("subject %q: want an error matching ErrRaises, got %v", value, err)
		}
	}
	if _, err := RenderDjangoMessage(DjangoMessage{Subject: "s", From: "a@b.test", To: []string{"c@d.test"},
		Date: time.Unix(0, 0), MessageID: "a\nb", Boundary: "b"}); !errors.Is(err, ErrRaises) {
		t.Errorf("message-id line break: got %v", err)
	}
	if _, err := RenderDjangoMessage(DjangoMessage{Subject: "s", Body: "a\x00b", From: "a@b.test",
		To: []string{"c@d.test"}, Date: time.Unix(0, 0), MessageID: "<0@localhost>", Boundary: "b"}); err != nil {
		t.Errorf("a NUL in the body is rendered: %v", err)
	}
	// An empty From would be settings.DEFAULT_FROM_EMAIL in Django.
	if _, err := RenderDjangoMessage(DjangoMessage{Subject: "s", Body: "x", To: []string{"c@d.test"},
		Date: time.Unix(0, 0), MessageID: "<0@localhost>", Boundary: "b"}); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("empty From: want an application-state error, got %v", err)
	}
}

// TestDjangoMessageLineLimit pins the quoted-printable switch of SafeMIMEText:
// a line of 998 bytes stays 7bit, one of 999 switches the part to
// quoted-printable with 75-octet pieces and a soft break.
func TestDjangoMessageLineLimit(t *testing.T) {
	render := func(body string) string {
		raw, err := RenderDjangoMessage(DjangoMessage{Subject: "s", Body: body, From: "a@b.test", To: []string{"c@d.test"},
			Date: time.Unix(0, 0), MessageID: "<0@localhost>", Boundary: "b"})
		if err != nil {
			t.Fatalf("%d bytes: %v", len(body), err)
		}
		return string(raw)
	}
	kept := render(strings.Repeat("a", 998))
	if !strings.Contains(kept, "Content-Transfer-Encoding: 7bit\r\n") {
		t.Errorf("998 bytes is quoted-printable")
	}
	quoted := render(strings.Repeat("a", 999))
	if !strings.Contains(quoted, "Content-Transfer-Encoding: quoted-printable\r\n") {
		t.Fatalf("999 bytes is not quoted-printable")
	}
	_, payload, _ := strings.Cut(quoted, "\r\n\r\n")
	for _, line := range strings.Split(payload, "\r\n") {
		if len(line) > 76 {
			t.Fatalf("quoted-printable line of %d octets", len(line))
		}
	}
	if soft := strings.Count(payload, "=\r\n"); soft != 13 {
		t.Fatalf("quoted-printable body has %d soft breaks", soft)
	}
}

// TestDjangoSurrogateText covers Message.set_payload's
// text.encode('utf-8', 'surrogateescape'): a lone U+DC80..U+DCFF carries the
// byte it escapes and Django sends it unchanged, any other surrogate raises
// UnicodeEncodeError wherever Django first encodes the value.
func TestDjangoSurrogateText(t *testing.T) {
	message := func(subject, body, from string, to []string, messageID string) (string, error) {
		raw, err := RenderDjangoMessage(DjangoMessage{Subject: subject, Body: body, From: from, To: to,
			Date: time.Unix(0, 0), MessageID: messageID, Boundary: "b"})
		return string(raw), err
	}
	escaped := pystr.String([]rune{0xDC80})
	other := pystr.String([]rune{0xD800})
	want := "Content-Type: text/plain; charset=\"utf-8\"\r\nMIME-Version: 1.0\r\nContent-Transfer-Encoding: 8bit\r\n" +
		"Subject: S\r\nFrom: a@b.test\r\nTo: c@d.test\r\nDate: Thu, 01 Jan 1970 00:00:00 -0000\r\n" +
		"Message-ID: <0@localhost>\r\n\r\n\x80x"
	if got, err := message("S", escaped+"x", "a@b.test", []string{"c@d.test"}, "<0@localhost>"); err != nil || got != want {
		t.Errorf("surrogate-escaped body:\n got %q %v\nwant %q", got, err, want)
	}
	for name, call := range map[string]func() (string, error){
		"body surrogate": func() (string, error) {
			return message("S", other+"x", "a@b.test", []string{"c@d.test"}, "<0@localhost>")
		},
		"subject surrogate": func() (string, error) {
			return message(escaped, "x", "a@b.test", []string{"c@d.test"}, "<0@localhost>")
		},
		"from surrogate": func() (string, error) {
			return message("S", "x", "A"+escaped+" <a@b.test>", []string{"c@d.test"}, "<0@localhost>")
		},
		"to surrogate": func() (string, error) {
			return message("S", "x", "a@b.test", []string{"A" + escaped + " <c@d.test>"}, "<0@localhost>")
		},
		"message-id surrogate": func() (string, error) { return message("S", "x", "a@b.test", []string{"c@d.test"}, "<"+escaped+"@b>") },
	} {
		if _, err := call(); !errors.Is(err, ErrRaises) {
			t.Errorf("%s: want an error matching ErrRaises, got %v", name, err)
		}
	}
}
