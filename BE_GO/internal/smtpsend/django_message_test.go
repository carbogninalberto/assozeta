package smtpsend

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"
)

// TestDjangoMessageGolden replays testdata/django_mail.json
// (TESTS/adapters/go/django_mail_golden.py): EmailMultiAlternatives bytes as
// core.tasks.custom_send_mail builds them, and the cases outside the subset.
func TestDjangoMessageGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/django_mail.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		MessageID string `json:"message_id"`
		Boundary  string `json:"boundary"`
		Cases     []struct {
			Name        string   `json:"name"`
			Subject     string   `json:"subject"`
			Body        *string  `json:"body"`
			HTML        string   `json:"html"`
			From        string   `json:"from"`
			To          []string `json:"to"`
			ReplyTo     []string `json:"reply_to"`
			Unsupported bool     `json:"unsupported"`
			Bytes       string   `json:"bytes"`
		} `json:"cases"`
	}
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) < 20 {
		t.Fatal("unexpected golden file")
	}
	date := time.Date(2026, 11, 9, 8, 43, 54, 0, time.UTC)
	supported := 0
	for _, c := range golden.Cases {
		body := ""
		if c.Body != nil {
			body = *c.Body
		}
		m := DjangoMessage{Subject: c.Subject, Body: body, HTML: c.HTML, From: c.From, To: c.To, ReplyTo: c.ReplyTo,
			Date: date, MessageID: golden.MessageID, Boundary: golden.Boundary}
		got, err := RenderDjangoMessage(m)
		if c.Unsupported {
			if !errors.Is(err, ErrUnsupported) {
				t.Errorf("%s: want ErrUnsupported, got %v", c.Name, err)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %v", c.Name, err)
			continue
		}
		supported++
		var want strings.Builder
		for _, r := range c.Bytes {
			want.WriteByte(byte(r))
		}
		if string(got) != want.String() {
			t.Errorf("%s:\n got %q\nwant %q", c.Name, got, want.String())
		}
		if CheckDjangoMessage(m) != nil {
			t.Errorf("%s: check refuses a rendered message", c.Name)
		}
	}
	if supported < 15 {
		t.Fatalf("only %d supported cases", supported)
	}
}

func TestDjangoEnvelope(t *testing.T) {
	for from, want := range map[string]string{"Brand <a@example.test>": "a@example.test", "a@example.test": "a@example.test",
		"Società Tre <noreply@example.test>": "noreply@example.test"} {
		if got, err := DjangoEnvelope(from); err != nil || got != want {
			t.Errorf("%q: %q %v", from, got, err)
		}
	}
	for _, from := range []string{"<a@example.test>", "Name <a@b@c>", "None", "\"Q\" <a@example.test>", "Name  <a@example.test>"} {
		if _, err := DjangoEnvelope(from); err == nil {
			t.Errorf("%q accepted", from)
		}
	}
}
