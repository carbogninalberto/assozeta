// Package social contains explicit, bounded clients for provider credentials.
// Account selection, persisted configuration, OTP and sessions remain services.
package social

import (
	"bytes"
	"context"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"math"
	"math/big"
	"net/http"
	"strconv"
	"strings"
	"time"
	"unicode"

	jwt "github.com/golang-jwt/jwt/v5"
)

const AppleKeysURL = "https://appleid.apple.com/auth/keys"
const MaxAppleKeysBytes = 1 << 20
const maxAppleTokenBytes = 1 << 20
const maxAppleRSAKeyBits = 16384

type HTTPClient interface {
	Do(*http.Request) (*http.Response, error)
}
type Apple struct {
	client HTTPClient
	now    func() time.Time
	slots  chan struct{}
}

func NewApple(client HTTPClient, now func() time.Time, concurrency int) *Apple {
	if client == nil || now == nil || concurrency < 1 {
		panic("invalid Apple verifier dependencies")
	}
	return &Apple{client: client, now: now, slots: make(chan struct{}, concurrency)}
}

// Verify retains Django's Apple result/error boundary. Provider/network/invalid
// credential failures return false without sensitive diagnostics. Caller
// cancellation is propagated, while the provider's own ten-second deadline is a
// failed verification. The caller must resolve the current client ID first.
func (a *Apple) Verify(ctx context.Context, token, audience any) (map[string]any, bool, error) {
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	if !truthy(audience) {
		return nil, false, nil
	}
	select {
	case a.slots <- struct{}{}:
	case <-ctx.Done():
		return nil, false, ctx.Err()
	}
	defer func() { <-a.slots }()
	op, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	info, valid := a.verify(op, token, audience)
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	if op.Err() != nil {
		return nil, false, nil
	}
	return info, valid, nil
}

func (a *Apple) verify(ctx context.Context, rawToken, audience any) (map[string]any, bool) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, AppleKeysURL, nil)
	if err != nil {
		return nil, false
	}
	response, err := a.client.Do(request)
	if err != nil {
		if response != nil && response.Body != nil {
			response.Body.Close()
		}
		return nil, false
	}
	if response == nil || response.Body == nil {
		return nil, false
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, MaxAppleKeysBytes+1))
	if err != nil || len(body) > MaxAppleKeysBytes {
		return nil, false
	}
	if !appleJSON(ctx, body) {
		return nil, false
	}
	var document map[string]any
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if decoder.Decode(&document) != nil {
		return nil, false
	}
	if decoder.Decode(new(any)) != io.EOF {
		return nil, false
	}
	keys, ok := document["keys"].([]any)
	if !ok {
		return nil, false
	}
	encoded, ok := rawToken.(string)
	if !ok || len(encoded) > maxAppleTokenBytes {
		return nil, false
	}
	parser := jwt.NewParser(jwt.WithValidMethods([]string{"RS256"}), jwt.WithJSONNumber(), jwt.WithPaddingAllowed(), jwt.WithoutClaimsValidation())
	segments := strings.Split(encoded, ".")
	if len(segments) != 3 {
		return nil, false
	}
	for _, segment := range segments[:2] {
		data, err := parser.DecodeSegment(segment)
		if err != nil || !appleJSON(ctx, data) {
			return nil, false
		}
	}
	claims := jwt.MapClaims{}
	parsed, err := parser.ParseWithClaims(encoded, claims, func(token *jwt.Token) (any, error) {
		if !appleHeaders(token.Header) {
			return nil, errAppleCredential
		}
		kid, present := token.Header["kid"]
		if present {
			if _, ok := kid.(string); !ok {
				return nil, errAppleCredential
			}
		}
		for _, raw := range keys {
			key, ok := raw.(map[string]any)
			if !ok {
				return nil, errAppleCredential
			}
			keyID, exists := key["kid"]
			if !exists {
				return nil, errAppleCredential
			}
			if keyID == nil && kid == nil {
				return applePublicKey(key)
			}
			if name, ok := keyID.(string); ok && name == kid {
				return applePublicKey(key)
			}
		}
		return nil, errAppleCredential
	})
	if err != nil || !parsed.Valid || !appleClaims(claims, audience, a.now()) {
		return nil, false
	}
	verified, exists := claims["email_verified"]
	if !exists {
		verified = false
	}
	return map[string]any{"email": claims["email"], "email_verified": verified, "sub": claims["sub"], "first_name": "", "last_name": ""}, true
}

var errAppleCredential = errors.New("invalid Apple credential")

// golang-jwt's JSON-number decoder otherwise accepts trailing payload data and
// does not apply Python's integer-digit bound. Scan every value, including
// duplicate/unused claims, before relying on its cryptographic verification.
func appleJSON(ctx context.Context, data []byte) bool {
	if !json.Valid(data) {
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	depth := 0
	for {
		if ctx.Err() != nil {
			return false
		}
		token, err := decoder.Token()
		if err == io.EOF {
			return true
		}
		if err != nil {
			return false
		}
		switch value := token.(type) {
		case json.Number:
			text := string(value)
			if !strings.ContainsAny(text, ".eE") && len(strings.TrimPrefix(text, "-")) > 4300 {
				return false
			}
		case json.Delim:
			if value == '{' || value == '[' {
				depth++
			} else {
				depth--
			}
			if depth > 1000 {
				return false
			}
		}
	}
}

func appleHeaders(header map[string]any) bool {
	if value, ok := header["b64"].(bool); ok && !value {
		return false
	}
	if raw, present := header["crit"]; present {
		items, ok := raw.([]any)
		if !ok || len(items) == 0 {
			return false
		}
		for _, item := range items {
			name, ok := item.(string)
			if !ok || name != "b64" {
				return false
			}
			if _, present := header[name]; !present {
				return false
			}
		}
	}
	return true
}

func applePublicKey(key map[string]any) (*rsa.PublicKey, error) {
	if key["kty"] != "RSA" {
		return nil, errAppleCredential
	}
	// PyJWT's RSA verifier cannot verify with a returned private-key object.
	if _, private := key["d"]; private {
		return nil, errAppleCredential
	}
	decode := func(value any) (*big.Int, bool) {
		text, ok := value.(string)
		if !ok {
			return nil, false
		}
		text = strings.TrimRight(text, "=")
		data, err := base64.RawURLEncoding.DecodeString(text)
		if err != nil || len(data) == 0 || len(data) > maxAppleRSAKeyBits/8 {
			return nil, false
		}
		return new(big.Int).SetBytes(data), true
	}
	n, ok := decode(key["n"])
	if !ok || n.Sign() <= 0 || n.BitLen() > maxAppleRSAKeyBits {
		return nil, errAppleCredential
	}
	e, ok := decode(key["e"])
	if !ok || !e.IsInt64() || e.Int64() < 3 || e.Int64() > math.MaxInt32 || e.Bit(0) == 0 || e.Cmp(n) >= 0 {
		return nil, errAppleCredential
	}
	return &rsa.PublicKey{N: n, E: int(e.Int64())}, nil
}

func appleClaims(claims jwt.MapClaims, audience any, now time.Time) bool {
	if claims["iss"] != "https://appleid.apple.com" {
		return false
	}
	for _, name := range []string{"sub", "jti"} {
		if value, exists := claims[name]; exists {
			if _, ok := value.(string); !ok {
				return false
			}
		}
	}
	current := big.NewInt(now.Unix())
	for _, name := range []string{"iat", "nbf", "exp"} {
		if value, exists := claims[name]; exists {
			timestamp, ok := pythonInteger(value)
			if !ok {
				return false
			}
			if name == "exp" {
				if timestamp.Cmp(current) <= 0 {
					return false
				}
			} else if timestamp.Cmp(current) > 0 {
				return false
			}
		}
	}
	var actual []string
	switch value := claims["aud"].(type) {
	case string:
		if value == "" {
			return false
		}
		actual = []string{value}
	case []any:
		for _, item := range value {
			text, ok := item.(string)
			if !ok {
				return false
			}
			actual = append(actual, text)
		}
	default:
		return false
	}
	match := func(value any) bool {
		text, ok := value.(string)
		if !ok {
			return false
		}
		for _, v := range actual {
			if v == text {
				return true
			}
		}
		return false
	}
	switch value := audience.(type) {
	case string:
		return match(value)
	case []any:
		for _, v := range value {
			if match(v) {
				return true
			}
		}
	case map[string]any:
		for k := range value {
			if match(k) {
				return true
			}
		}
	}
	return false
}

func truthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	case json.Number:
		n, _ := strconv.ParseFloat(string(v), 64)
		return n != 0
	default:
		return true
	}
}

// PyJWT validates optional time claims with Python int(), including numeric
// strings and truncation toward zero. This is separate from REST token policy.
func pythonInteger(value any) (*big.Int, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewInt(1), true
		}
		return big.NewInt(0), true
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			if len(strings.TrimPrefix(text, "-")) > 4300 {
				return nil, false
			}
			n, ok := new(big.Int).SetString(text, 10)
			return n, ok
		}
		n, err := strconv.ParseFloat(text, 64)
		if err != nil || math.IsNaN(n) || math.IsInf(n, 0) {
			return nil, false
		}
		i, _ := big.NewFloat(n).Int(nil)
		return i, true
	case string:
		text := strings.TrimFunc(v, unicode.IsSpace)
		var out strings.Builder
		if strings.HasPrefix(text, "+") || strings.HasPrefix(text, "-") {
			out.WriteByte(text[0])
			text = text[1:]
		}
		previous, digits := false, 0
		for _, r := range text {
			if r == '_' {
				if !previous {
					return nil, false
				}
				previous = false
				continue
			}
			digit := -1
			for _, v := range unicode.Digit.R16 {
				if r >= rune(v.Lo) && r <= rune(v.Hi) && (r-rune(v.Lo))%rune(v.Stride) == 0 {
					digit = int((r-rune(v.Lo))/rune(v.Stride)) % 10
					break
				}
			}
			if digit < 0 {
				for _, v := range unicode.Digit.R32 {
					if uint32(r) >= v.Lo && uint32(r) <= v.Hi && (uint32(r)-v.Lo)%v.Stride == 0 {
						digit = int((uint32(r)-v.Lo)/v.Stride) % 10
						break
					}
				}
			}
			if digit < 0 {
				return nil, false
			}
			out.WriteByte(byte('0' + digit))
			digits++
			previous = true
			if digits > 4300 {
				return nil, false
			}
		}
		if !previous {
			return nil, false
		}
		n, ok := new(big.Int).SetString(out.String(), 10)
		return n, ok
	}
	return nil, false
}
