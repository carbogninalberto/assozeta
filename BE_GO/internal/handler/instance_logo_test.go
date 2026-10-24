package handler

import (
	"compress/gzip"
	"errors"
	"io"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/labstack/echo/v4"
)

// A controlled large finite body removes the random-padding ambiguity of the
// small real OPTIONS metadata, without inventing an API business error payload.
func TestInstanceLogoFiniteEncodingCombinesHeadersAndRestoresRequest(t *testing.T) {
	for _, fails := range []bool{false, true} {
		t.Run(map[bool]string{false: "body", true: "error"}[fails], func(t *testing.T) {
			original := httptest.NewRequest("OPTIONS", "/instance/logo.png", nil)
			original.Header.Add("Accept-Encoding", "identity")
			original.Header.Add("Accept-Encoding", "gzip")
			response := httptest.NewRecorder()
			context := echo.New().NewContext(original, response)
			body := strings.Repeat("controlled finite encoder body ", 128)
			sentinel := errors.New("controlled encoder failure")
			err := renderInstanceLogoFinite(context, func(c echo.Context) error {
				if c.Request() == original || c.Request().Header.Get("Accept-Encoding") != "identity,gzip" {
					t.Fatal("finite encoder did not receive isolated combined headers")
				}
				if fails {
					return sentinel
				}
				return c.Blob(200, "application/json", []byte(body))
			})
			if context.Request() != original || !reflect.DeepEqual(original.Header.Values("Accept-Encoding"), []string{"identity", "gzip"}) {
				t.Fatal("caller request was not restored intact")
			}
			if fails {
				if !errors.Is(err, sentinel) {
					t.Fatal(err)
				}
				return
			}
			if err != nil || response.Header().Get("Content-Encoding") != "gzip" {
				t.Fatal(err, response.Header())
			}
			reader, err := gzip.NewReader(response.Body)
			if err != nil {
				t.Fatal(err)
			}
			defer reader.Close()
			decoded, err := io.ReadAll(reader)
			if err != nil || string(decoded) != body {
				t.Fatal("finite body changed", err)
			}
		})
	}
}
