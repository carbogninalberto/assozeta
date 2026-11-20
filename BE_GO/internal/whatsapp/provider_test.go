package whatsapp

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
)

func TestTextAndProviderFailures(t *testing.T) {
	for _, tc := range []struct {
		status    int
		transient bool
	}{{200, false}, {400, false}, {429, true}, {503, true}} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/v23.0/phone/messages" || r.Header.Get("Authorization") != "Bearer fixture-token" {
				t.Error("incorrect send endpoint or authorization")
			}
			var payload struct {
				To, Type  string
				Text      struct{ Body string }
				Reference string `json:"biz_opaque_callback_data"`
			}
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Error(err)
			}
			if payload.To != "393331234567" || payload.Type != "text" || payload.Text.Body != "hello" || payload.Reference != "ref" {
				t.Errorf("incorrect text payload: %+v", payload)
			}
			w.WriteHeader(tc.status)
			if tc.status == 200 {
				w.Write([]byte(`{"messages":[{"id":"wamid.example"}]}`))
			} else {
				w.Write([]byte(`{"error":{"code":42,"message":"bad fixture-token"}}`))
			}
		}))
		client := NewCloudAPI(config.WhatsAppSettings{PhoneID: "phone", AccessToken: "fixture-token", BaseURL: server.URL, APIVersion: "v23.0"})
		id, err := client.SendText(context.Background(), "+393331234567", "hello", "ref")
		server.Close()
		if tc.status == 200 {
			if err != nil || id != "wamid.example" {
				t.Fatalf("send: %q, %v", id, err)
			}
		} else {
			var failure *ProviderError
			if !errors.As(err, &failure) || failure.Transient != tc.transient || failure.Code != "42" || failure.Message != "bad [redacted]" {
				t.Fatalf("http %d: %v", tc.status, err)
			}
		}
		_, err = client.SendText(context.Background(), "+393331234567", "hello", "ref")
		var failure *ProviderError
		if !errors.As(err, &failure) || !failure.Transient || failure.Code != "network" {
			t.Fatalf("connection failure: %v", err)
		}
	}
}

func TestApprovedTemplateOperations(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v23.0/account/message_templates" {
			t.Error(r.URL.Path)
		}
		switch {
		case r.Method == http.MethodPost:
			var template Object
			if err := json.NewDecoder(r.Body).Decode(&template); err != nil || template["name"] != "receipt" {
				t.Error("missing template definition")
			}
			w.Write([]byte(`{"id":"t","status":"PENDING"}`))
		case r.URL.Query().Get("after") == "":
			w.Write([]byte(`{"data":[{"name":"receipt","language":"it","status":"PENDING"}],"paging":{"next":"https://unused.invalid/","cursors":{"after":"page2"}}}`))
		default:
			w.Write([]byte(`{"data":[{"id":"t","name":"receipt","language":"en_US","status":"APPROVED"}]}`))
		}
	}))
	defer server.Close()
	p := NewCloudAPI(config.WhatsAppSettings{AccountID: "account", AccessToken: "fixture", BaseURL: server.URL, APIVersion: "v23.0"})
	registered, err := p.RegisterTemplate(context.Background(), Object{"name": "receipt", "language": "en_US", "category": "UTILITY", "components": []Object{}})
	if err != nil || registered["status"] != "PENDING" {
		t.Fatal("register", err)
	}
	list, err := p.ListTemplates(context.Background())
	if err != nil || len(list) != 1 || list[0]["id"] != "t" {
		t.Fatalf("list: %v %v", list, err)
	}
	found, err := p.GetTemplate(context.Background(), "receipt", "en_US")
	if err != nil || found["id"] != "t" {
		t.Fatal("get", err)
	}
	if _, err := p.GetTemplate(context.Background(), "receipt", "it"); err == nil {
		t.Fatal("returned unapproved translation")
	}
}

func TestWebhookAuthentication(t *testing.T) {
	p := NewCloudAPI(config.WhatsAppSettings{VerifyToken: "verify", AppSecret: "secret"})
	challenge, err := p.VerifyWebhook("subscribe", "verify", "123")
	_, deniedErr := p.VerifyWebhook("subscribe", "", "123")
	if err != nil || challenge != "123" || !errors.Is(deniedErr, denied) {
		t.Fatal("unexpected verification result")
	}
	if _, err := p.ParseStatusCallback([]byte("{}"), ""); !errors.Is(err, denied) {
		t.Fatal("accepted unsigned callback")
	}
}
