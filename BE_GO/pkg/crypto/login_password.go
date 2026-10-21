package crypto

import (
	"context"
	"crypto/sha256"
	"errors"
	"strconv"
	"strings"
	"unicode/utf8"

	"golang.org/x/crypto/pbkdf2"
)

// LoginPasswordDecision distinguishes Django's eager check_password upgrade from
// raw bcrypt migration, which the view persists only for the selected account.
type LoginPasswordDecision struct{ Match, Upgrade, Legacy bool }

func (v *PasswordVerifier) CheckLogin(ctx context.Context, password, encoded string) (LoginPasswordDecision, error) {
	result := LoginPasswordDecision{}
	algorithm, _, _ := strings.Cut(encoded, "$")
	supported := algorithm == "pbkdf2_sha256" || algorithm == "pbkdf2_sha1" || algorithm == "argon2" || algorithm == "bcrypt_sha256" || algorithm == "scrypt"
	if !supported || strings.HasPrefix(encoded, "!") {
		// Django burns a preferred-hasher invocation for unusable/unknown encodings,
		// including raw bcrypt before the view's legacy migration fallback.
		if _, err := v.Encode(ctx, "unusable-login-runtime"); err != nil {
			return result, err
		}
		if !strings.HasPrefix(encoded, "$2") {
			return result, nil
		}
		match, err := v.Verify(ctx, password, encoded)
		if errors.Is(err, ErrPasswordFormat) || errors.Is(err, ErrPasswordAlgorithm) {
			return result, nil
		}
		result.Match, result.Upgrade, result.Legacy = match, match, match
		return result, err
	}
	match, err := v.Verify(ctx, password, encoded)
	if err != nil {
		return result, err
	}
	upgrade := algorithm != "pbkdf2_sha256"
	if algorithm == "pbkdf2_sha256" {
		parts := strings.Split(encoded, "$") // Verify has validated the shape.
		rounds, _ := strconv.Atoi(parts[1])
		upgrade = rounds != 1000000 || utf8.RuneCountInString(parts[2]) < 22
		if !match && upgrade && rounds < 1000000 {
			if v.limits.PBKDF2Iterations < 1000000 {
				return result, ErrPasswordCost
			}
			// Match Django's harden_runtime for a failed low-cost preferred hash.
			_ = pbkdf2.Key([]byte(password), []byte(parts[2]), 1000000-rounds, sha256.Size, sha256.New)
		}
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	result.Match, result.Upgrade = match, match && upgrade
	return result, nil
}
