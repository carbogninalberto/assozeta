package dto

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestMessageTemplateValidationAndRendering(t *testing.T) {
	input := domain.RequestObject{
		{Name: "channel", Value: "email"}, {Name: "locale", Value: "EN-gb"},
		{Name: "name", Value: "Invite"}, {Name: "subject", Value: "Hello {{ customer_name }}"},
		{Name: "body", Value: " Join on {{date}} at {{time}}. {{link}} "},
		{Name: "enabled", Value: false}, {Name: "is_default", Value: true},
	}
	template, err := ValidateMessageTemplate(input, domain.MessageTemplate{Enabled: true})
	if err != nil || template.Locale != "en-gb" || template.IsDefault {
		t.Fatalf("template %#v, error %v", template, err)
	}
	sample, err := SampleMessageTemplate(template)
	if err != nil || sample.Subject != "Hello Giulia Rossi" || sample.Body != " Join on 2026-12-15 at 18:30. https://example.org/info " {
		t.Fatalf("sample %#v, error %v", sample, err)
	}

	for _, tc := range []struct{ body, field, detail string }{
		{"{{unknown}}", "body", "Variabile sconosciuta: unknown."},
		{"{{date|safe}}", "body", "Sintassi variabile non valida. Usa {{nome_variabile}}."},
		{"{{{date}}}", "body", "Sintassi variabile non valida. Usa {{nome_variabile}}."},
		{"{{date", "body", "Sintassi variabile non valida. Usa {{nome_variabile}}."},
		{"{{link}}", "link", "Inserisci un valore per questa variabile."},
	} {
		template.Body = tc.body
		_, err := RenderMessageTemplate(template, map[string]string{"customer_name": "Giulia"})
		var invalid *MessageTemplateError
		if !errors.As(err, &invalid) || invalid.Field != tc.field || invalid.Detail != tc.detail {
			t.Errorf("%s: got %v", tc.body, err)
		}
	}
	template.Body = "Hi {{customer_name}}"
	real, err := RenderMessageTemplate(template, map[string]string{"customer_name": "Alex {{date}}"})
	if err != nil || real.Body != "Hi Alex {{date}}" {
		t.Fatalf("recipient values must be literal: %#v, %v", real, err)
	}
}

func TestMessageContextAndDestinations(t *testing.T) {
	for field, values := range map[string][]string{
		"date": {"2026-02-29", "0000-01-01"}, "time": {"24:00", "9:05"},
		"link": {"javascript:alert(1)", "https://", "https://example.org bad"},
	} {
		for _, value := range values {
			_, err := MessageTemplateContext(domain.RequestObject{{Name: field, Value: value}})
			var invalid *MessageTemplateError
			if !errors.As(err, &invalid) || invalid.Field != field {
				t.Errorf("%s=%s: got %v", field, value, err)
			}
		}
	}
	recipient := domain.MessageRecipient{Email: "giulia@example.org", Phone: " 0039 333-1234567 "}
	if got := MessageRecipientAddress(recipient, "email"); got != recipient.Email {
		t.Fatalf("email %q", got)
	}
	if got := MessageRecipientAddress(recipient, "whatsapp"); got != "+393331234567" {
		t.Fatalf("phone %q", got)
	}
	if got := WhatsAppMessageURL(domain.OutgoingMessage{Recipient: "+393331234567", Body: "Ciao & grazie! ☀"}); got != "https://wa.me/393331234567?text=Ciao%20%26%20grazie%21%20%E2%98%80" {
		t.Fatal(got)
	}
}
