// Package whatsapp implements the Cloud API contract in docs/whatsapp.md.
package whatsapp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
)

type Provider interface {
	SendText(ctx context.Context, recipient, body, reference string) (string, error)
	SendTemplate(ctx context.Context, recipient, name, language, parameter, reference string) (string, error)
	RegisterTemplate(ctx context.Context, definition Object) (Object, error)
	ListTemplates(ctx context.Context) ([]Object, error)
	GetTemplate(ctx context.Context, name, language string) (Object, error)
	VerifyWebhook(mode, token, challenge string) (string, error)
	ParseStatusCallback(body []byte, signature string) ([]Status, error)
}

type Object = map[string]any

type ProviderError struct {
	Code, Message string
	Transient     bool
}

func (e *ProviderError) Error() string { return e.Code + ": " + e.Message }

type CloudAPI struct {
	config config.WhatsAppSettings
	client *http.Client
}

func NewCloudAPI(cfg config.WhatsAppSettings) *CloudAPI {
	return &CloudAPI{cfg, &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
}

func (p *CloudAPI) safeMessage(message string) string {
	for _, secret := range []string{p.config.AccessToken, p.config.AppSecret, p.config.VerifyToken} {
		if secret != "" {
			message = strings.ReplaceAll(message, secret, "[redacted]")
		}
	}
	runes := []rune(message)
	if len(runes) > 1000 {
		return string(runes[:1000])
	}
	return message
}

func (p *CloudAPI) request(ctx context.Context, method, resource, edge string, query url.Values, payload Object, out any) error {
	if resource == "" || p.config.AccessToken == "" {
		return &ProviderError{"configuration", "Missing WhatsApp resource ID or access token", false}
	}
	address := strings.TrimRight(p.config.BaseURL, "/") + "/" + p.config.APIVersion + "/" + url.PathEscape(resource) + "/" + edge
	if len(query) > 0 {
		address += "?" + query.Encode()
	}
	var body io.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, address, body)
	if err != nil {
		return &ProviderError{"configuration", "Invalid WhatsApp API URL", false}
	}
	req.Header.Set("Authorization", "Bearer "+p.config.AccessToken)
	req.Header.Set("Content-Type", "application/json")
	res, err := p.client.Do(req)
	if err != nil {
		return &ProviderError{"network", "WhatsApp connection failed", true}
	}
	defer res.Body.Close()
	decoder := json.NewDecoder(io.LimitReader(res.Body, 4<<20))
	if res.StatusCode < 200 || res.StatusCode >= 300 {
		var failure struct {
			Error struct {
				Code    json.Number
				Message string
			}
		}
		_ = decoder.Decode(&failure)
		code, message := failure.Error.Code.String(), p.safeMessage(failure.Error.Message)
		if code == "" {
			code = fmt.Sprint(res.StatusCode)
		}
		if message == "" {
			message = "WhatsApp API request failed"
		}
		return &ProviderError{code, message, res.StatusCode == 429 || res.StatusCode >= 500}
	}
	if err := decoder.Decode(out); err != nil {
		var networkErr net.Error
		if errors.As(err, &networkErr) || errors.Is(err, io.ErrUnexpectedEOF) {
			return &ProviderError{"network", "WhatsApp connection failed", true}
		}
		return &ProviderError{"invalid_response", "Invalid WhatsApp API response", false}
	}
	return nil
}

func (p *CloudAPI) send(ctx context.Context, recipient, kind string, content Object, reference string) (string, error) {
	payload := Object{"messaging_product": "whatsapp", "to": strings.TrimPrefix(recipient, "+"), "type": kind, kind: content, "biz_opaque_callback_data": reference}
	var response struct{ Messages []struct{ ID string } }
	if err := p.request(ctx, http.MethodPost, p.config.PhoneID, "messages", nil, payload, &response); err != nil {
		return "", err
	}
	if len(response.Messages) == 0 || response.Messages[0].ID == "" {
		return "", &ProviderError{"invalid_response", "Missing WhatsApp message ID", false}
	}
	return response.Messages[0].ID, nil
}
func (p *CloudAPI) SendText(ctx context.Context, recipient, body, reference string) (string, error) {
	return p.send(ctx, recipient, "text", Object{"body": body}, reference)
}
func (p *CloudAPI) SendTemplate(ctx context.Context, recipient, name, language, parameter, reference string) (string, error) {
	content := Object{"name": name, "language": Object{"code": language}}
	if parameter != "" {
		content["components"] = []Object{{"type": "body", "parameters": []Object{{"type": "text", "text": parameter}}}}
	}
	return p.send(ctx, recipient, "template", content, reference)
}

func (p *CloudAPI) RegisterTemplate(ctx context.Context, definition Object) (Object, error) {
	var result Object
	err := p.request(ctx, http.MethodPost, p.config.AccountID, "message_templates", nil, definition, &result)
	return result, err
}

func (p *CloudAPI) ListTemplates(ctx context.Context) ([]Object, error) {
	rows := []Object{}
	query := url.Values{"status": {`["APPROVED"]`}, "fields": {"id,name,language,status,components"}}
	seen := map[string]bool{}
	for {
		var page struct {
			Data   []Object
			Paging struct {
				Next    string
				Cursors struct{ After string }
			}
		}
		if err := p.request(ctx, http.MethodGet, p.config.AccountID, "message_templates", query, nil, &page); err != nil {
			return nil, err
		}
		if page.Data == nil {
			return nil, &ProviderError{"invalid_response", "Missing WhatsApp template list", false}
		}
		for _, t := range page.Data {
			if t["status"] == "APPROVED" {
				rows = append(rows, t)
			}
		}
		if page.Paging.Next == "" {
			return rows, nil
		}
		cursor := page.Paging.Cursors.After
		if cursor == "" || seen[cursor] {
			return nil, &ProviderError{"invalid_response", "Invalid WhatsApp template cursor", false}
		}
		seen[cursor] = true
		query.Set("after", cursor)
	}
}

func (p *CloudAPI) GetTemplate(ctx context.Context, name, language string) (Object, error) {
	rows, err := p.ListTemplates(ctx)
	if err != nil {
		return nil, err
	}
	for _, row := range rows {
		if row["name"] == name && row["language"] == language {
			return row, nil
		}
	}
	return nil, &ProviderError{"not_found", "Approved template not found", false}
}
