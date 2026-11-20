package whatsapp

import (
	"crypto/hmac"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"time"
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
	var envelope struct {
		Object string
		Entry  []struct {
			ID      string
			Changes []struct {
				Field string
				Value struct {
					Metadata struct {
						Phone string `json:"phone_number_id"`
					}
					Statuses []struct {
						ID, Status, Timestamp string
						Reference             string `json:"biz_opaque_callback_data"`
						Errors                []struct {
							Code           json.Number
							Message, Title string
						}
					}
				}
			}
		}
	}
	if err := json.Unmarshal(body, &envelope); err != nil {
		return nil, err
	}
	if envelope.Object == "" {
		return nil, errors.New("missing webhook object")
	}
	var result []Status
	if envelope.Object != "whatsapp_business_account" {
		return result, nil
	}
	for _, entry := range envelope.Entry {
		if entry.ID == "" || entry.ID != p.config.AccountID {
			continue
		}
		for _, change := range entry.Changes {
			if change.Field != "messages" || change.Value.Metadata.Phone == "" || change.Value.Metadata.Phone != p.config.PhoneID {
				continue
			}
			for _, item := range change.Value.Statuses {
				if rank[item.Status] == 0 {
					continue
				}
				seconds, err := strconv.ParseInt(item.Timestamp, 10, 64)
				if err != nil || item.ID == "" || seconds < -62135596800 || seconds > 253402300799 {
					return nil, errors.New("invalid WhatsApp status")
				}
				status := Status{ID: item.ID, Reference: item.Reference, State: item.Status, At: time.Unix(seconds, 0).UTC()}
				if len(item.Errors) > 0 {
					detail := item.Errors[0]
					status.Code = detail.Code.String()
					status.Message = p.safeMessage(detail.Message)
					if status.Message == "" {
						status.Message = p.safeMessage(detail.Title)
					}
				}
				result = append(result, status)
			}
		}
	}
	return result, nil
}

func Webhook(db *sql.DB, provider Provider) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			q := r.URL.Query()
			challenge, err := provider.VerifyWebhook(q.Get("hub.mode"), q.Get("hub.verify_token"), q.Get("hub.challenge"))
			if err != nil {
				w.WriteHeader(403)
				return
			}
			w.Header().Set("Content-Type", "text/plain")
			io.WriteString(w, challenge)
			return
		}
		if r.Method != http.MethodPost {
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
