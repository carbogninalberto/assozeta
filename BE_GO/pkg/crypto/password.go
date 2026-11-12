// Package crypto contains compatibility helpers for existing Django credentials.
// Password verification never changes a stored hash; the authentication service
// must coordinate any rehash with its transaction and rollback policy.
package crypto

import (
	"context"
	"crypto/rand"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"hash"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto/internal/argon2legacy"
	"golang.org/x/crypto/argon2"
	"golang.org/x/crypto/bcrypt"
	"golang.org/x/crypto/pbkdf2"
	"golang.org/x/crypto/scrypt"
)

var ErrPasswordFormat = errors.New("malformed password hash")
var ErrPasswordAlgorithm = errors.New("unsupported password hash algorithm or version")
var ErrPasswordCost = errors.New("password hash exceeds the configured verification budget")

// PasswordLimits is an explicit resource policy, not a list of allowed account
// credentials. A cost error must be reported operationally, never silently
// interpreted as a wrong password. Census imported hash parameters before rollout.
type PasswordLimits struct {
	PBKDF2Iterations  uint64
	BcryptCost        int
	ScryptMemoryBytes uint64
	ScryptWork        uint64
	ArgonMemoryKiB    uint64
	ArgonTime         uint64
	ArgonThreads      uint64
}

func DefaultPasswordLimits() PasswordLimits {
	return PasswordLimits{PBKDF2Iterations: 10000000, BcryptCost: 20, ScryptMemoryBytes: 512 << 20, ScryptWork: 1 << 30,
		ArgonMemoryKiB: 512 << 10, ArgonTime: 20, ArgonThreads: 255}
}

type PasswordVerifier struct{ limits PasswordLimits }

func NewPasswordVerifier(limits PasswordLimits) *PasswordVerifier {
	return &PasswordVerifier{limits: limits}
}
func (v *PasswordVerifier) Verify(ctx context.Context, password, encoded string) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if strings.HasPrefix(encoded, "!") {
		return false, nil
	}
	parts := strings.Split(encoded, "$")
	var ok bool
	var err error
	switch parts[0] {
	case "pbkdf2_sha256":
		ok, err = v.pbkdf(password, encoded, parts, sha256.New)
	case "pbkdf2_sha1":
		ok, err = v.pbkdf(password, encoded, parts, sha1.New)
	case "bcrypt_sha256":
		digest := sha256.Sum256([]byte(password))
		ok, err = v.bcrypt(hex.EncodeToString(digest[:]), strings.TrimPrefix(encoded, "bcrypt_sha256$"))
	case "scrypt":
		ok, err = v.scrypt(password, encoded, parts)
	case "argon2":
		ok, err = v.argon(password, parts)
	case "":
		if strings.HasPrefix(encoded, "$2a$") || strings.HasPrefix(encoded, "$2b$") || strings.HasPrefix(encoded, "$2x$") || strings.HasPrefix(encoded, "$2y$") {
			// Legacy raw bcrypt import: Python bcrypt 4.3 truncates at 72 bytes.
			raw := []byte(password)
			if len(raw) > 72 {
				raw = raw[:72]
			}
			ok, err = v.bcrypt(string(raw), encoded)
		} else {
			err = ErrPasswordAlgorithm
		}
	default:
		err = ErrPasswordAlgorithm
	}
	if cancellation := ctx.Err(); cancellation != nil {
		return false, cancellation
	}
	return ok, err
}
func (v *PasswordVerifier) Encode(ctx context.Context, password string) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	const iterations = 1000000 // Django 5.2.1's preferred PBKDF2 hasher.
	if v.limits.PBKDF2Iterations < iterations {
		return "", ErrPasswordCost
	}
	salt := rand.Text() // 130 random bits; Django requires at least 128 salt bits.
	digest := pbkdf2.Key([]byte(password), []byte(salt), iterations, sha256.Size, sha256.New)
	if err := ctx.Err(); err != nil {
		return "", err
	}
	return "pbkdf2_sha256$1000000$" + salt + "$" + base64.StdEncoding.EncodeToString(digest), nil
}
func positive(value string) (uint64, error) {
	number, err := strconv.ParseUint(value, 10, 32)
	if err != nil || number == 0 {
		return 0, ErrPasswordFormat
	}
	return number, nil
}
func (v *PasswordVerifier) pbkdf(password, encoded string, parts []string, digest func() hash.Hash) (bool, error) {
	if len(parts) != 4 || parts[2] == "" {
		return false, ErrPasswordFormat
	}
	iterations, err := positive(parts[1])
	if err != nil {
		return false, err
	}
	if iterations > v.limits.PBKDF2Iterations {
		return false, ErrPasswordCost
	}
	expected, err := base64.StdEncoding.DecodeString(parts[3])
	if err != nil || len(expected) != digest().Size() {
		return false, ErrPasswordFormat
	}
	result := pbkdf2.Key([]byte(password), []byte(parts[2]), int(iterations), len(expected), digest)
	canonical := parts[0] + "$" + strconv.FormatUint(iterations, 10) + "$" + parts[2] + "$" + base64.StdEncoding.EncodeToString(result)
	return subtle.ConstantTimeCompare([]byte(encoded), []byte(canonical)) == 1, nil
}
func (v *PasswordVerifier) bcrypt(password, encoded string) (bool, error) {
	if len(encoded) != 60 {
		return false, ErrPasswordFormat
	}
	cost, err := bcrypt.Cost([]byte(encoded))
	if err != nil {
		return false, ErrPasswordFormat
	}
	if cost > v.limits.BcryptCost {
		return false, ErrPasswordCost
	}
	err = bcrypt.CompareHashAndPassword([]byte(encoded), []byte(password))
	if errors.Is(err, bcrypt.ErrMismatchedHashAndPassword) {
		return false, nil
	}
	if err != nil {
		return false, ErrPasswordFormat
	}
	return true, nil
}
func (v *PasswordVerifier) scrypt(password, encoded string, parts []string) (bool, error) {
	if len(parts) != 6 || parts[2] == "" {
		return false, ErrPasswordFormat
	}
	n, err := positive(parts[1])
	if err != nil || n < 2 || n&(n-1) != 0 {
		return false, ErrPasswordFormat
	}
	r, err := positive(parts[3])
	if err != nil {
		return false, err
	}
	p, err := positive(parts[4])
	if err != nil || r*p >= 1<<30 {
		return false, ErrPasswordFormat
	}
	budget := v.limits.ScryptMemoryBytes
	if r > budget/128 || n > budget/(128*r) || p > budget/(128*r) || 128*r*(n+p+2) > budget || n > v.limits.ScryptWork/r/p {
		return false, ErrPasswordCost
	}
	expected, err := base64.StdEncoding.DecodeString(parts[5])
	if err != nil || len(expected) != 64 {
		return false, ErrPasswordFormat
	}
	result, err := scrypt.Key([]byte(password), []byte(parts[2]), int(n), int(r), int(p), 64)
	if err != nil {
		return false, ErrPasswordFormat
	}
	canonical := "scrypt$" + strconv.FormatUint(n, 10) + "$" + parts[2] + "$" + strconv.FormatUint(r, 10) + "$" + strconv.FormatUint(p, 10) + "$" + base64.StdEncoding.EncodeToString(result)
	return subtle.ConstantTimeCompare([]byte(encoded), []byte(canonical)) == 1, nil
}
func (v *PasswordVerifier) argon(password string, parts []string) (bool, error) {
	if len(parts) == 5 {
		parts = append(parts[:2:2], append([]string{"v=16"}, parts[2:]...)...)
	}
	if len(parts) != 6 {
		return false, ErrPasswordFormat
	}
	if (parts[2] != "v=19" && parts[2] != "v=16") || (parts[1] != "argon2id" && parts[1] != "argon2i" && parts[1] != "argon2d") {
		return false, ErrPasswordAlgorithm
	}
	parameters := strings.Split(parts[3], ",")
	if len(parameters) != 3 || !strings.HasPrefix(parameters[0], "m=") || !strings.HasPrefix(parameters[1], "t=") || !strings.HasPrefix(parameters[2], "p=") {
		return false, ErrPasswordFormat
	}
	memory, err := positive(parameters[0][2:])
	if err != nil {
		return false, err
	}
	rounds, err := positive(parameters[1][2:])
	if err != nil {
		return false, err
	}
	threads, err := positive(parameters[2][2:])
	if err != nil || memory < 8*threads {
		return false, ErrPasswordFormat
	}
	if memory > v.limits.ArgonMemoryKiB || rounds > v.limits.ArgonTime || threads > v.limits.ArgonThreads {
		return false, ErrPasswordCost
	}
	salt, err := base64.RawStdEncoding.Strict().DecodeString(parts[4])
	if err != nil || len(salt) < 8 {
		return false, ErrPasswordFormat
	}
	expected, err := base64.RawStdEncoding.Strict().DecodeString(parts[5])
	if err != nil || len(expected) < 4 {
		return false, ErrPasswordFormat
	}
	if len(expected) > 1024 {
		return false, ErrPasswordCost
	}
	derive := argon2.IDKey
	if parts[1] == "argon2i" {
		derive = argon2.Key
	}
	var result []byte
	if parts[2] == "v=19" && parts[1] != "argon2d" && threads <= 255 {
		result = derive([]byte(password), salt, uint32(rounds), uint32(memory), uint8(threads), uint32(len(expected)))
	} else {
		mode := 2
		if parts[1] == "argon2i" {
			mode = 1
		}
		if parts[1] == "argon2d" {
			mode = 0
		}
		version := uint32(19)
		if parts[2] == "v=16" {
			version = 16
		}
		result = argon2legacy.DeriveKey(mode, version, []byte(password), salt, uint32(rounds), uint32(memory), uint32(threads), uint32(len(expected)))
	}
	return subtle.ConstantTimeCompare(result, expected) == 1, nil
}
