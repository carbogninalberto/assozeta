package service

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type SessionAuthentication struct {
	sessions domain.DjangoSessions
	users    domain.SessionUsers
	secret   string
}

func NewSessionAuthentication(sessions domain.DjangoSessions, users domain.SessionUsers, secret string) *SessionAuthentication {
	return &SessionAuthentication{sessions: sessions, users: users, secret: secret}
}

// Resolve reproduces Django's pre-view cache-session identity. Missing/broken
// cache loads become an empty anonymous session; database and deletion failures
// propagate. A valid session can attribute audits without authenticating the API.
func (s *SessionAuthentication) Resolve(ctx context.Context, key string) (domain.SessionIdentity, error) {
	if err := ctx.Err(); err != nil {
		return domain.SessionIdentity{}, err
	}
	if !utf8.ValidString(key) || utf8.RuneCountInString(key) < 8 {
		return domain.SessionIdentity{Empty: true}, nil
	}
	value, err := s.sessions.LoadSession(ctx, key)
	if err := ctx.Err(); err != nil {
		return domain.SessionIdentity{}, err
	}
	if err != nil || value == nil {
		return domain.SessionIdentity{Empty: true}, nil
	}
	data, ok := value.(map[string]any)
	if !ok {
		return domain.SessionIdentity{}, domain.ErrInvalidCachedSession
	}
	rawID, present := data["_auth_user_id"]
	if !present {
		return domain.SessionIdentity{}, nil
	}
	var id string
	if rawID != nil {
		id, _, err = referenceUUID(rawID)
		if err != nil {
			return domain.SessionIdentity{}, domain.ErrInvalidCachedSession
		}
	}
	backend, known := data["_auth_user_backend"].(string)
	if !known {
		return domain.SessionIdentity{}, nil
	}
	standard := false
	switch backend {
	case "django.contrib.auth.backends.ModelBackend":
		standard = true
	case "social_core.backends.google.GoogleOAuth2", "social_core.backends.apple.AppleIdAuth", "social_core.backends.orcid.ORCIDOAuth2":
	default:
		return domain.SessionIdentity{}, nil
	}
	// A primary UUID cannot be NULL; avoid a lookup whose result is known empty.
	if rawID == nil {
		return domain.SessionIdentity{}, nil
	}
	user, err := s.users.SessionUser(ctx, id)
	if err := ctx.Err(); err != nil {
		return domain.SessionIdentity{}, err
	}
	if errors.Is(err, domain.ErrAccountNotFound) {
		return domain.SessionIdentity{}, nil
	}
	if err != nil {
		return domain.SessionIdentity{}, err
	}
	if standard && !user.Active {
		return domain.SessionIdentity{}, nil
	}
	hash := data["_auth_user_hash"]
	if err := sessionHashRepresentation(ctx, hash); err != nil {
		return domain.SessionIdentity{}, err
	}
	var supplied []byte
	switch raw := hash.(type) {
	case []byte:
		supplied = raw
	case string:
		supplied = []byte(raw)
	case json.Number:
		supplied = []byte(raw)
	}
	expected := sessionPasswordHash(s.secret, user.Password)
	user.Password = ""
	if !hmac.Equal(supplied, []byte(expected)) {
		if err := s.sessions.DeleteSession(ctx, key); err != nil {
			return domain.SessionIdentity{}, err
		}
		if err := ctx.Err(); err != nil {
			return domain.SessionIdentity{}, err
		}
		return domain.SessionIdentity{Empty: true}, nil
	}
	if err := ctx.Err(); err != nil {
		return domain.SessionIdentity{}, err
	}
	return domain.SessionIdentity{Actor: &domain.SessionActor{ID: user.ID, Email: user.Email}}, nil
}

// Python's force_bytes calls str() for non-string hashes. Only an integer's
// decimal representation could equal a 64-character hex digest; other primitive
// representations cannot match. Still preserve conversion errors: large integers
// raise even inside a container, and must not turn into session deletion.
func sessionHashRepresentation(ctx context.Context, value any) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	switch current := value.(type) {
	case json.Number:
		if len(strings.TrimPrefix(string(current), "-")) > 4300 {
			return domain.ErrInvalidCachedSession
		}
	case []any:
		for _, item := range current {
			if err := sessionHashRepresentation(ctx, item); err != nil {
				return err
			}
		}
	case map[string]any:
		for _, item := range current {
			if err := sessionHashRepresentation(ctx, item); err != nil {
				return err
			}
		}
	}
	return nil
}

func sessionPasswordHash(secret, password string) string {
	key := sha256.Sum256([]byte("django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash" + secret))
	mac := hmac.New(sha256.New, key[:])
	mac.Write([]byte(password))
	return hex.EncodeToString(mac.Sum(nil))
}
