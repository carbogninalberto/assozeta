package social

import (
	"context"
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/elliptic"
	"encoding/base64"
	"math/big"
	"strings"
	"time"

	protocol "github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	jwt "github.com/golang-jwt/jwt/v5"
)

// Google auth delegates JWK responses to a new PyJWKClient on every operation.
// This performs a second fetch and uses PyJWT claims/error semantics, which
// differ from the certificate decoder (including required times and expiry).
func (g *Google) verifyJWK(ctx context.Context, rawToken, audience any) (map[string]any, error) {
	encoded, ok := rawToken.(string)
	if !ok || len(encoded) > maxGoogleTokenBytes {
		return nil, ErrGoogleProvider
	}
	parser := jwt.NewParser(jwt.WithJSONNumber(), jwt.WithPaddingAllowed(), jwt.WithoutClaimsValidation())
	segments := strings.Split(encoded, ".")
	if len(segments) != 3 {
		return nil, ErrGoogleProvider
	}
	documents := make([]map[string]any, 0, 2)
	for _, part := range segments[:2] {
		data, err := parser.DecodeSegment(part)
		if err != nil {
			return nil, ErrGoogleProvider
		}
		raw, err := googleJSON(ctx, data)
		if err != nil {
			return nil, ErrGoogleProvider
		}
		document, ok := raw.(map[string]any)
		if !ok {
			return nil, ErrGoogleProvider
		}
		documents = append(documents, document)
	}
	header := documents[0]
	claims := jwt.MapClaims(documents[1])
	if !protocol.ValidProtectedHeader(header) {
		return nil, ErrGoogleProvider
	}
	if _, err := parser.DecodeSegment(segments[2]); err != nil {
		return nil, ErrGoogleProvider
	}
	data, status, err := g.requestWith(ctx, g.jwkClient, GoogleCertsURL, nil, 30*time.Second)
	if err != nil || status != 200 {
		return nil, ErrGoogleProvider
	}
	raw, err := googleJSON(ctx, data)
	// json.load ValueError is the narrow JWK-fetch failure that reaches the
	// application's access-token fallback; transport/key/token errors propagate.
	if err != nil {
		return nil, errGoogleCredential
	}
	document, ok := raw.(map[string]any)
	if !ok {
		return nil, ErrGoogleProvider
	}
	keys, ok := document["keys"].([]any)
	if !ok {
		return nil, ErrGoogleProvider
	}
	var key any
	var algorithm string
	for _, raw := range keys {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		entry, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		candidate, method, ok := googleJWK(entry)
		if !ok {
			continue
		}
		use := entry["use"]
		if use != nil && use != "sig" {
			continue
		}
		kid, ok := entry["kid"].(string)
		if !ok || kid == "" || kid != header["kid"] {
			continue
		}
		key, algorithm = candidate, method
		break
	}
	if key == nil || header["alg"] != algorithm {
		return nil, ErrGoogleProvider
	}
	parser = jwt.NewParser(jwt.WithJSONNumber(), jwt.WithPaddingAllowed(), jwt.WithoutClaimsValidation(), jwt.WithValidMethods([]string{algorithm}))
	verified, err := parser.ParseWithClaims(encoded, claims, func(*jwt.Token) (any, error) { return key, nil })
	if err != nil || !verified.Valid || !providerJWTClaims(claims, audience, g.now()) {
		return nil, ErrGoogleProvider
	}
	issuer, ok := claims["iss"].(string)
	if !ok || issuer != "accounts.google.com" && issuer != "https://accounts.google.com" {
		return nil, ErrGoogleProvider
	}
	return map[string]any(claims), nil
}

func googleJWK(entry map[string]any) (any, string, bool) {
	kind, _ := entry["kty"].(string)
	algorithm, stringAlgorithm := entry["alg"].(string)
	if !stringAlgorithm && truthy(entry["alg"]) {
		return nil, "", false
	}
	if algorithm == "" {
		switch kind {
		case "RSA":
			algorithm = "RS256"
		case "oct":
			algorithm = "HS256"
		case "OKP":
			algorithm = "EdDSA"
		case "EC":
			switch entry["crv"] {
			case "P-256":
				algorithm = "ES256"
			case "P-384":
				algorithm = "ES384"
			case "P-521":
				algorithm = "ES512"
			}
		}
	}
	method := jwt.GetSigningMethod(algorithm)
	if method == nil {
		return nil, "", false
	}
	decode := func(value any) ([]byte, bool) {
		s, ok := value.(string)
		if !ok {
			return nil, false
		}
		v, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(s, "="))
		return v, err == nil
	}
	switch kind {
	case "RSA":
		switch method.(type) {
		case *jwt.SigningMethodRSA, *jwt.SigningMethodRSAPSS:
		default:
			return nil, "", false
		}
		key, err := applePublicKey(entry)
		return key, algorithm, err == nil
	case "EC":
		if _, ok := method.(*jwt.SigningMethodECDSA); !ok {
			return nil, "", false
		}
		var curve elliptic.Curve
		switch entry["crv"] {
		case "P-256":
			curve = elliptic.P256()
		case "P-384":
			curve = elliptic.P384()
		case "P-521":
			curve = elliptic.P521()
		default:
			return nil, "", false
		}
		x, okX := decode(entry["x"])
		y, okY := decode(entry["y"])
		size := (curve.Params().BitSize + 7) / 8
		if !okX || !okY || len(x) != size || len(y) != size {
			return nil, "", false
		}
		key := &ecdsa.PublicKey{Curve: curve, X: new(big.Int).SetBytes(x), Y: new(big.Int).SetBytes(y)}
		if !curve.IsOnCurve(key.X, key.Y) {
			return nil, "", false
		}
		return key, algorithm, true
	case "OKP":
		if algorithm != "EdDSA" || entry["crv"] != "Ed25519" {
			return nil, "", false
		}
		key, ok := decode(entry["x"])
		if !ok || len(key) != ed25519.PublicKeySize {
			return nil, "", false
		}
		return ed25519.PublicKey(key), algorithm, true
	case "oct":
		if _, ok := method.(*jwt.SigningMethodHMAC); !ok {
			return nil, "", false
		}
		key, ok := decode(entry["k"])
		return key, algorithm, ok
	}
	return nil, "", false
}
