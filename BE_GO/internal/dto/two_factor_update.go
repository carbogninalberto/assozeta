package dto

import (
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var ErrTwoFactorUpdateShape = errors.New("invalid two-factor update shape")

type TwoFactorUpdateRequest struct{ Enable, Secret, OTP any }

// Django indexes all three fields before checking truthiness or OTP. Preserve
// missing fields as a server-error boundary rather than silently disabling 2FA.
func ParseTwoFactorUpdate(body any) (TwoFactorUpdateRequest, error) {
	data, ok := body.(domain.RequestObject)
	if !ok {
		return TwoFactorUpdateRequest{}, ErrTwoFactorUpdateShape
	}
	enable, hasEnable := data.Lookup("enable")
	secret, hasSecret := data.Lookup("secret")
	otp, hasOTP := data.Lookup("otp")
	if !hasEnable || !hasSecret || !hasOTP {
		return TwoFactorUpdateRequest{}, ErrTwoFactorUpdateShape
	}
	return TwoFactorUpdateRequest{Enable: enable, Secret: secret, OTP: otp}, nil
}
