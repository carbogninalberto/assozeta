package handler

import (
	"bytes"
	"context"
	"errors"
	"mime/multipart"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
)

func TestSecondFactorUsesUploadMetadata(t *testing.T) {
	secret := "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
	for _, tc := range []struct {
		name, content string
		want          error
	}{{"287082", "ignore", nil}, {"wrong!", "287082", service.ErrOTPInvalid}, {"287082", "short", service.ErrOTPRequired}} {
		var body bytes.Buffer
		writer := multipart.NewWriter(&body)
		part, err := writer.CreateFormFile("otp", tc.name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = part.Write([]byte(tc.content)); err != nil {
			t.Fatal(err)
		}
		if err = writer.Close(); err != nil {
			t.Fatal(err)
		}
		request := httptest.NewRequest("POST", "/oauth2/login", &body)
		request.Header.Set("Content-Type", writer.FormDataContentType())
		parsed, err := readReferenceBody(request)
		if err != nil {
			t.Fatal(err)
		}
		value, present := parsed.(domain.RequestObject).Lookup("otp")
		gate := service.NewSecondFactor(func() time.Time { return time.Unix(59, 0) })
		if err = gate.Check(context.Background(), true, &secret, value, present); !errors.Is(err, tc.want) {
			t.Fatal("upload metadata or OTP outcome changed")
		}
	}
}
