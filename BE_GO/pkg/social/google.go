package social

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"reflect"
	"strconv"
	"strings"
	"time"

	protocol "github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	jwt "github.com/golang-jwt/jwt/v5"
)

const GoogleCertsURL = "https://www.googleapis.com/oauth2/v1/certs"
const GoogleTokenInfoURL = "https://oauth2.googleapis.com/tokeninfo"
const GoogleUserInfoURL = "https://www.googleapis.com/oauth2/v3/userinfo"
const MaxGoogleResponseBytes = 1 << 20
const maxGoogleTokenBytes = 1 << 20

// ErrGoogleProvider preserves failures outside Django's ValueError fallback.
// It deliberately contains neither credentials nor provider response details.
var ErrGoogleProvider = errors.New("Google credential provider failure")
var errGoogleCredential = errors.New("invalid Google ID credential")

type Google struct {
	client    HTTPClient
	jwkClient HTTPClient
	now       func() time.Time
	slots     chan struct{}
}

// The dedicated JWK transport allows the client to enforce PyJWKClient's no-
// redirect policy independently of requests-style certificate/access redirects.
// Transport ownership and idle-connection shutdown remain with the caller.
func NewGoogle(client HTTPClient, jwkTransport http.RoundTripper, now func() time.Time, concurrency int) *Google {
	if client == nil || jwkTransport == nil || now == nil || concurrency < 1 {
		panic("invalid Google verifier dependencies")
	}
	return &Google{client: client, jwkClient: &http.Client{Transport: jwkTransport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, now: now, slots: make(chan struct{}, concurrency)}
}

func (g *Google) Verify(ctx context.Context, token, audience any) (map[string]any, bool, error) {
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	if !truthy(audience) {
		return nil, false, nil
	}
	select {
	case g.slots <- struct{}{}:
	case <-ctx.Done():
		return nil, false, ctx.Err()
	}
	defer func() { <-g.slots }()
	claims, err := g.verifyID(ctx, token, audience)
	if ctx.Err() != nil {
		return nil, false, ctx.Err()
	}
	if errors.Is(err, errGoogleCredential) {
		claims = g.verifyAccess(ctx, token, audience)
		if ctx.Err() != nil {
			return nil, false, ctx.Err()
		}
		if claims == nil {
			return nil, false, nil
		}
	} else if err != nil {
		return nil, false, err
	}
	return googleIdentity(claims), true, nil
}

func (g *Google) request(ctx context.Context, destination string, headers http.Header, timeout time.Duration) ([]byte, int, error) {
	return g.requestWith(ctx, g.client, destination, headers, timeout)
}

func (g *Google) requestWith(ctx context.Context, client HTTPClient, destination string, headers http.Header, timeout time.Duration) ([]byte, int, error) {
	op, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	r, err := http.NewRequestWithContext(op, http.MethodGet, destination, nil)
	if err != nil {
		return nil, 0, ErrGoogleProvider
	}
	r.Header = headers
	response, err := client.Do(r)
	if err != nil {
		if response != nil && response.Body != nil {
			response.Body.Close()
		}
		return nil, 0, ErrGoogleProvider
	}
	if response == nil || response.Body == nil {
		return nil, 0, ErrGoogleProvider
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, MaxGoogleResponseBytes+1))
	if err != nil || len(data) > MaxGoogleResponseBytes || op.Err() != nil {
		return nil, 0, ErrGoogleProvider
	}
	return data, response.StatusCode, nil
}

func googleJSON(ctx context.Context, data []byte) (any, error) {
	if !protocol.ValidDocument(ctx, data) {
		return nil, errGoogleCredential
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	var value any
	if d.Decode(&value) != nil {
		return nil, errGoogleCredential
	}
	return value, nil
}

func (g *Google) verifyID(ctx context.Context, rawToken, audience any) (map[string]any, error) {
	data, status, err := g.request(ctx, GoogleCertsURL, nil, 120*time.Second)
	if err != nil || status != http.StatusOK {
		return nil, ErrGoogleProvider
	}
	certs, err := googleJSON(ctx, data)
	if err != nil {
		return nil, err
	}
	if object, ok := certs.(map[string]any); ok {
		if _, exists := object["keys"]; exists {
			return g.verifyJWK(ctx, rawToken, audience)
		}
	} else {
		switch certs.(type) {
		case string, []any:
		default:
			return nil, ErrGoogleProvider
		}
	}
	encoded, ok := rawToken.(string)
	if !ok || len(encoded) > maxGoogleTokenBytes {
		return nil, errGoogleCredential
	}
	segments := strings.Split(encoded, ".")
	if len(segments) != 3 {
		return nil, errGoogleCredential
	}
	decode := func(s string) ([]byte, error) { return base64.RawURLEncoding.DecodeString(strings.TrimRight(s, "=")) }
	headerBytes, err := decode(segments[0])
	if err != nil {
		return nil, errGoogleCredential
	}
	payloadBytes, err := decode(segments[1])
	if err != nil {
		return nil, errGoogleCredential
	}
	signature, err := decode(segments[2])
	if err != nil {
		return nil, errGoogleCredential
	}
	rawHeader, err := googleJSON(ctx, headerBytes)
	if err != nil {
		return nil, err
	}
	rawClaims, err := googleJSON(ctx, payloadBytes)
	if err != nil {
		return nil, err
	}
	header, ok := rawHeader.(map[string]any)
	if !ok {
		return nil, errGoogleCredential
	}
	claims, ok := rawClaims.(map[string]any)
	if !ok {
		return nil, errGoogleCredential
	}
	algorithm, ok := header["alg"].(string)
	if !ok {
		return nil, errGoogleCredential
	}
	var method jwt.SigningMethod
	switch algorithm {
	case "RS256":
		method = jwt.SigningMethodRS256
	case "ES256":
		method = jwt.SigningMethodES256
	default:
		return nil, errGoogleCredential
	}
	keys, err := googleCertificates(data, certs, header["kid"])
	if err != nil {
		return nil, err
	}
	verified := false
	for _, raw := range keys {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		key, err := googlePublicKey(raw)
		if err != nil {
			return nil, err
		}
		switch k := key.(type) {
		case *rsa.PublicKey:
			if algorithm != "RS256" {
				return nil, ErrGoogleProvider
			}
			if k.N.BitLen() > 16384 {
				return nil, errGoogleCredential
			}
		case *ecdsa.PublicKey:
			if algorithm != "ES256" {
				return nil, ErrGoogleProvider
			}
		default:
			return nil, ErrGoogleProvider
		}
		if method.Verify(segments[0]+"."+segments[1], signature, key) == nil {
			verified = true
			break
		}
	}
	if !verified {
		return nil, errGoogleCredential
	}
	current := big.NewRat(g.now().Unix(), 1)
	for _, name := range []string{"iat", "exp"} {
		value, present := claims[name]
		if !present {
			return nil, errGoogleCredential
		}
		number, ok := googleNumber(value)
		if !ok {
			return nil, ErrGoogleProvider
		}
		comparison := number.Cmp(current)
		if name == "iat" && comparison > 0 || name == "exp" && comparison < 0 {
			return nil, errGoogleCredential
		}
	}
	match, err := googleAudience(claims["aud"], audience)
	if err != nil {
		return nil, err
	}
	if !match {
		return nil, errGoogleCredential
	}
	issuer, ok := claims["iss"].(string)
	if !ok || issuer != "accounts.google.com" && issuer != "https://accounts.google.com" {
		return nil, ErrGoogleProvider
	}
	return claims, nil
}

func googleCertificates(data []byte, certs, kid any) ([]any, error) {
	if object, ok := certs.(map[string]any); ok {
		if truthy(kid) {
			switch kid.(type) {
			case []any, map[string]any:
				return nil, ErrGoogleProvider
			}
			name, ok := kid.(string)
			if !ok {
				return nil, errGoogleCredential
			}
			key, present := object[name]
			if !present {
				return nil, errGoogleCredential
			}
			return []any{key}, nil
		}
		// Python checks certificates in insertion order; an invalid earlier PEM
		// aborts verification even if a later key would match the signature.
		d := json.NewDecoder(bytes.NewReader(data))
		d.UseNumber()
		_, _ = d.Token()
		keys := []any{}
		seen := map[string]bool{}
		for d.More() {
			name, err := d.Token()
			if err != nil {
				return nil, errGoogleCredential
			}
			var discard any
			if d.Decode(&discard) != nil {
				return nil, errGoogleCredential
			}
			text, ok := name.(string)
			if !ok {
				return nil, errGoogleCredential
			}
			if !seen[text] {
				keys = append(keys, object[text])
				seen[text] = true
			}
		}
		return keys, nil
	}
	switch value := certs.(type) {
	case string:
		return []any{value}, nil
	case []any:
		return value, nil
	}
	return nil, ErrGoogleProvider
}

func googlePublicKey(value any) (any, error) {
	text, ok := value.(string)
	if !ok {
		return nil, errGoogleCredential
	}
	block, _ := pem.Decode([]byte(text))
	if block == nil {
		return nil, errGoogleCredential
	}
	if block.Type == "CERTIFICATE" {
		cert, err := x509.ParseCertificate(block.Bytes)
		if err != nil {
			return nil, errGoogleCredential
		}
		return cert.PublicKey, nil
	}
	if block.Type == "RSA PUBLIC KEY" {
		key, err := x509.ParsePKCS1PublicKey(block.Bytes)
		if err != nil {
			return nil, errGoogleCredential
		}
		return key, nil
	}
	key, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, errGoogleCredential
	}
	return key, nil
}

func googleNumber(value any) (*big.Rat, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewRat(1, 1), true
		}
		return big.NewRat(0, 1), true
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			return new(big.Rat).SetString(string(v))
		}
		n, err := strconv.ParseFloat(string(v), 64)
		if err != nil {
			return nil, false
		}
		r := new(big.Rat).SetFloat64(n)
		return r, r != nil
	}
	return nil, false
}

func googleAudience(actual, configured any) (bool, error) {
	switch value := configured.(type) {
	case string:
		return actual == value, nil
	case []any:
		for _, item := range value {
			if reflect.DeepEqual(actual, item) {
				return true, nil
			}
		}
		return false, nil
	case map[string]any:
		name, ok := actual.(string)
		if !ok {
			return false, ErrGoogleProvider
		}
		_, present := value[name]
		return present, nil
	}
	return false, ErrGoogleProvider
}

func (g *Google) verifyAccess(ctx context.Context, token, audience any) map[string]any {
	query := url.Values{}
	// Requests omits a null query value; non-string scalars keep their textual
	// representation. Container conversion remains a separate compatibility case.
	text := ""
	switch value := token.(type) {
	case nil:
	case string:
		text = value
		query.Set("access_token", text)
	case json.Number:
		text = string(value)
		query.Set("access_token", text)
	case bool:
		if value {
			text = "True"
		} else {
			text = "False"
		}
		query.Set("access_token", text)
	default:
		return nil
	}
	destination := GoogleTokenInfoURL
	if len(query) > 0 {
		destination += "?" + query.Encode()
	}
	data, status, err := g.request(ctx, destination, nil, 10*time.Second)
	if err != nil || status != 200 {
		return nil
	}
	raw, err := googleJSON(ctx, data)
	if err != nil {
		return nil
	}
	info, ok := raw.(map[string]any)
	if !ok || !reflect.DeepEqual(info["aud"], audience) {
		return nil
	}
	if token == nil {
		text = "None"
	}
	data, status, err = g.request(ctx, GoogleUserInfoURL, http.Header{"Authorization": []string{"Bearer " + text}}, 10*time.Second)
	if err != nil || status != 200 {
		return nil
	}
	raw, err = googleJSON(ctx, data)
	if err != nil {
		return nil
	}
	info, ok = raw.(map[string]any)
	if !ok {
		return nil
	}
	return info
}

func googleIdentity(claims map[string]any) map[string]any {
	field := func(name string, fallback any) any {
		value, present := claims[name]
		if present {
			return value
		}
		return fallback
	}
	return map[string]any{"email": claims["email"], "email_verified": field("email_verified", false), "first_name": field("given_name", ""), "last_name": field("family_name", ""), "picture": claims["picture"], "sub": claims["sub"]}
}
