package service

import (
	"bytes"
	"context"
	"errors"
	"io"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type setupQRTest struct {
	calls int
	uri   string
	fail  error
	after func()
}

func (q *setupQRTest) SVG(_ context.Context, uri string) (string, error) {
	q.calls++
	q.uri = uri
	if q.after != nil {
		q.after()
	}
	return "synthetic-qr", q.fail
}
func TestTwoFactorSetupAdmissionAndFailures(t *testing.T) {
	account := &domain.Account{ID: "scope"}
	identity := domain.ResolvedIdentity{Actor: account, Effective: account, ScopeUser: account}
	for _, kind := range []string{"missing identity", "cancelled", "short entropy", "QR failure", "cancel after QR", "success"} {
		t.Run(kind, func(t *testing.T) {
			q := &setupQRTest{}
			reader := bytes.NewReader(make([]byte, 20))
			admitted := identity
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			switch kind {
			case "missing identity":
				admitted = domain.ResolvedIdentity{}
			case "cancelled":
				cancel()
			case "short entropy":
				reader = bytes.NewReader(make([]byte, 19))
			case "cancel after QR":
				q.after = cancel
			case "QR failure":
				q.fail = errors.New("encoder failed")
			}
			value, err := NewTwoFactorSetup(reader, q, "support@example.test", "Club").Setup(ctx, admitted)
			if kind == "success" {
				if err != nil || value.Secret != "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" || value.QRCodeURI != "synthetic-qr" || q.calls != 1 || q.uri != "otpauth://totp/Club:support%40example.test?secret=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&issuer=Club" {
					t.Fatal("provisioning result differs", err)
				}
				return
			}
			if err == nil || value.Secret != "" || value.QRCodeURI != "" {
				t.Fatal("failed setup exposed partial provisioning material")
			}
			if kind == "short entropy" && !errors.Is(err, io.ErrUnexpectedEOF) {
				t.Fatal(err)
			}
			if (kind == "cancelled" || kind == "missing identity") && (reader.Len() != 20 || q.calls != 0) {
				t.Fatal("denied setup generated provisioning material")
			}
		})
	}
}
