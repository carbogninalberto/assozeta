package service

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/whatsapp"
	"github.com/jackc/pgx/v5"
)

// Temporary tables on one private connection inherit the real Django migration.
// The test never sends, updates or claims rows in the deployment tables.
func TestWhatsAppNotificationDelivery(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_WHATSAPP_TEST_DSN")
	if dsn == "" {
		t.Skip("requires a PostgreSQL fixture migrated through communications.0016")
	}
	pgConfig, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	pgConfig.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := database.OpenDB(*pgConfig)
	defer db.Close()
	db.SetMaxOpenConns(1)
	for _, query := range []string{
		"CREATE TEMP TABLE bakney_user (user_id text, phone text)",
		"INSERT INTO bakney_user VALUES ('user','0039 3331234567')",
		"CREATE TEMP TABLE communications_whatsappdelivery (LIKE public.communications_whatsappdelivery INCLUDING ALL)",
		"CREATE TEMP TABLE communications_whatsappreceipt (LIKE public.communications_whatsappreceipt INCLUDING ALL)",
	} {
		if _, err := db.Exec(query); err != nil {
			t.Fatal(err)
		}
	}
	type graphRequest struct {
		To, Type string
		Ref      string `json:"biz_opaque_callback_data"`
		Template struct {
			Name       string
			Language   struct{ Code string }
			Components []struct{ Parameters []struct{ Text string } }
		}
	}
	observed := make(chan graphRequest, 4)
	var status atomic.Int32
	status.Store(200)
	graph := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v23.0/phone/messages" {
			t.Error(r.URL.Path)
		}
		var payload graphRequest
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		observed <- payload
		code := int(status.Load())
		w.WriteHeader(code)
		if code == 200 {
			fmt.Fprint(w, `{"messages":[{"id":"wamid.example"}]}`)
		} else {
			fmt.Fprint(w, `{"error":{"code":42,"message":"busy"}}`)
		}
	}))
	defer graph.Close()
	cfg := config.WhatsAppSettings{PhoneID: "phone", AccountID: "account", AccessToken: "token", AppSecret: "fixture-secret",
		APIVersion: "v23.0", BaseURL: graph.URL, Routes: `{"payment":"receipt/en_US/message"}`}
	client := whatsapp.NewCloudAPI(cfg)
	queue := whatsapp.NewQueue(db, cfg, client)
	notifier := NewSubscriptionNotifier(&paidFlowRows{}, newPaidFlowRedis(), &paidFlowLayer{}, time.Now).WithNotificationChannel(queue.Enqueue)
	ctx := context.Background()
	trigger := func() {
		t.Helper()
		if err := notifier.DeliverValidatedAssociation(ctx, "user", "payment", "Payment received", nil, false); err != nil {
			t.Fatal(err)
		}
	}
	trigger()
	var id, state string
	if err := db.QueryRow("SELECT id,status FROM communications_whatsappdelivery").Scan(&id, &state); err != nil || state != "queued" {
		t.Fatal(state, err)
	}
	if worked, err := queue.DeliverOne(ctx); err != nil || !worked {
		t.Fatal("send", worked, err)
	}
	payload := <-observed
	if payload.Ref != id || payload.To != "393331234567" || payload.Type != "template" || payload.Template.Name != "receipt" ||
		payload.Template.Language.Code != "en_US" || len(payload.Template.Components) != 1 || len(payload.Template.Components[0].Parameters) != 1 ||
		payload.Template.Components[0].Parameters[0].Text != "Payment received" {
		t.Fatalf("wrong template: %+v", payload)
	}
	var providerID string
	var attempts int
	if err := db.QueryRow("SELECT status,provider_message_id,attempts FROM communications_whatsappdelivery WHERE id=$1", id).Scan(&state, &providerID, &attempts); err != nil || state != "sent" || providerID != "wamid.example" || attempts != 1 {
		t.Fatal(state, providerID, attempts, err)
	}
	webhook := whatsapp.Webhook(db, client)
	for _, event := range []string{"read", "delivered", "read", "sent"} {
		body := []byte(fmt.Sprintf(`{"object":"whatsapp_business_account","entry":[{"id":"account","changes":[{"field":"messages","value":{"metadata":{"phone_number_id":"phone"},"statuses":[{"id":"wamid.example","status":"%s","timestamp":"1700000000"}]}}]}]}`, event))
		mac := hmac.New(sha256.New, []byte(cfg.AppSecret))
		mac.Write(body)
		req := httptest.NewRequest("POST", "/communications/whatsapp/webhook", bytes.NewReader(body))
		req.Header.Set("X-Hub-Signature-256", "sha256="+hex.EncodeToString(mac.Sum(nil)))
		res := httptest.NewRecorder()
		webhook.ServeHTTP(res, req)
		if res.Code != 200 {
			t.Fatal(res.Code)
		}
	}
	var count int
	if err := db.QueryRow("SELECT status,(SELECT count(*) FROM communications_whatsappreceipt) FROM communications_whatsappdelivery WHERE id=$1", id).Scan(&state, &count); err != nil || state != "read" || count != 3 {
		t.Fatal(state, count, err)
	}
	trigger()
	for attempt := 1; attempt <= 3; attempt++ {
		status.Store(429)
		if worked, err := queue.DeliverOne(ctx); err != nil || !worked {
			t.Fatal("retry", worked, err)
		}
		row := <-observed
		var delay float64
		var code string
		err := db.QueryRow("SELECT status,attempts,error_code,extract(epoch FROM next_attempt_at-updated_at) FROM communications_whatsappdelivery WHERE id=$1", row.Ref).Scan(&state, &attempts, &code, &delay)
		if err != nil || attempts != attempt || code != "42" {
			t.Fatal(attempts, code, err)
		}
		if attempt == 3 {
			if state != "failed" {
				t.Fatal(state)
			}
			break
		}
		if state != "queued" || delay < float64(30*attempt)-1 {
			t.Fatal(state, delay)
		}
		if worked, err := queue.DeliverOne(ctx); err != nil || worked {
			t.Fatal("premature retry", worked, err)
		}
		if _, err := db.Exec("UPDATE communications_whatsappdelivery SET next_attempt_at=now() WHERE id=$1", row.Ref); err != nil {
			t.Fatal(err)
		}
	}
}

// A channel failure must not prevent the existing realtime push or fail its caller.
func TestNotificationChannelFailureDoesNotFailTrigger(t *testing.T) {
	layer := &paidFlowLayer{}
	notifier := NewSubscriptionNotifier(&paidFlowRows{}, newPaidFlowRedis(), layer, time.Now).
		WithNotificationChannel(func(context.Context, string, string, string) error { return sql.ErrConnDone })
	if err := notifier.DeliverValidatedAssociation(context.Background(), "user", "payment", "received", nil, false); err != nil {
		t.Fatal(err)
	}
	if len(layer.messages) != 1 {
		t.Fatal("missing realtime notification")
	}
}
