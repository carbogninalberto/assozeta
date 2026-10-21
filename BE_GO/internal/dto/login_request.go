package dto

import (
	"errors"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var ErrLoginRequestInvalid = errors.New("invalid login request")
var ErrLoginRequestShape = errors.New("login request is not an object")

// LoginRequest retains field values and presence separately. Credential services
// own Python-compatible identifier/password coercion and provider verification;
// decoding a body must not turn null into an absent field or a number into text.
type LoginRequest struct {
	Mode                                      string
	Identifier, Password, Token, Backend, OTP any
	OTPPresent                                bool
}

func ParseLoginRequest(body any) (LoginRequest, error) {
	data, ok := body.(domain.RequestObject)
	if !ok {
		return LoginRequest{}, ErrLoginRequestShape
	}
	identifier, username := data.Lookup("username")
	password, hasPassword := data.Lookup("password")
	token, hasToken := data.Lookup("token")
	backend, hasBackend := data.Lookup("backend")
	otp, present := data.Lookup("otp")
	mode := ""
	if username && hasPassword && !hasToken {
		mode = "password"
	}
	if username && hasToken && hasBackend {
		mode = "social"
	}
	if mode == "" {
		return LoginRequest{}, ErrLoginRequestInvalid
	}
	return LoginRequest{mode, identifier, password, token, backend, otp, present}, nil
}
