package handler

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type staffBoardServiceTest struct {
	err      error
	calls    []string
	body     any
	form     bool
	id       string
	admitErr error
}

func (s *staffBoardServiceTest) Admit(context.Context, domain.ResolvedIdentity) error {
	return s.admitErr
}
func (s *staffBoardServiceTest) List(context.Context, domain.ResolvedIdentity) ([]dto.StaffBoardMessageRow, error) {
	s.calls = append(s.calls, "list")
	return []dto.StaffBoardMessageRow{{StaffBoardMessageID: "m", AuthorName: "Utente eliminato"}}, s.err
}
func (s *staffBoardServiceTest) Add(_ context.Context, _ domain.ResolvedIdentity, body any, form bool) (dto.StaffBoardMessageRow, error) {
	s.calls, s.body, s.form = append(s.calls, "add"), body, form
	return dto.StaffBoardMessageRow{StaffBoardMessageID: "new"}, s.err
}
func (s *staffBoardServiceTest) Update(_ context.Context, _ domain.ResolvedIdentity, id string, body any, form bool) (dto.StaffBoardMessageRow, error) {
	s.calls, s.id, s.body, s.form = append(s.calls, "update"), id, body, form
	return dto.StaffBoardMessageRow{StaffBoardMessageID: id}, s.err
}
func (s *staffBoardServiceTest) Delete(_ context.Context, _ domain.ResolvedIdentity, id string) error {
	s.calls, s.id = append(s.calls, "delete"), id
	return s.err
}

func staffBoardTestServer(board *staffBoardServiceTest) *echo.Echo {
	ownerID := "a1000000-0000-4000-8000-000000700001"
	tokens := &messageReadsTokensTest{sub: ownerID}
	accounts := messageReadsAccountsTest{ownerID: {ID: ownerID, Role: domain.RoleAssociation, Active: true}}
	h := NewStaffBoardHandler(board, service.NewActorAuthentication(accounts, tokens),
		service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil), paymentcategorylifecycle.New())
	e := echo.New()
	e.Any("/communications/staff-board/list", h.List)
	e.Any("/communications/staff-board/add", h.Add)
	e.Any("/communications/staff-board/:staff_board_message_id/update", h.Update)
	e.Any("/communications/staff-board/:staff_board_message_id/delete", h.Delete)
	return e
}

func staffBoardRequest(e *echo.Echo, method, path, contentType, body, language string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer valid")
	if contentType != "" {
		r.Header.Set("Content-Type", contentType)
	}
	if language != "" {
		r.Header.Set("Accept-Language", language)
	}
	w := httptest.NewRecorder()
	e.ServeHTTP(w, r)
	return w
}

func staffBoardJSON(t *testing.T, w *httptest.ResponseRecorder) any {
	t.Helper()
	var value any
	if err := json.Unmarshal(w.Body.Bytes(), &value); err != nil {
		t.Fatalf("json %q: %v", w.Body.String(), err)
	}
	return value
}

func TestStaffBoardHandlerTransport(t *testing.T) {
	board := &staffBoardServiceTest{}
	e := staffBoardTestServer(board)
	w := staffBoardRequest(e, http.MethodOptions, "/communications/staff-board/list", "", "", "en")
	if w.Code != 200 || w.Header().Get("Allow") != "GET, OPTIONS" {
		t.Fatal(w.Code, w.Header())
	}
	metadata := staffBoardJSON(t, w).(map[string]any)
	if metadata["name"] != "Staff Board Messages List" || !strings.HasPrefix(metadata["description"].(string), "Internal staff board: list all messages") {
		t.Fatal(metadata)
	}
	if w = staffBoardRequest(e, http.MethodPost, "/communications/staff-board/list", "", "", "en"); w.Code != 405 || len(board.calls) != 0 {
		t.Fatal("list method", w.Code, board.calls)
	}
	if w = staffBoardRequest(e, http.MethodGet, "/communications/staff-board/list", "", "", ""); w.Code != 200 {
		t.Fatal(w.Code)
	}
	if data := staffBoardJSON(t, w).(map[string]any)["data"].([]any); len(data) != 1 || data[0].(map[string]any)["author"] != nil {
		t.Fatal(data)
	}
	w = staffBoardRequest(e, http.MethodPost, "/communications/staff-board/add", "application/json", `{"content":"x"}`, "")
	if w.Code != 201 || board.form || staffBoardJSON(t, w).(map[string]any)["data"].(map[string]any)["staff_board_message_id"] != "new" {
		t.Fatal("add", w.Code, w.Body.String(), board.form)
	}
	if object, ok := board.body.(domain.RequestObject); !ok || len(object) != 1 || object[0].Value != "x" {
		t.Fatalf("decoded body %#v", board.body)
	}
	if w = staffBoardRequest(e, http.MethodPost, "/communications/staff-board/add", "application/x-www-form-urlencoded", "content=y&pinned=on", ""); w.Code != 201 || !board.form {
		t.Fatal("form add", w.Code, board.form)
	}
	if w = staffBoardRequest(e, http.MethodPatch, "/communications/staff-board/abc/update", "application/json", `{"pinned":true}`, ""); w.Code != 200 || board.id != "abc" {
		t.Fatal("update", w.Code, board.id)
	}
	if w = staffBoardRequest(e, http.MethodDelete, "/communications/staff-board/abc/delete", "", "", ""); w.Code != 200 || w.Body.String() != "{\"msg\":\"message deleted.\"}\n" {
		t.Fatal("delete", w.Code, w.Body.String())
	}
	if w = staffBoardRequest(e, http.MethodPut, "/communications/staff-board/abc/update", "application/json", `{}`, "en"); w.Code != 405 {
		t.Fatal("update method", w.Code)
	}
}

func TestStaffBoardHandlerErrors(t *testing.T) {
	invalid := &dto.StaffBoardInputError{Fields: []dto.StaffBoardInputField{
		{Name: "content", Codes: []string{"blank"}}, {Name: "document", Codes: []string{"json"}},
		{Name: "pinned", Codes: []string{"boolean"}}}}
	for _, c := range []struct {
		name     string
		err      error
		language string
		status   int
		body     string
	}{
		{"italian field errors", invalid, "", 400, `{"content":["Questo campo non può essere omesso."],"document":["Il valore deve essere un JSON valido."],"pinned":["Must be a valid boolean."]}`},
		{"english field errors", invalid, "en", 400, `{"content":["This field may not be blank."],"document":["Value must be valid JSON."],"pinned":["Must be a valid boolean."]}`},
		{"literal validate message", &dto.StaffBoardInputError{Fields: []dto.StaffBoardInputField{{Name: "non_field_errors", Codes: []string{"text:Documento non valido."}}}}, "en", 400, `{"non_field_errors":["Documento non valido."]}`},
		{"not found", domain.ErrStaffBoardNotFound, "en", 404, `{"error":"message not found."}`},
		{"edit not author", domain.ErrStaffBoardEditNotAuthor, "en", 403, `{"error":"Solo l’autore può modificare questo messaggio."}`},
		{"delete not author", domain.ErrStaffBoardDeleteNotAuthor, "en", 403, `{"error":"Solo l’autore può eliminare questo messaggio."}`},
		{"forbidden", domain.ErrStaffBoardForbidden, "en", 403, `{"detail":"You do not have permission to perform this action."}`},
		{"malformed id", &dto.MessageReadUUIDError{Detail: "badly formed hexadecimal UUID string"}, "en", 400, `["badly formed hexadecimal UUID string"]`},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := staffBoardTestServer(&staffBoardServiceTest{err: c.err})
			w := staffBoardRequest(e, http.MethodPatch, "/communications/staff-board/abc/update", "application/json", `{"content":""}`, c.language)
			var got, want any
			if w.Code != c.status || json.Unmarshal(w.Body.Bytes(), &got) != nil || json.Unmarshal([]byte(c.body), &want) != nil || !jsonEqual(got, want) {
				t.Fatalf("%d %s", w.Code, w.Body.String())
			}
		})
	}
	for _, err := range []error{dto.ErrStaffBoardDocumentUnhashable, service.ErrStaffBoardUnsupportedBody, errors.New("database down")} {
		e := staffBoardTestServer(&staffBoardServiceTest{err: err})
		w := staffBoardRequest(e, http.MethodPatch, "/communications/staff-board/abc/update", "application/json", `{}`, "en")
		if w.Code != 500 || !strings.Contains(w.Body.String(), "Server Error (500)") || strings.Contains(w.Body.String(), err.Error()) {
			t.Fatal(err, w.Code, w.Body.String())
		}
	}
}

func jsonEqual(a, b any) bool {
	left, _ := json.Marshal(a)
	right, _ := json.Marshal(b)
	return string(left) == string(right)
}
