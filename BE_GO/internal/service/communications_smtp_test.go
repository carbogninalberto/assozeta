package service

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestSMTPVerifyWithoutConnection(t *testing.T) {
	text := func(value string) *string { return &value }
	number := func(value int64) *int64 { return &value }
	for _, test := range []struct {
		host    *string
		port    *int64
		message string
	}{
		{nil, number(587), "please run connect() first"},
		{text(""), nil, "please run connect() first"},
		{text("smtp.example.test:abc"), nil, "nonnumeric port"},
		{text("smtp.example.test:"), number(0), "nonnumeric port"},
		{text("smtp.example.test: 2_5x"), nil, "nonnumeric port"},
		// Outbound connections are refused.
		{text("smtp.example.test"), number(587), ""},
		{text("smtp.example.test"), nil, ""},
		{text("smtp.example.test:25"), nil, ""},
		{text("smtp.example.test: +2_5 "), number(0), ""},
		{text("smtp.example.test:abc"), number(25), ""},
		{text("[::1]:abc"), nil, ""},
		{text("smtp.example.test:٢٥"), nil, ""},
	} {
		message, err := SMTPVerifyWithoutConnection(domain.CommunicationConfigurationRecord{Host: test.host, Port: test.port, Encryption: "TLS"})
		if test.message == "" {
			if !errors.Is(err, domain.ErrSMTPVerifyNotPorted) || message != "" {
				t.Fatalf("%v %v: got %q %v, want refusal", test.host, test.port, message, err)
			}
			continue
		}
		if err != nil || message != test.message {
			t.Fatalf("%v %v: got %q %v, want %q", test.host, test.port, message, err, test.message)
		}
	}
}
