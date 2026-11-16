package service

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
)

// The text/plain multipart send_email builds; a subject Python raises on is
// ErrHeader, and raw environment bytes are decoded with surrogateescape and
// sent.
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
		"subject line break":         {"Riga\nseconda", []string{"socio@example.test"}, smtpsend.ErrHeader},
		"subject continuation line":  {"Riga\n seconda", []string{"socio@example.test"}, nil},
		"subject lone surrogate":     {"Riga\xed\xa0\x80", []string{"socio@example.test"}, smtpsend.ErrRaises},
		"subject longer than a line": {strings.Repeat("Riga ", 300000), []string{"socio@example.test"}, nil},
		"quoted recipient":           {"Avviso", []string{`"a b"@example.test`}, nil},
		"ip literal":                 {"Avviso", []string{"socio@[127.0.0.1]"}, nil},
		"invalid text":               {"Avviso\xff", []string{"socio@example.test"}, nil},
	} {
		if _, err := s.render(row, dto.SendEmailInput{Subject: input.subject, Message: "Ciao"}, input.to); !errors.Is(err, input.want) {
			t.Errorf("%s: err = %v, want %v", name, err, input.want)
		}
	}
}

type sendEmailTestSMTP struct{}

func (sendEmailTestSMTP) Verify(context.Context, smtpsend.Server) error { return nil }

func (sendEmailTestSMTP) Send(context.Context, smtpsend.Server, string, []string, string) error {
	return nil
}

// A render that reports an application-state error is the 500 page, never a
// handoff: it is returned as it is, not joined with the not-ported sentinel.
func TestCommunicationSendEmailKeepsAppStateRenderError(t *testing.T) {
	host, user, password := "smtp.example.test", "mittente@example.test", "segreta"
	port := int64(587)
	row := domain.CommunicationConfigurationRecord{Host: &host, Port: &port, User: &user, Password: &password, DailyLimit: 10}
	s := NewCommunicationSendEmail(nil, nil, CommunicationSendEmailSettings{}, time.Now)
	s.smtp = sendEmailTestSMTP{}
	s.renderedMessage = func(domain.CommunicationConfigurationRecord, dto.SendEmailInput, []string) (string, error) {
		return "", refusal.AppState("send email test: unexpected application state")
	}
	_, err := s.sendEmail(context.Background(), row, dto.SendEmailInput{Subject: "Avviso", Message: "Ciao"},
		[]string{"socio@example.test"}, domain.LoginAuditContext{})
	if !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("err = %v, want an application-state error", err)
	}
	for _, sentinel := range []error{domain.ErrSendEmailNotPorted, refusal.ErrNotPorted} {
		if errors.Is(err, sentinel) {
			t.Fatalf("err = %v, want no match for %v", err, sentinel)
		}
	}
}
