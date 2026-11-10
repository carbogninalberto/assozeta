package service

import (
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
)

// The text/plain multipart send_email builds; a subject Python raises on is
// ErrHeader, text that is not valid Unicode stays outside the port.
func TestCommunicationSendEmailRenderSubset(t *testing.T) {
	user, name := "mittente@example.test", "Associazione"
	row := domain.CommunicationConfigurationRecord{User: &user, SenderName: &name}
	s := NewCommunicationSendEmail(nil, nil, CommunicationSendEmailSettings{}, time.Now)
	s.boundary = func() string { return "===============0000000000000000001==" }
	message, err := s.render(row, dto.SendEmailInput{Subject: "Avviso", Message: "Ciao"}, []string{"socio@example.test"})
	want := "Content-Type: multipart/mixed; boundary=\"===============0000000000000000001==\"\nMIME-Version: 1.0\n" +
		"From: Associazione <mittente@example.test>\nTo: socio@example.test\nSubject: Avviso\n\n" +
		"--===============0000000000000000001==\nContent-Type: text/plain; charset=\"utf-8\"\nMIME-Version: 1.0\n" +
		"Content-Transfer-Encoding: base64\n\nQ2lhbw==\n\n--===============0000000000000000001==--\n"
	if err != nil || message != want {
		t.Fatalf("render = %q, %v", message, err)
	}
	for name, input := range map[string]struct {
		subject string
		to      []string
		want    error
	}{
		"subject line break":        {"Riga\nseconda", []string{"socio@example.test"}, smtpsend.ErrHeader},
		"subject continuation line": {"Riga\n seconda", []string{"socio@example.test"}, nil},
		"quoted recipient":          {"Avviso", []string{`"a b"@example.test`}, nil},
		"ip literal":                {"Avviso", []string{"socio@[127.0.0.1]"}, nil},
		"invalid text":              {"Avviso\xff", []string{"socio@example.test"}, smtpsend.ErrUnsupported},
	} {
		if _, err := s.render(row, dto.SendEmailInput{Subject: input.subject, Message: "Ciao"}, input.to); !errors.Is(err, input.want) {
			t.Errorf("%s: err = %v, want %v", name, err, input.want)
		}
	}
}
