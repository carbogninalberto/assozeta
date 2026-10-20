// Package jwt preserves Assozeta's EdDSA/SimpleJWT token protocol. It verifies
// cryptographic credentials only: account status, roles, scopes and impersonation
// must still be resolved against trusted persisted state by the service layer.
package jwt

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"math"
	"strconv"
	"strings"
	"time"

	libjwt "github.com/golang-jwt/jwt/v5"
)

var ErrInvalidToken = errors.New("invalid or expired token")
var ErrInvalidKey = errors.New("invalid Ed25519 signing or verification key")

const AccessLifetime = 4 * time.Hour
const RefreshLifetime = 30 * 24 * time.Hour

type Pair struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
	ExpiresIn    int64  `json:"expires_in"`
	TokenType    string `json:"token_type"`
}

type Manager struct {
	private ed25519.PrivateKey
	public  ed25519.PublicKey
	now     func() time.Time
}

func New(privatePEM, publicPEM []byte, now func() time.Time) (*Manager, error) {
	privateBlock, _ := pem.Decode(privatePEM)
	publicBlock, _ := pem.Decode(publicPEM)
	if privateBlock == nil || publicBlock == nil || now == nil {
		return nil, ErrInvalidKey
	}
	privateValue, err := x509.ParsePKCS8PrivateKey(privateBlock.Bytes)
	if err != nil {
		return nil, ErrInvalidKey
	}
	publicValue, err := x509.ParsePKIXPublicKey(publicBlock.Bytes)
	if err != nil {
		return nil, ErrInvalidKey
	}
	private, ok := privateValue.(ed25519.PrivateKey)
	if !ok {
		return nil, ErrInvalidKey
	}
	public, ok := publicValue.(ed25519.PublicKey)
	if !ok || !private.Public().(ed25519.PublicKey).Equal(public) {
		return nil, ErrInvalidKey
	}
	return &Manager{private: private, public: public, now: now}, nil
}

// Verify intentionally does not enforce iss/aud for REST: SIMPLE_JWT configures
// neither ISSUER nor AUDIENCE. WebSocket authentication adds those checks before
// resolving the user. Unknown claims remain intact and numbers retain precision.
func (m *Manager) Verify(encoded, kind string) (map[string]any, error) {
	if kind != "access" && kind != "refresh" {
		return nil, ErrInvalidToken
	}
	claims := libjwt.MapClaims{}
	token, err := libjwt.ParseWithClaims(encoded, claims, func(token *libjwt.Token) (any, error) { return m.public, nil },
		libjwt.WithValidMethods([]string{"EdDSA"}), libjwt.WithJSONNumber(), libjwt.WithoutClaimsValidation())
	if err != nil || !token.Valid || claims["token_type"] != kind {
		return nil, ErrInvalidToken
	}
	if _, ok := claims["jti"].(string); !ok {
		return nil, ErrInvalidToken
	}
	if subject, exists := claims["sub"]; exists {
		if _, ok := subject.(string); !ok {
			return nil, ErrInvalidToken
		}
	}
	expiry, ok := epoch(claims["exp"], false)
	// SimpleJWT additionally converts exp through Python datetime (year 1..9999).
	if !ok || expiry < -62135596800 || expiry > 253402300799 || expiry <= m.now().Unix() {
		return nil, ErrInvalidToken
	}
	for _, name := range []string{"iat", "nbf"} {
		if value, exists := claims[name]; exists {
			timestamp, ok := epoch(value, true)
			if !ok || timestamp > m.now().Unix() {
				return nil, ErrInvalidToken
			}
		}
	}
	return map[string]any(claims), nil
}

func epoch(value any, allowString bool) (int64, bool) {
	switch number := value.(type) {
	case json.Number:
		if integer, err := number.Int64(); err == nil {
			return integer, true
		}
		decimal, err := number.Float64()
		if err != nil || math.IsNaN(decimal) || math.IsInf(decimal, 0) || decimal >= float64(math.MaxInt64) || decimal < float64(math.MinInt64) {
			return 0, false
		}
		return int64(decimal), true // Python int() truncates fractional NumericDates.
	case string:
		if allowString {
			integer, err := strconv.ParseInt(strings.TrimSpace(number), 10, 64)
			return integer, err == nil
		}
	case bool:
		if number {
			return 1, true
		}
		return 0, true
	}
	return 0, false
}

func (m *Manager) Issue(userID string, role int, email string) (Pair, error) {
	now := m.now().Unix()
	refresh, err := newClaims("refresh", now, int64(RefreshLifetime/time.Second))
	if err != nil {
		return Pair{}, err
	}
	refresh["user_id"] = userID
	refresh["role"] = role
	refresh["email"] = email
	refresh["iss"] = "https://bakney.com"
	refresh["aud"] = "bakney"
	encoded, err := m.sign(refresh)
	if err != nil {
		return Pair{}, err
	}
	return m.pair(encoded, refresh, now)
}
func (m *Manager) Refresh(encoded string) (Pair, error) {
	claims, err := m.Verify(encoded, "refresh")
	if err != nil {
		return Pair{}, err
	}
	// Current Assozeta does not rotate refresh tokens despite its configuration
	// comment. Preserve the valid original credential and its original expiry/JTI.
	return m.pair(encoded, libjwt.MapClaims(claims), m.now().Unix())
}
func (m *Manager) pair(refresh string, claims libjwt.MapClaims, now int64) (Pair, error) {
	access, err := newClaims("access", now, int64(AccessLifetime/time.Second))
	if err != nil {
		return Pair{}, err
	}
	for name, value := range claims {
		switch name {
		case "token_type", "exp", "jti":
			continue
		}
		// SimpleJWT 5.3.1 copies iat as well; it stays at the original refresh issue
		// time while the new access expiry is based on the time of this request.
		access[name] = value
	}
	encoded, err := m.sign(access)
	if err != nil {
		return Pair{}, err
	}
	return Pair{AccessToken: encoded, RefreshToken: refresh, ExpiresIn: int64(AccessLifetime / time.Second), TokenType: "Bearer"}, nil
}
func newClaims(kind string, now, lifetime int64) (libjwt.MapClaims, error) {
	var id [16]byte
	if _, err := rand.Read(id[:]); err != nil {
		return nil, errors.New("cannot generate token identifier")
	}
	id[6] = (id[6] & 0x0f) | 0x40
	id[8] = (id[8] & 0x3f) | 0x80
	return libjwt.MapClaims{"token_type": kind, "exp": now + lifetime, "iat": now, "jti": hex.EncodeToString(id[:])}, nil
}
func (m *Manager) sign(claims libjwt.MapClaims) (string, error) {
	token, err := libjwt.NewWithClaims(libjwt.SigningMethodEdDSA, claims).SignedString(m.private)
	if err != nil {
		return "", errors.New("cannot sign token")
	}
	return token, nil
}
