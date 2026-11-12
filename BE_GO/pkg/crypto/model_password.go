package crypto

import (
	"context"
	"strings"
)

// CheckDjangoPassword follows AbstractBaseUser.check_password, without the
// public login view's extra raw-bcrypt fallback. The caller persists Upgrade.
func (v *PasswordVerifier) CheckDjangoPassword(ctx context.Context, value LoginPasswordValue, encoded string) (LoginPasswordDecision, error) {
	empty := LoginPasswordDecision{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	algorithm, _, _ := strings.Cut(encoded, "$")
	supported := algorithm == "pbkdf2_sha256" || algorithm == "pbkdf2_sha1" || algorithm == "argon2" || algorithm == "bcrypt_sha256" || algorithm == "scrypt"
	if value.Null || !supported || strings.HasPrefix(encoded, "!") {
		_, err := v.Encode(ctx, "unusable-model-runtime")
		return empty, err
	}
	if !value.String && algorithm != "pbkdf2_sha256" && algorithm != "pbkdf2_sha1" {
		return empty, ErrLoginPasswordValue
	}
	result, err := v.CheckLogin(ctx, value.Text, encoded)
	if err != nil {
		return empty, err
	}
	if result.Upgrade && !value.String {
		return empty, ErrLoginPasswordValue
	}
	return result, nil
}
