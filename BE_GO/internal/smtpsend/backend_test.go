package smtpsend

import (
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"
)

// TestDjangoTextGolden replays testdata/django_text.json
// (TESTS/adapters/go/instance_outbound_golden.py): Django's EmailMessage bytes
// and compat32 header folds.
func TestDjangoTextGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/django_text.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Messages []struct {
			Name       string `json:"name"`
			SenderName string `json:"sender_name"`
			FromEmail  string `json:"from_email"`
			To         string `json:"to"`
			MessageID  string `json:"message_id"`
			Subject    string `json:"subject"`
			Bytes      string `json:"bytes"`
		} `json:"messages"`
		Folds []struct{ Header, Value, Folded string } `json:"folds"`
	}
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	date := time.Date(2026, 11, 9, 8, 43, 54, 0, time.UTC)
	subject := "Assozeta — verifica email"
	body := "Questo messaggio è stato richiesto dal proprietario dell’istanza per verificare la configurazione email."
	for _, m := range golden.Messages {
		byHeader, err := RenderDjangoText(DjangoText{Subject: subject, Body: body, SenderName: m.SenderName,
			FromEmail: m.FromEmail, To: m.To, Date: date, MessageID: m.MessageID})
		if err != nil {
			t.Fatalf("%s: %v", m.Name, err)
		}
		if got, want := string(byHeader), latin1String(m.Bytes); got != want {
			t.Errorf("%s:\n got %q\nwant %q", m.Name, got, want)
		}
	}
	if len(golden.Messages) < 20 || len(golden.Folds) < 10 {
		t.Fatalf("unexpected golden file")
	}
	for _, f := range golden.Folds {
		if got, err := foldDjangoHeader(f.Header, f.Value); err != nil || got != f.Folded {
			t.Errorf("fold %s %q:\n got %q\nwant %q", f.Header, f.Value, got, f.Folded)
		}
	}
}

// TestDjangoAnyGolden replays testdata/django_any.json: EmailMultiAlternatives
// messages of every shape core.tasks.custom_send_mail builds (subjects, bodies,
// addresses and Message-IDs of any length and spacing) as Django 5.2.18
// renders them, and the exceptions it raises for them.
func TestDjangoAnyGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/django_any.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Date      string `json:"date"`
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
			Bytes     *string  `json:"bytes"`
			Error     string   `json:"error"`
		} `json:"cases"`
	}
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) < 60 {
		t.Fatalf("unexpected golden file: %d cases", len(golden.Cases))
	}
	date, err := time.Parse(time.RFC1123Z, strings.Replace(golden.Date, " -0000", " +0000", 1))
	if err != nil {
		t.Fatal(err)
	}
	rendered, raised := 0, 0
	for _, c := range golden.Cases {
		body := ""
		if c.Body != nil {
			body = *c.Body
		}
		messageID := c.MessageID
		if messageID == "" {
			messageID = golden.MessageID
		}
		got, err := RenderDjangoMessage(DjangoMessage{Subject: c.Subject, Body: body, HTML: c.HTML, From: c.From,
			To: c.To, ReplyTo: c.ReplyTo, Date: date, MessageID: messageID, Boundary: golden.Boundary})
		if c.Error != "" {
			if !errors.Is(err, ErrRaises) {
				t.Errorf("%s: want an error matching ErrRaises (%s), got %v", c.Name, c.Error, err)
			}
			raised++
			continue
		}
		if err != nil {
			t.Errorf("%s: %v", c.Name, err)
			continue
		}
		if c.Bytes == nil {
			t.Fatalf("%s: golden has neither bytes nor an error", c.Name)
		}
		if want := latin1String(*c.Bytes); string(got) != want {
			t.Errorf("%s:\n got %q\nwant %q", c.Name, got, want)
		}
		rendered++
	}
	if rendered < 60 || raised < 3 {
		t.Fatalf("coverage: rendered %d raised %d", rendered, raised)
	}
}

// TestDjangoAddressesGolden replays testdata/django_addresses.json:
// email._header_value_parser.get_mailbox, sanitize_address and
// smtplib.quoteaddr of sanitize_address for addresses of every shape, and the
// exceptions they raise. The golden strings are the latin-1 text of their
// UTF-8 bytes, so a lone surrogate survives the JSON round trip.
func TestDjangoAddressesGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/django_addresses.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Addresses []struct {
			Address string `json:"address"`
			Mailbox *struct {
				DisplayName *string `json:"display_name"`
				LocalPart   *string `json:"local_part"`
				Domain      *string `json:"domain"`
				Rest        string  `json:"rest"`
			} `json:"mailbox"`
			MailboxError   string  `json:"mailbox_error"`
			Sanitized      *string `json:"sanitized"`
			SanitizedError string  `json:"sanitized_error"`
			Envelope       *string `json:"envelope"`
			EnvelopeError  string  `json:"envelope_error"`
		} `json:"addresses"`
	}
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Addresses) < 150 {
		t.Fatalf("unexpected golden file: %d addresses", len(golden.Addresses))
	}
	parsed, sanitized, quoted := 0, 0, 0
	failure := func(kind string) bool {
		switch kind {
		case "":
			return false
		case "HeaderParseError":
			return true
		}
		return true
	}
	for _, c := range golden.Addresses {
		address := latin1String(c.Address)
		token, rest, err := getMailbox(address)
		switch {
		case c.MailboxError != "" && !failure(c.MailboxError):
			t.Fatalf("%q: unknown golden error %q", address, c.MailboxError)
		case c.MailboxError == "HeaderParseError":
			if !isHeaderParseError(err) {
				t.Fatalf("%q: want HeaderParseError, got %v", address, err)
			}
		case c.MailboxError != "":
			if err == nil || isHeaderParseError(err) {
				t.Fatalf("%q: want %s, got %v", address, c.MailboxError, err)
			}
		default:
			if err != nil {
				t.Fatalf("%q: getMailbox = %v", address, err)
			}
			if rest != latin1String(c.Mailbox.Rest) {
				t.Fatalf("%q: rest %q, want %q", address, rest, latin1String(c.Mailbox.Rest))
			}
			name, err := mailboxDisplayName(token)
			if err != nil {
				t.Fatalf("%q: display name = %v", address, err)
			}
			if !sameOptional(name, c.Mailbox.DisplayName) {
				t.Fatalf("%q: display name %s, want %s", address, showOptional(name), showOptional(c.Mailbox.DisplayName))
			}
			localPart, err := mailboxLocalPart(token)
			if err != nil {
				t.Fatalf("%q: local part = %v", address, err)
			}
			if !sameOptional(localPart, c.Mailbox.LocalPart) {
				t.Fatalf("%q: local part %s, want %s", address, showOptional(localPart), showOptional(c.Mailbox.LocalPart))
			}
			domain, err := mailboxDomain(token)
			if err != nil {
				t.Fatalf("%q: domain = %v", address, err)
			}
			if !sameOptional(domain, c.Mailbox.Domain) {
				t.Fatalf("%q: domain %s, want %s", address, showOptional(domain), showOptional(c.Mailbox.Domain))
			}
			parsed++
		}
		value, err := SanitizeAddress(address)
		if c.SanitizedError != "" {
			if !errors.Is(err, ErrRaises) {
				t.Fatalf("%q: want an error matching ErrRaises (%s), got %v %q", address, c.SanitizedError, err, value)
			}
		} else if err != nil {
			t.Fatalf("%q: SanitizeAddress = %v", address, err)
		} else if want := latin1String(*c.Sanitized); value != want {
			t.Fatalf("%q: SanitizeAddress = %q, want %q", address, value, want)
		} else {
			sanitized++
		}
		envelope, err := DjangoEnvelope(address)
		if c.EnvelopeError != "" {
			if !errors.Is(err, ErrRaises) {
				t.Fatalf("%q: want an error matching ErrRaises (%s), got %v %q", address, c.EnvelopeError, err, envelope)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%q: DjangoEnvelope = %v", address, err)
		}
		if want := latin1String(*c.Envelope); QuoteAddr(envelope) != want {
			t.Fatalf("%q: envelope %q, want %q", address, QuoteAddr(envelope), want)
		}
		quoted++
	}
	if parsed < 150 || sanitized < 100 || quoted < 100 {
		t.Fatalf("coverage: parsed %d sanitized %d quoted %d", parsed, sanitized, quoted)
	}
}

// sameOptional compares an optional str with a golden value.
func sameOptional(got *string, want *string) bool {
	if got == nil || want == nil {
		return got == nil && want == nil
	}
	return *got == latin1String(*want)
}

func showOptional(value *string) string {
	if value == nil {
		return "<nil>"
	}
	return strconv.Quote(*value)
}
