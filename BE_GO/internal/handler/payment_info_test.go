package handler

import (
	"context"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/google/uuid"
	"github.com/labstack/echo/v4"
)

type paymentInfoTokens struct{ calls int }

func (s *paymentInfoTokens) Verify(token, kind string) (map[string]any, error) {
	s.calls++
	if token == "valid" {
		return map[string]any{"user_id": "a1000000-0000-4000-8000-000000220008"}, nil
	}
	return nil, errors.New("bad token")
}

type paymentInfoAccounts struct{ account domain.Account }

func (s paymentInfoAccounts) ByID(context.Context, string) (domain.Account, error) {
	return s.account, nil
}

type paymentInfoReader struct{ calls int }

func (s *paymentInfoReader) ReadPaymentInfo(context.Context, domain.PaymentInfoLookup) (*domain.PaymentInfoRecord, error) {
	s.calls++
	return nil, nil
}
func (s *paymentInfoReader) ReadPaymentInfoCategoryNames(context.Context, []uuid.UUID) (map[uuid.UUID]string, error) {
	panic("unexpected metadata lookup")
}

func TestPaymentInfoOptionalAuthenticationPrecedence(t *testing.T) {
	for _, tc := range []struct {
		name, method, accept, authorization, query string
		status, tokenCalls, reads                  int
	}{
		{"negotiation before invalid credential", "POST", "text/plain", "Bearer invalid", "", 406, 0, 0},
		{"format before invalid credential", "GET", "", "Bearer invalid", "?format=xml", 404, 0, 0},
		{"format before accept and invalid credential", "GET", "text/plain", "Bearer invalid", "?format=xml", 404, 0, 0},
		{"invalid token before method", "POST", "", "Bearer invalid", "", 401, 1, 0},
		{"admin permission before method", "POST", "", "Bearer valid", "", 403, 1, 0},
		{"anonymous forged scope ignored", "GET", "", "", "", 404, 0, 1},
		{"empty format ignored", "GET", "", "", "?format=", 404, 0, 1},
		{"anonymous options before UUID lookup", "OPTIONS", "", "", "", 200, 0, 0},
		{"anonymous method before UUID lookup", "POST", "", "", "", 405, 0, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tokens := &paymentInfoTokens{}
			reader := &paymentInfoReader{}
			accounts := paymentInfoAccounts{domain.Account{ID: "a1000000-0000-4000-8000-000000220008", Active: true, Superuser: true}}
			h := NewPaymentInfoHandler(service.NewPaymentInfo(reader), service.NewActorAuthentication(accounts, tokens), service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil))
			e := echo.New()
			e.Any("/payment/:uid/info", h.Info)
			r := httptest.NewRequest(tc.method, "/payment/a1000000-0000-4000-8000-000000226000/info"+tc.query, strings.NewReader("malformed json"))
			r.Header.Set("Accept", tc.accept)
			r.Header.Set("Accept-Language", "en")
			r.Header.Set("Authorization", tc.authorization)
			if tc.authorization == "" {
				r.Header.Set("X-Impersonation-Id", "forged")
				r.Header.Set("User-Id", "forged")
			}
			r.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if w.Code != tc.status || tokens.calls != tc.tokenCalls || reader.calls != tc.reads {
				t.Fatalf("status=%d body=%s tokenCalls=%d reads=%d", w.Code, w.Body.String(), tokens.calls, reader.calls)
			}
			if w.Header().Get("Allow") != "GET, OPTIONS" || w.Header().Get("Set-Cookie") != "" {
				t.Fatal(w.Header())
			}
		})
	}
}

func TestPaymentInfoMalformedUUIDResponse(t *testing.T) {
	h := NewPaymentInfoHandler(service.NewPaymentInfo(&paymentInfoReader{}), service.NewActorAuthentication(nil, nil), nil)
	e := echo.New()
	e.Any("/payment/:uid/info", h.Info)
	w := httptest.NewRecorder()
	e.ServeHTTP(w, httptest.NewRequest("GET", "/payment/not-a-uuid/info", nil))
	var body []string
	if w.Code != 400 || json.Unmarshal(w.Body.Bytes(), &body) != nil || len(body) != 1 || body[0] != "badly formed hexadecimal UUID string" {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
}
