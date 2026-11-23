// Package whatsapp implements the Cloud API contract in docs/whatsapp.md.
package whatsapp

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
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

func field(object domain.RequestObject, key string, fallback any) any {
	if value, ok := object.Lookup(key); ok {
		return value
	}
	return fallback
}

func stringField(object domain.RequestObject, key string) string {
	text, _ := field(object, key, nil).(string)
	return text
}

// Python's isinstance(code, int) includes booleans but excludes floats and strings.
func integerCode(value any, fallback string) string {
	switch v := value.(type) {
	case bool:
		return dto.PythonStr(v)
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			return dto.PythonStr(v)
		}
	}
	return fallback
}

func (p *CloudAPI) request(ctx context.Context, method, resource, edge string, query url.Values, payload any) (domain.RequestObject, error) {
	if resource == "" || p.config.AccessToken == "" {
		return nil, &ProviderError{"configuration", "Missing WhatsApp resource ID or access token", false}
	}
	address := strings.TrimRight(p.config.BaseURL, "/") + "/" + p.config.APIVersion + "/" + url.PathEscape(resource) + "/" + edge
	if len(query) > 0 {
		address += "?" + query.Encode()
	}
	var body io.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, address, body)
	if err != nil {
		return nil, &ProviderError{"configuration", "Invalid WhatsApp API URL", false}
	}
	req.Header.Set("Authorization", "Bearer "+p.config.AccessToken)
	req.Header.Set("Content-Type", "application/json")
	res, err := p.client.Do(req)
	if err != nil {
		return nil, &ProviderError{"network", "WhatsApp connection failed", true}
	}
	defer res.Body.Close()
	const maxReply = 4 << 20
	raw, err := io.ReadAll(io.LimitReader(res.Body, maxReply+1))
	if err != nil {
		return nil, &ProviderError{"network", "WhatsApp connection failed", true}
	}
	var data domain.RequestObject
	if len(raw) <= maxReply {
		value, err := dto.ParsePythonJSONBytes(raw)
		if err == nil {
			data, _ = value.(domain.RequestObject)
		}
	}
	if res.StatusCode < 200 || res.StatusCode >= 300 {
		detail, _ := field(data, "error", nil).(domain.RequestObject)
		code := integerCode(field(detail, "code", nil), strconv.Itoa(res.StatusCode))
		message := dto.PythonStr(field(detail, "message", "WhatsApp API request failed"))
		return nil, &ProviderError{code, p.safeMessage(message), res.StatusCode == 429 || res.StatusCode >= 500}
	}
	if data == nil {
		return nil, &ProviderError{"invalid_response", "Invalid WhatsApp API response", false}
	}
	return data, nil
}

func (p *CloudAPI) send(ctx context.Context, recipient, kind string, content Object, reference string) (string, error) {
	payload := Object{"messaging_product": "whatsapp", "to": strings.TrimLeft(recipient, "+"), "type": kind, kind: content, "biz_opaque_callback_data": reference}
	response, err := p.request(ctx, http.MethodPost, p.config.PhoneID, "messages", nil, payload)
	if err != nil {
		return "", err
	}
	messages, _ := field(response, "messages", nil).([]any)
	if len(messages) > 0 {
		message, _ := messages[0].(domain.RequestObject)
		if id := stringField(message, "id"); id != "" {
			return id, nil
		}
	}
	return "", &ProviderError{"invalid_response", "Missing WhatsApp message ID", false}
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

// Template operations retain the provider's key order for the management command.
func (p *CloudAPI) RegisterTemplateDocument(ctx context.Context, definition domain.RequestObject) (domain.RequestObject, error) {
	return p.request(ctx, http.MethodPost, p.config.AccountID, "message_templates", nil, json.RawMessage(dto.PythonJSONDumps(definition)))
}

func (p *CloudAPI) ListTemplateDocuments(ctx context.Context) ([]any, error) {
	rows := []any{}
	query := url.Values{"status": {`["APPROVED"]`}, "fields": {"id,name,language,status,components"}}
	seen := map[string]bool{}
	for {
		page, err := p.request(ctx, http.MethodGet, p.config.AccountID, "message_templates", query, nil)
		if err != nil {
			return nil, err
		}
		data, ok := field(page, "data", nil).([]any)
		if !ok {
			return nil, &ProviderError{"invalid_response", "Missing WhatsApp template list", false}
		}
		for _, item := range data {
			template, _ := item.(domain.RequestObject)
			if stringField(template, "status") == "APPROVED" {
				rows = append(rows, template)
			}
		}
		value := field(page, "paging", nil)
		if !dto.PythonTruthy(value) {
			return rows, nil
		}
		paging, ok := value.(domain.RequestObject)
		if !ok {
			return nil, &ProviderError{"invalid_response", "Invalid WhatsApp template page", false}
		}
		if !dto.PythonTruthy(field(paging, "next", nil)) {
			return rows, nil
		}
		cursors, _ := field(paging, "cursors", nil).(domain.RequestObject)
		cursor := stringField(cursors, "after")
		if cursor == "" || seen[cursor] {
			return nil, &ProviderError{"invalid_response", "Invalid WhatsApp template cursor", false}
		}
		seen[cursor] = true
		query.Set("after", cursor)
	}
}

func (p *CloudAPI) GetTemplateDocument(ctx context.Context, name, language string) (domain.RequestObject, error) {
	rows, err := p.ListTemplateDocuments(ctx)
	if err != nil {
		return nil, err
	}
	for _, row := range rows {
		template := row.(domain.RequestObject)
		if field(template, "name", nil) == name && field(template, "language", nil) == language {
			return template, nil
		}
	}
	return nil, &ProviderError{"not_found", "Approved template not found", false}
}

// The programmatic Provider API exposes ordinary Go maps.
func templateMaps[T Object | []Object](value any, err error) (T, error) {
	var result T
	if err == nil {
		err = json.Unmarshal(dto.PythonJSONDumps(value), &result)
	}
	return result, err
}

func (p *CloudAPI) RegisterTemplate(ctx context.Context, definition Object) (Object, error) {
	return templateMaps[Object](p.request(ctx, http.MethodPost, p.config.AccountID, "message_templates", nil, definition))
}

func (p *CloudAPI) ListTemplates(ctx context.Context) ([]Object, error) {
	return templateMaps[[]Object](p.ListTemplateDocuments(ctx))
}

func (p *CloudAPI) GetTemplate(ctx context.Context, name, language string) (Object, error) {
	return templateMaps[Object](p.GetTemplateDocument(ctx, name, language))
}
