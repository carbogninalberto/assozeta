package handler

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/labstack/echo/v4"
)

// TestProfileTemplateRejectedTextResponse is the view's 400 body:
// {"<field>": ["<message>"]}, also behind a wrapped error.
func TestProfileTemplateRejectedTextResponse(t *testing.T) {
	for _, tc := range []struct{ field, message string }{
		{"regulation", "Il testo contiene troppi livelli annidati (massimo 32)."},
		{"additional_sections", "Il testo è troppo lungo (massimo 500000 caratteri)."},
		{"demand", "Il testo è troppo lungo (massimo 500000 caratteri)."},
	} {
		e := echo.New()
		request := httptest.NewRequest(http.MethodPatch, "/profile/update/subscription/template", nil)
		recorder := httptest.NewRecorder()
		c := e.NewContext(request, recorder)
		rejection := &dto.ProfileTemplateTextRejected{Field: tc.field, Message: tc.message}
		if err := profileTemplateError(c, fmt.Errorf("save: %w", rejection)); err != nil {
			t.Fatal(err)
		}
		var body map[string][]string
		if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil || recorder.Code != http.StatusBadRequest ||
			len(body) != 1 || len(body[tc.field]) != 1 || body[tc.field][0] != tc.message {
			t.Fatal(recorder.Code, recorder.Body.String(), err)
		}
	}
}
