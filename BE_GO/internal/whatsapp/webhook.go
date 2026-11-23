package whatsapp

import (
	"crypto/hmac"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type Status struct {
	ID, Reference, State, Code, Message string
	At                                  time.Time
}

var denied = errors.New("invalid WhatsApp signature")

func (p *CloudAPI) VerifyWebhook(mode, token, challenge string) (string, error) {
	if mode != "subscribe" || challenge == "" || p.config.VerifyToken == "" || !hmac.Equal([]byte(token), []byte(p.config.VerifyToken)) {
		return "", denied
	}
	return challenge, nil
}

func (p *CloudAPI) ParseStatusCallback(body []byte, signature string) ([]Status, error) {
	mac := hmac.New(sha256.New, []byte(p.config.AppSecret))
	mac.Write(body)
	if p.config.AppSecret == "" || !hmac.Equal([]byte(signature), []byte("sha256="+hex.EncodeToString(mac.Sum(nil)))) {
		return nil, denied
	}
	value, err := dto.ParsePythonJSONBytes(body)
	if err != nil {
		return nil, err
	}
	envelope, _ := value.(domain.RequestObject)
	kind, present := envelope.Lookup("object")
	if !present {
		return nil, invalidStatus
	}
	if kind != "whatsapp_business_account" {
		return nil, nil
	}
	entries, ok := iterableField(envelope, "entry")
	if !ok {
		return nil, invalidStatus
	}
	var result []Status
	for _, raw := range entries {
		if p.config.AccountID == "" {
			break
		}
		entry, ok := raw.(domain.RequestObject)
		if !ok {
			return nil, invalidStatus
		}
		if field(entry, "id", nil) != p.config.AccountID {
			continue
		}
		changes, ok := iterableField(entry, "changes")
		if !ok {
			return nil, invalidStatus
		}
		for _, change := range changes {
			events, err := p.changeStatuses(change)
			if err != nil {
				return nil, err
			}
			result = append(result, events...)
		}
	}
	return result, nil
}

var invalidStatus = errors.New("invalid WhatsApp status")

// Missing collections default to empty. Explicit null is not iterable.
func iterableField(object domain.RequestObject, name string) ([]any, bool) {
	return dto.PythonIterate(field(object, name, []any{}))
}

func (p *CloudAPI) changeStatuses(raw any) ([]Status, error) {
	change, ok := raw.(domain.RequestObject)
	if !ok {
		return nil, invalidStatus
	}
	value, ok := field(change, "value", domain.RequestObject{}).(domain.RequestObject)
	if !ok {
		return nil, invalidStatus
	}
	metadata, ok := field(value, "metadata", domain.RequestObject{}).(domain.RequestObject)
	if !ok {
		return nil, invalidStatus
	}
	if field(change, "field", nil) != "messages" || p.config.PhoneID == "" || field(metadata, "phone_number_id", nil) != p.config.PhoneID {
		return nil, nil
	}
	items, ok := iterableField(value, "statuses")
	if !ok {
		return nil, invalidStatus
	}
	var statuses []Status
	for _, raw := range items {
		item, ok := raw.(domain.RequestObject)
		if !ok {
			return nil, invalidStatus
		}
		state, _ := field(item, "status", nil).(string)
		if rank[state] == 0 {
			continue
		}
		id, _ := field(item, "id", nil).(string)
		stamp, parsed := dto.PythonInt(field(item, "timestamp", nil))
		if id == "" || parsed != dto.PythonIntOK || !stamp.IsInt64() {
			return nil, invalidStatus
		}
		at := time.Unix(stamp.Int64(), 0).UTC()
		if at.Year() < 1 || at.Year() > 9999 {
			return nil, invalidStatus
		}
		var detail domain.RequestObject
		if details := field(item, "errors", nil); dto.PythonTruthy(details) {
			list, ok := details.([]any)
			if !ok {
				return nil, invalidStatus
			}
			detail, ok = list[0].(domain.RequestObject)
			if !ok {
				return nil, invalidStatus
			}
		}
		message := field(detail, "message", nil)
		if !dto.PythonTruthy(message) {
			message = field(detail, "title", "")
		}
		statuses = append(statuses, Status{
			ID: id, Reference: dto.PythonStr(field(item, "biz_opaque_callback_data", nil)), State: state, At: at,
			Code: integerCode(field(detail, "code", nil), ""), Message: p.safeMessage(dto.PythonStr(message)),
		})
	}
	return statuses, nil
}

func Webhook(db *sql.DB, provider Provider) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		if r.Method == http.MethodGet {
			q := dto.ParseQueryPairs(r.URL.RawQuery, false)
			challenge, err := provider.VerifyWebhook(stringField(q, "hub.mode"), stringField(q, "hub.verify_token"), stringField(q, "hub.challenge"))
			if err != nil {
				w.WriteHeader(403)
				return
			}
			w.Header().Set("Content-Type", "text/plain")
			io.WriteString(w, challenge)
			return
		}
		if r.Method != http.MethodPost {
			w.Header().Set("Allow", "GET, POST")
			w.WriteHeader(405)
			return
		}
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 2<<20))
		if err != nil {
			w.WriteHeader(400)
			return
		}
		events, err := provider.ParseStatusCallback(body, r.Header.Get("X-Hub-Signature-256"))
		if errors.Is(err, denied) {
			w.WriteHeader(403)
			return
		}
		if err != nil {
			w.WriteHeader(400)
			return
		}
		for _, event := range events {
			if err := recordStatus(r.Context(), db, event); err != nil {
				w.WriteHeader(500)
				return
			}
		}
		w.WriteHeader(200)
	}
}
