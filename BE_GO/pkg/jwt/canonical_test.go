package jwt

import (
	"crypto/ed25519"
	"encoding/base64"
	"testing"
)

func rawSigned(key ed25519.PrivateKey, header, payload string, padded bool) string {
	encoding := base64.RawURLEncoding
	if padded {
		encoding = base64.URLEncoding
	}
	message := encoding.EncodeToString([]byte(header)) + "." + encoding.EncodeToString([]byte(payload))
	return message + "." + encoding.EncodeToString(ed25519.Sign(key, []byte(message)))
}

func TestRefreshReserializesWithoutRotatingClaims(t *testing.T) {
	m, key, _ := testManager(t)
	for _, tc := range []struct{ name, input, want string }{
		{"order and whitespace", ` { "z":1,"token_type":"refresh", "exp":1893456060,"jti":"same" } `, `{"z":1,"token_type":"refresh","exp":1893456060,"jti":"same"}`},
		{"duplicates", `{"exp":1,"token_type":"refresh","exp":1893456060,"jti":"same"}`, `{"exp":1893456060,"token_type":"refresh","jti":"same"}`},
		{"nested Unicode and numbers", `{"token_type":"refresh","exp":1893456060,"jti":"same","custom":{"z":"é😀<>&","a":[1e0,-0,-0.0,1e-5,1e16,1.2345e6]}}`, `{"token_type":"refresh","exp":1893456060,"jti":"same","custom":{"z":"\u00e9\ud83d\ude00<>&","a":[1.0,0,-0.0,1e-05,1e+16,1234500.0]}}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			original := rawSigned(key, `{"typ":"JWT","alg":"EdDSA","custom":"discarded"}`, tc.input, true)
			pair, err := m.Refresh(original)
			if err != nil {
				t.Fatal(err)
			}
			want := rawSigned(key, `{"alg":"EdDSA","typ":"JWT"}`, tc.want, false)
			if pair.RefreshToken != want {
				t.Fatal("refresh was not serialized with the retained claim order/types and default header")
			}
			claims, err := m.Verify(pair.RefreshToken, "refresh")
			if err != nil || claims["jti"] != "same" {
				t.Fatal("normalization rotated or invalidated refresh claims")
			}
		})
	}
}
