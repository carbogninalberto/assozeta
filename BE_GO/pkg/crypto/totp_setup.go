package crypto

import (
	"encoding/base32"
	"io"
	"net/url"
	"strings"
)

// GenerateTOTPSecret uses twenty bytes of caller-supplied cryptographic entropy,
// yielding the same 32-character unpadded Base32 space as pyotp.random_base32.
func GenerateTOTPSecret(random io.Reader) (string, error) {
	var entropy [20]byte
	if _, err := io.ReadFull(random, entropy[:]); err != nil {
		return "", err
	}
	return base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(entropy[:]), nil
}

// TOTPProvisioningURI preserves PyOTP's default SHA1/6-digit/30-second URI,
// account fallback, query order and Python quote's slash-safe label encoding.
func TOTPProvisioningURI(secret, name, issuer string) string {
	quote := func(value string) string { return strings.ReplaceAll(url.QueryEscape(value), "+", "%20") }
	labelQuote := func(value string) string { return strings.ReplaceAll(quote(value), "%2F", "/") }
	if name == "" {
		name = "Secret"
	}
	label := labelQuote(name)
	query := "secret=" + quote(secret)
	if issuer != "" {
		label = labelQuote(issuer) + ":" + label
		query += "&issuer=" + quote(issuer)
	}
	return "otpauth://totp/" + label + "?" + query
}
