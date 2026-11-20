package service

import (
	"bytes"
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type whatsAppSettingsStore struct {
	InstanceAdminStore
	row *domain.InstanceAdminRow
}

func (s whatsAppSettingsStore) Read(context.Context) (*domain.InstanceAdminRow, error) {
	return s.row, nil
}

func (s whatsAppSettingsStore) Locked(_ context.Context, update func(*domain.InstanceAdminRow) (*domain.InstanceAdminUpdate, error)) error {
	_, err := update(s.row)
	return err
}

func TestWhatsAppOwnerSettings(t *testing.T) {
	ctx := context.Background()
	store := whatsAppSettingsStore{row: &domain.InstanceAdminRow{SelfHosted: true, IntegrationSettings: json.RawMessage("{}"), DiagnosticResults: json.RawMessage("{}")}}
	env := InstanceAdminEnvironment{SecretKeys: []string{"fixture-only"}, WhatsApp: config.WhatsAppSettings{Routes: "{}", APIVersion: "v23.0"}}
	admin := NewInstanceAdmin(store, nil, env, time.Now)
	fields := domain.RequestObject{
		{Name: "revision", Value: json.Number("0")}, {Name: "enabled", Value: true},
		{Name: "phone_number_id", Value: "12345"}, {Name: "business_account_id", Value: "67890"},
		{Name: "notification_routes", Value: `{"payment":"receipt/it/message"}`},
	}
	if _, err := admin.PutIntegration(ctx, "whatsapp", fields); err == nil {
		t.Fatal("enabled without credentials")
	}
	withSecrets := append(fields, domain.RequestField{Name: "access_token", Value: "fixture-access"},
		domain.RequestField{Name: "app_secret", Value: "fixture-app"}, domain.RequestField{Name: "webhook_verify_token", Value: "fixture-verify"})
	reply, err := admin.PutIntegration(ctx, "whatsapp", withSecrets)
	if err != nil {
		t.Fatal(err)
	}
	if reply["access_token"] != nil || reply["access_token_configured"] != true || bytes.Contains(store.row.IntegrationSettings, []byte("fixture-")) {
		t.Fatal("credentials must be write-only and encrypted")
	}
	effective, err := admin.WhatsAppSettings(ctx)
	if err != nil || effective.Disabled || effective.AccessToken != "fixture-access" || effective.PhoneID != "12345" || effective.Routes != fields[4].Value {
		t.Fatal("saved sender or route unavailable", err)
	}
	fields[1].Value = false
	if _, err := admin.PutIntegration(ctx, "whatsapp", fields); err == nil {
		t.Fatal("accepted stale revision")
	}
	fields[0].Value = json.Number("1")
	if _, err := admin.PutIntegration(ctx, "whatsapp", fields); err != nil {
		t.Fatal(err)
	}
	effective, err = admin.WhatsAppSettings(ctx)
	if err != nil || !effective.Disabled || effective.AccessToken != "fixture-access" {
		t.Fatal("disable or retain failed", err)
	}
	if _, err := admin.DeleteIntegration(ctx, "whatsapp", domain.RequestObject{{Name: "revision", Value: json.Number("2")}}); err != nil {
		t.Fatal(err)
	}
	effective, err = admin.WhatsAppSettings(ctx)
	if err != nil || effective != env.WhatsApp {
		t.Fatal("reset did not restore deployment configuration", err)
	}
}
