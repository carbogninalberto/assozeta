package dto

import (
	"encoding/json"
	"errors"
	"reflect"
	"testing"
)

func TestLoginRequestPresenceAndRawValues(t *testing.T) {
	for _, tc := range []struct {
		body, mode     string
		invalid, shape bool
	}{
		{`{}`, "", true, false}, {`{"username":"a"}`, "", true, false},
		{`{"username":"a","password":"p"}`, "password", false, false},
		{`{"username":"a","password":null}`, "password", false, false},
		{`{"username":null,"password":42,"otp":null}`, "password", false, false},
		{`{"username":["a"],"password":false,"backend":"ignored"}`, "password", false, false},
		{`{"username":"a","password":"p","token":null}`, "", true, false},
		{`{"username":"a","token":null,"backend":null}`, "social", false, false},
		{`{"username":"a","password":"p","token":"token","backend":"google"}`, "social", false, false},
		{`{"username":"a","token":"t"}`, "", true, false},
		{`{"password":"p","token":"t","backend":"apple"}`, "", true, false},
		{`{"Username":"a","password":"p"}`, "", true, false},
		{`{"username":"a","password":[],"otp":{"k":1}}`, "password", false, false},
		{`[]`, "", false, true}, {`null`, "", false, true}, {`"username"`, "", false, true}, {`false`, "", false, true}, {`42`, "", false, true},
	} {
		t.Run(tc.body, func(t *testing.T) {
			body, err := ParseReferenceJSON([]byte(tc.body))
			if err != nil {
				t.Fatal(err)
			}
			request, err := ParseLoginRequest(body)
			if tc.invalid || tc.shape {
				want := ErrLoginRequestInvalid
				if tc.shape {
					want = ErrLoginRequestShape
				}
				if !errors.Is(err, want) || !reflect.DeepEqual(request, LoginRequest{}) {
					t.Fatal("invalid shape accepted", err)
				}
				return
			}
			if err != nil || request.Mode != tc.mode {
				t.Fatal("wrong login branch", request.Mode, err)
			}
			var raw map[string]json.RawMessage
			json.Unmarshal([]byte(tc.body), &raw)
			for field, value := range map[string]any{"username": request.Identifier, "password": request.Password, "token": request.Token, "backend": request.Backend, "otp": request.OTP} {
				expected := any(nil)
				if b, ok := raw[field]; ok {
					expected, err = ParseReferenceJSON(b)
					if err != nil {
						t.Fatal(err)
					}
				}
				if !reflect.DeepEqual(value, expected) {
					t.Fatal("request value coerced", field)
				}
			}
			_, present := raw["otp"]
			if request.OTPPresent != present {
				t.Fatal("OTP presence lost")
			}
		})
	}
}
