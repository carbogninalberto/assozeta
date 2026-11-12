package handler

import (
	"context"
	"net/http/httptest"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/labstack/echo/v4"
)

type manifestReaderFunc func(context.Context) (*dto.InstanceManifest, error)

func (f manifestReaderFunc) Read(ctx context.Context) (*dto.InstanceManifest, error) { return f(ctx) }

func TestManifestTransportShortCircuitsRead(t *testing.T) {
	for _, test := range []struct {
		method, path, accept string
		status               int
	}{
		{"GET", "/instance/manifest.json?format=xml", "text/html", 404},
		{"GET", "/instance/manifest.json", "text/html", 406},
		{"OPTIONS", "/instance/manifest.json", "application/json", 200},
		{"POST", "/instance/manifest.json", "application/json", 405},
	} {
		t.Run(test.method+test.path+test.accept, func(t *testing.T) {
			handler := NewInstanceManifestHandler(manifestReaderFunc(func(context.Context) (*dto.InstanceManifest, error) {
				t.Fatal("transport-only request read the database")
				return nil, nil
			}))
			r := httptest.NewRequest(test.method, test.path, nil)
			r.Header.Set("Accept", test.accept)
			r.Header.Set("Authorization", "Bearer invalid")
			response := httptest.NewRecorder()
			if err := handler.Read(echo.New().NewContext(r, response)); err != nil {
				t.Fatal(err)
			}
			if response.Code != test.status || response.Header().Get("WWW-Authenticate") != "" {
				t.Fatal(response.Code, response.Body.String())
			}
		})
	}
}
