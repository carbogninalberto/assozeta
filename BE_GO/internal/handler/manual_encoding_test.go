package handler

import (
	"bytes"
	"compress/gzip"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/labstack/echo/v4"
)

func TestManualEncodingDoesNotCommitBeforeHandlerSuccess(t *testing.T) {
	e := echo.New()
	e.GET("/manuale/sections", func(c echo.Context) error {
		if err := c.Blob(200, "application/json", []byte("private partial failure")); err != nil {
			return err
		}
		return errors.New("private error detail")
	}, ManualResponseEncoding())
	w := httptest.NewRecorder()
	e.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/manuale/sections", nil))
	if w.Code != 500 || strings.Contains(w.Body.String(), "private") {
		t.Fatal("buffered failure escaped", w.Code, w.Body.String())
	}
}

func TestManualEncodingThresholdAndVerifiedAssetBytes(t *testing.T) {
	for _, tc := range []struct {
		name, accept, kind string
		size               int
		gzip, vary         bool
	}{
		{"short", "gzip", "application/json", 199, false, false},
		{"boundary", "identity", "application/json", 200, false, true},
		{"json-gzip", "gzip", "application/json", 4000, true, true},
		{"case-sensitive", "GZIP", "application/json", 4000, false, true},
		{"gzip-token-not-substring", "xgzip", "application/json", 4000, false, true},
		{"django-quality-token", "gzip;q=0", "application/json", 4000, true, true},
		{"asset-identity", "identity", "image/png", 24, false, true},
		{"asset-gzip", "gzip", "image/png", 24, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := echo.New()
			payload := bytes.Repeat([]byte("x"), tc.size)
			e.GET("/manuale/sections", func(c echo.Context) error { return c.Blob(200, tc.kind, payload) }, ManualResponseEncoding())
			r := httptest.NewRequest(http.MethodGet, "/manuale/sections", nil)
			r.Header.Set("Accept-Encoding", tc.accept)
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if w.Code != 200 || (w.Header().Get("Content-Encoding") == "gzip") != tc.gzip {
				t.Fatal("encoding", w.Code, w.Header())
			}
			if strings.Contains(strings.Join(w.Header().Values("Vary"), ","), "Accept-Encoding") != tc.vary {
				t.Fatal("vary", w.Header())
			}
			actual := w.Body.Bytes()
			if tc.gzip {
				reader, err := gzip.NewReader(bytes.NewReader(actual))
				if err != nil {
					t.Fatal(err)
				}
				actual, err = io.ReadAll(reader)
				reader.Close()
				if err != nil {
					t.Fatal(err)
				}
			}
			if !bytes.Equal(actual, payload) {
				t.Fatal("encoded bytes differ from verified response")
			}
		})
	}
}
