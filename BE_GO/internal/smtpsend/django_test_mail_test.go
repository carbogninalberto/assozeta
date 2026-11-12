package smtpsend

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"
)

// djangoTestMailGolden replays testdata/django_test_mail.json
// (TESTS/adapters/go/django_test_mail_golden.py): the probe's
// formataddr((sender_name, from_email)), EmailMessage bytes and the SMTP
// envelope texts.
type djangoTestMailGolden struct {
	Django  string `json:"django"`
	Python  string `json:"python"`
	Subject string `json:"subject"`
	Body    string `json:"body"`
	Cases   []struct {
		SenderName string   `json:"sender_name"`
		FromEmail  string   `json:"from_email"`
		Recipient  string   `json:"recipient"`
		FormatAddr string   `json:"formataddr"`
		Message    string   `json:"message"`
		MailFrom   string   `json:"mail_from"`
		RcptTo     []string `json:"rcpt_to"`
		Error      string   `json:"error"`
	} `json:"cases"`
}

// latin1String is the golden message: bytes decoded as latin-1.
func latin1String(s string) string {
	var out strings.Builder
	out.Grow(len(s))
	for _, r := range s {
		out.WriteByte(byte(r))
	}
	return out.String()
}

func TestDjangoTestMailGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/django_test_mail.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden djangoTestMailGolden
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) < 98 || golden.Subject == "" || golden.Body == "" {
		t.Fatalf("incomplete golden file: %d cases", len(golden.Cases))
	}
	date := time.Date(2026, 11, 11, 10, 0, 0, 0, time.UTC)
	const messageID = "<177000000000.1.2@host.example>"
	subject := HeaderEncode(golden.Subject)
	accepted, refused := 0, 0
	for i, item := range golden.Cases {
		from, err := FormatAddr(item.SenderName, item.FromEmail)
		if item.Error != "" {
			if !strings.Contains(item.Error, "UnicodeEncodeError") {
				t.Fatalf("%d: golden error is not a formataddr failure: %s", i, item.Error)
			}
			if !errors.Is(err, ErrFormatAddr) {
				t.Fatalf("%d %q: FormatAddr = %v, want ErrFormatAddr", i, item.FromEmail, err)
			}
			_, err = RenderDjangoText(DjangoText{EncodedSubject: subject, Body: golden.Body, SenderName: item.SenderName,
				FromEmail: item.FromEmail, To: item.Recipient, Date: date, MessageID: messageID})
			if !errors.Is(err, ErrFormatAddr) {
				t.Fatalf("%d %q: RenderDjangoText = %v, want ErrFormatAddr", i, item.FromEmail, err)
			}
			refused++
			continue
		}
		if err != nil {
			t.Fatalf("%d %q: FormatAddr = %v", i, item.FromEmail, err)
		}
		if from != item.FormatAddr {
			t.Fatalf("%d %q: formataddr\ngo:     %q\npython: %q", i, item.FromEmail, from, item.FormatAddr)
		}
		message, err := RenderDjangoText(DjangoText{EncodedSubject: subject, Body: golden.Body, SenderName: item.SenderName,
			FromEmail: item.FromEmail, To: item.Recipient, Date: date, MessageID: messageID})
		if err != nil {
			t.Fatalf("%d %q -> %q: RenderDjangoText = %v", i, item.FromEmail, item.Recipient, err)
		}
		if got, want := string(message), latin1String(item.Message); got != want {
			t.Fatalf("%d %q -> %q: message differs\ngo:     %q\npython: %q", i, item.FromEmail, item.Recipient, got, want)
		}
		mailFrom, rcptTo, err := DjangoTestEnvelope(from, item.Recipient)
		if err != nil {
			t.Fatalf("%d %q -> %q: envelope = %v", i, item.FromEmail, item.Recipient, err)
		}
		if mailFrom != item.MailFrom {
			t.Fatalf("%d %q: mail from\ngo:     %q\npython: %q", i, item.FromEmail, mailFrom, item.MailFrom)
		}
		if len(item.RcptTo) != 1 || rcptTo != item.RcptTo[0] {
			t.Fatalf("%d %q -> %q: rcpt to\ngo:     %q\npython: %q", i, item.FromEmail, item.Recipient, rcptTo, item.RcptTo)
		}
		accepted++
	}
	if accepted == 0 || refused == 0 {
		t.Fatalf("coverage: accepted %d refused %d", accepted, refused)
	}
	t.Logf("accepted %d, refused %d", accepted, refused)
}
