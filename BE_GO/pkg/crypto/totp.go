package crypto

import (
	"crypto/hmac"
	"crypto/sha1"
	"encoding/base32"
	"encoding/binary"
	"errors"
	"fmt"
	"strings"
	"time"

	"golang.org/x/text/unicode/norm"
)

var ErrTOTPSecret = errors.New("invalid persisted TOTP secret")
var ErrTOTPCounter = errors.New("negative TOTP counter")

// TOTPCode matches the existing PyOTP defaults: SHA1, six digits and 30 seconds.
// It accepts the existing base32 encoding (including lower case and omitted
// padding). Empty secrets retain PyOTP behavior; provisioning must validate input.
func TOTPCode(secret string, at time.Time) (string, error) {
	counter := at.Unix() / 30
	if counter < 0 {
		return "", ErrTOTPCounter
	}
	for _, c := range secret {
		if c > 127 {
			return "", ErrTOTPSecret
		}
	}
	secret = strings.ToUpper(secret)
	for _, c := range secret {
		if !(c >= 'A' && c <= 'Z') && !(c >= '2' && c <= '7') && c != '=' {
			return "", ErrTOTPSecret
		}
	}
	if padding := len(secret) % 8; padding != 0 {
		secret += strings.Repeat("=", 8-padding)
	}
	key, err := base32.StdEncoding.DecodeString(secret)
	if err != nil {
		return "", ErrTOTPSecret
	}
	var message [8]byte
	binary.BigEndian.PutUint64(message[:], uint64(counter))
	digest := hmac.New(sha1.New, key)
	_, _ = digest.Write(message[:])
	sum := digest.Sum(nil)
	offset := sum[len(sum)-1] & 15
	code := (binary.BigEndian.Uint32(sum[offset:offset+4]) & 0x7fffffff) % 1000000
	return fmt.Sprintf("%06d", code), nil
}

// VerifyTOTP checks only the current counter. PyOTP normalizes both strings with
// NFKC before its constant-time comparison; it does not trim input whitespace.
func VerifyTOTP(secret, supplied string, at time.Time) (bool, error) {
	expected, err := TOTPCode(secret, at)
	if err != nil {
		return false, err
	}
	return hmac.Equal([]byte(norm.NFKC.String(supplied)), []byte(expected)), nil
}
