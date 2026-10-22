// Package jwt preserves Assozeta's EdDSA/SimpleJWT token protocol. It verifies
// cryptographic credentials only: account status, roles, scopes and impersonation
// must still be resolved against trusted persisted state by the service layer.
package jwt

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"strings"
	"time"

	libjwt "github.com/golang-jwt/jwt/v5"
)

var ErrInvalidToken = errors.New("invalid or expired token")
var ErrExpiredToken = fmt.Errorf("expired token: %w", ErrInvalidToken)
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
	parser := libjwt.NewParser(libjwt.WithValidMethods([]string{"EdDSA"}), libjwt.WithJSONNumber(), libjwt.WithPaddingAllowed(), libjwt.WithoutClaimsValidation())
	segments := strings.Split(encoded, ".")
	if len(segments) != 3 {
		return nil, ErrInvalidToken
	}
	for _, segment := range segments[:2] {
		data, err := parser.DecodeSegment(segment)
		if err != nil || !ValidDocument(context.Background(), data) {
			return nil, ErrInvalidToken
		}
	}
	claims := libjwt.MapClaims{}
	token, err := parser.ParseWithClaims(encoded, claims, func(token *libjwt.Token) (any, error) {
		if !ValidProtectedHeader(token.Header) {
			return nil, ErrInvalidToken
		}
		return m.public, nil
	})
	if err != nil || !token.Valid {
		return nil, ErrInvalidToken
	}
	// PyJWT validates iat and nbf before exp. SimpleJWT 5.5 distinguishes
	// expiry, but a bad signature or a future iat/nbf must remain invalid.
	now := m.now().Unix()
	for _, name := range []string{"iat", "nbf"} {
		if value, exists := claims[name]; exists {
			timestamp, ok := IntegerClaim(value)
			if !ok || timestamp.Cmp(big.NewInt(now)) > 0 {
				return nil, ErrInvalidToken
			}
		}
	}
	if expiry, ok := IntegerClaim(claims["exp"]); ok && expiry.Cmp(big.NewInt(now)) <= 0 {
		return nil, ErrExpiredToken
	}
	expiry, ok := epoch(claims["exp"])
	// SimpleJWT additionally converts exp through Python datetime (year 1..9999).
	if !ok || expiry < -62135596800 || expiry > 253402300799 {
		return nil, ErrInvalidToken
	}
	if subject, exists := claims["sub"]; exists {
		if _, ok := subject.(string); !ok {
			return nil, ErrInvalidToken
		}
	}
	if _, ok := claims["jti"].(string); !ok {
		return nil, ErrInvalidToken
	}
	if claims["token_type"] != kind {
		return nil, ErrInvalidToken
	}
	return map[string]any(claims), nil
}

func epoch(value any) (int64, bool) {
	// SimpleJWT additionally passes exp to datetime; strings cannot reach it.
	if _, isString := value.(string); isString {
		return 0, false
	}
	integer, ok := IntegerClaim(value)
	if !ok || !integer.IsInt64() {
		return 0, false
	}
	return integer.Int64(), true
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
	// Django re-encodes the same refresh claims; expiry and JTI do not rotate.
	refresh, err := m.resignRefresh(encoded)
	if err != nil {
		return Pair{}, err
	}
	return m.pair(refresh, libjwt.MapClaims(claims), m.now().Unix())
}

// IssueUnbound matches constructing SimpleJWT RefreshToken(None). It deliberately
// supplies no identity/custom claims; actor authentication rejects its access.
func (m *Manager) IssueUnbound() (Pair, error) {
	now := m.now().Unix()
	claims, err := newClaims("refresh", now, int64(RefreshLifetime/time.Second))
	if err != nil {
		return Pair{}, err
	}
	encoded, err := m.sign(claims)
	if err != nil {
		return Pair{}, err
	}
	return m.pair(encoded, claims, now)
}
func (m *Manager) pair(refresh string, claims libjwt.MapClaims, now int64) (Pair, error) {
	access, err := newClaims("access", now, int64(AccessLifetime/time.Second))
	if err != nil {
		return Pair{}, err
	}
	for name, value := range claims {
		switch name {
		case "token_type", "exp", "jti", "iat":
			continue
		}
		// SimpleJWT 5.5.1 retains custom claims but gives access a fresh iat.
		// The serialized refresh token keeps its original claims.
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
