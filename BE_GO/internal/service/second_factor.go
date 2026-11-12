package service

import (
	"context"
	"errors"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
)

var ErrOTPRequired = errors.New("OTP code required.")
var ErrOTPInvalid = errors.New("OTP code not valid.")
var ErrOTPShape = errors.New("OTP value has no supported length")

type SecondFactor struct{ now func() time.Time }

func NewSecondFactor(now func() time.Time) *SecondFactor { return &SecondFactor{now: now} }

// Check accepts trusted persisted 2FA configuration, never a request-supplied
// enabled flag or secret. In the complete login workflow this runs after valid
// credentials, active-status admission and username/audit normalization. It has
// no durable effects and does not issue a session or advance a replay counter.
func (s *SecondFactor) Check(ctx context.Context, enabled bool, secret *string, value any, present bool) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if !enabled {
		return nil
	}
	if !present {
		return ErrOTPRequired
	}
	length := 0
	supplied := ""
	switch v := value.(type) {
	case string:
		length = utf8.RuneCountInString(v)
		supplied = v
	case []any:
		length = len(v)
	case domain.RequestObject:
		length = len(v)
	case domain.RequestUpload:
		if v.Size != 6 {
			return ErrOTPRequired
		}
		length = 6
		supplied = v.Name
	default:
		return ErrOTPShape
	}
	if length != 6 {
		return ErrOTPRequired
	}
	if secret == nil {
		return passwordcrypto.ErrTOTPSecret
	}
	// A JSON list/object can have length six, but its Python string representation
	// cannot equal a six-digit code. Still decode/generate first to preserve the
	// corrupt-secret error precedence before PyOTP's comparison.
	ok, err := passwordcrypto.VerifyTOTP(*secret, supplied, s.now())
	if cancellation := ctx.Err(); cancellation != nil {
		return cancellation
	}
	if err != nil {
		return err
	}
	if !ok {
		return ErrOTPInvalid
	}
	return nil
}
