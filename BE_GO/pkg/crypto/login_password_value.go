package crypto

import (
	"context"
	"errors"
	"strings"
)

var ErrLoginPasswordValue = errors.New("login password value cannot be processed")

// LoginPasswordValue carries the original type independently of its Python text.
// Only transport-compatible value conversion may construct it. Null is distinct
// because Django deliberately skips verification and burns the preferred KDF.
type LoginPasswordValue struct {
	Text         string
	String, Null bool
}

func (v *PasswordVerifier) CheckLoginValue(ctx context.Context, password LoginPasswordValue, encoded string) (LoginPasswordDecision, error) {
	empty := LoginPasswordDecision{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if password.String {
		return v.CheckLogin(ctx, password.Text, encoded)
	}
	algorithm, _, _ := strings.Cut(encoded, "$")
	supported := algorithm == "pbkdf2_sha256" || algorithm == "pbkdf2_sha1" || algorithm == "argon2" || algorithm == "bcrypt_sha256" || algorithm == "scrypt"
	if password.Null || !supported || strings.HasPrefix(encoded, "!") {
		if _, err := v.Encode(ctx, "unusable-login-runtime"); err != nil {
			return empty, err
		}
		// The view then calls password.encode() in its raw-bcrypt fallback. A
		// non-string has no such method, including for unknown/unusable encodings.
		return empty, ErrLoginPasswordValue
	}
	if algorithm != "pbkdf2_sha256" && algorithm != "pbkdf2_sha1" {
		return empty, ErrLoginPasswordValue
	}
	decision, err := v.CheckLogin(ctx, password.Text, encoded)
	if err != nil {
		return empty, err
	}
	// PBKDF2 force_bytes accepts str(value), but a successful stale hash calls
	// make_password(value), which rejects non-text inputs. Wrong credentials hit
	// the legacy fallback above. Neither path may produce a replacement hash.
	if !decision.Match || decision.Upgrade {
		return empty, ErrLoginPasswordValue
	}
	return decision, nil
}
