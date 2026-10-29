package service

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"strings"
)

// ErrIntegrationSecret is decrypt_secret's ImproperlyConfigured: no
// configured key opens the stored Fernet token.
var ErrIntegrationSecret = errors.New("stored integration credential cannot be decrypted")

// DecryptIntegrationSecret is instance.integration_configuration.decrypt_secret:
// Fernet keyed by sha256('assozeta:instance-integrations:v1:' + secret) for
// SECRET_KEY then each fallback. Fernet's TTL is unset, so timestamps are not
// checked; an empty value decrypts to "".
func DecryptIntegrationSecret(token string, secrets []string) (string, error) {
	if token == "" {
		return "", nil
	}
	for _, secret := range secrets {
		if plain, ok := fernetDecrypt(token, secret); ok {
			return plain, nil
		}
	}
	return "", ErrIntegrationSecret
}

func fernetDecrypt(token, secret string) (string, bool) {
	key := sha256.Sum256([]byte("assozeta:instance-integrations:v1:" + secret))
	data, err := base64.URLEncoding.DecodeString(token)
	if err != nil {
		data, err = base64.RawURLEncoding.DecodeString(strings.TrimRight(token, "="))
	}
	// version, timestamp, IV, at least one block, HMAC.
	if err != nil || len(data) < 1+8+16+16+32 || data[0] != 0x80 || (len(data)-1-8-16-32)%aes.BlockSize != 0 {
		return "", false
	}
	body, tag := data[:len(data)-32], data[len(data)-32:]
	mac := hmac.New(sha256.New, key[:16])
	mac.Write(body)
	if !hmac.Equal(mac.Sum(nil), tag) {
		return "", false
	}
	block, err := aes.NewCipher(key[16:])
	if err != nil {
		return "", false
	}
	iv, ciphertext := body[9:25], body[25:]
	plain := make([]byte, len(ciphertext))
	cipher.NewCBCDecrypter(block, iv).CryptBlocks(plain, ciphertext)
	pad := int(plain[len(plain)-1])
	if pad == 0 || pad > aes.BlockSize {
		return "", false
	}
	for _, b := range plain[len(plain)-pad:] {
		if int(b) != pad {
			return "", false
		}
	}
	return string(plain[:len(plain)-pad]), true
}
