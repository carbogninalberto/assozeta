package handler

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/google/uuid"
	"github.com/labstack/echo/v4"
)

type messageReadsTokensTest struct {
	calls int
	sub   string
}

func (t *messageReadsTokensTest) Verify(token, kind string) (map[string]any, error) {
	t.calls++
	if token != "valid" {
		return nil, errors.New("invalid token")
	}
	return map[string]any{"user_id": t.sub}, nil
}

type messageReadsAccountsTest map[string]domain.Account

func (a messageReadsAccountsTest) ByID(_ context.Context, id string) (domain.Account, error) {
	value, ok := a[id]
	if !ok {
		return domain.Account{}, domain.ErrAccountNotFound
	}
	return value, nil
}

type messageReadsStoreHandlerTest struct {
	visit                  func(context.Context, func(domain.MessageReadRecord) error) error
	list, detail           []domain.MessageReadRecord
	transactions           []domain.MessageTransactionReadRecord
	id                     string
	listCalls, detailCalls int
}

func (s *messageReadsStoreHandlerTest) VisitMessages(ctx context.Context, _ string, consume func(domain.MessageReadRecord) error) error {
	s.listCalls++
	if s.visit != nil {
		return s.visit(ctx, consume)
	}
	for _, row := range s.list {
		if err := consume(row); err != nil {
			return err
		}
	}
	return nil
}
func (s *messageReadsStoreHandlerTest) MessageTransactions(_ context.Context, _ string, id string) ([]domain.MessageTransactionReadRecord, error) {
	s.detailCalls++
	s.id = id
	return s.transactions, nil
}

func messageReadsHandlerFor(t *testing.T, store *messageReadsStoreHandlerTest, tokens *messageReadsTokensTest,
	accounts messageReadsAccountsTest, scoped service.ScopedRequestChecker,
) *echo.Echo {
	t.Helper()
	h := NewMessageReadsHandler(service.NewMessageReads(store), service.NewActorAuthentication(accounts, tokens),
		service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), scoped))
	e := echo.New()
	e.Any("/communications/messages/list", h.List)
	e.Any("/communications/messages/:message_id/detail", h.Detail)
	return e
}

func messageReadsHandler(t *testing.T, store *messageReadsStoreHandlerTest, tokens *messageReadsTokensTest) (*echo.Echo, string) {
	t.Helper()
	ownerID := "a1000000-0000-4000-8000-000000700001"
	tokens.sub = ownerID
	owner := domain.Account{ID: ownerID, Role: domain.RoleAssociation, Active: true}
	accounts := messageReadsAccountsTest{ownerID: owner}
	e := messageReadsHandlerFor(t, store, tokens, accounts, nil)
	return e, ownerID
}

func TestMessageReadsHTTPProjectsListDetailAndNotFound(t *testing.T) {
	messageID := uuid.MustParse("a1000000-0000-4000-8000-000000702001")
	associationID := uuid.MustParse("a1000000-0000-4000-8000-000000701001")
	transactionID := uuid.MustParse("a1000000-0000-4000-8000-000000703001")
	created := time.Date(2026, 10, 23, 8, 9, 10, 123456000, time.UTC)
	store := &messageReadsStoreHandlerTest{
		list: []domain.MessageReadRecord{{ID: messageID, AssociationID: associationID, Type: "EMAIL", Message: "A & B", CreatedAt: &created}},
		transactions: []domain.MessageTransactionReadRecord{{ID: transactionID, MessageID: messageID,
			Recipient: "person@example.test", SentOn: created}},
	}
	e, _ := messageReadsHandler(t, store, &messageReadsTokensTest{})
	for _, tc := range []struct {
		path, authorization string
		status              int
		contains            string
	}{
		{"/communications/messages/list", "Bearer valid", 200, `"subject":"None"`},
		{"/communications/messages/" + messageID.String() + "/detail", "Bearer valid", 200, `"recipient":"person@example.test"`},
		{"/communications/messages/not-a-uuid/detail", "Bearer valid", 400, `"badly formed hexadecimal UUID string"`},
		{"/communications/messages/list", "", 401, `"Authentication credentials were not provided."`},
		{"/communications/messages/" + messageID.String() + "/detail", "Bearer invalid", 401, `"Given token not valid for any token type"`},
	} {
		r := httptest.NewRequest("GET", tc.path, nil)
		r.Header.Set("Accept-Language", "en")
		r.Header.Set("Authorization", tc.authorization)
		w := httptest.NewRecorder()
		e.ServeHTTP(w, r)
		if w.Code != tc.status || !strings.Contains(w.Body.String(), tc.contains) {
			t.Fatalf("%s -> %d %s", tc.path, w.Code, w.Body.String())
		}
	}

	r := httptest.NewRequest("GET", "/communications/messages/"+messageID.String()+"/detail", nil)
	r.Header.Set("Accept-Language", "en")
	r.Header.Set("Authorization", "Bearer valid")
	w := httptest.NewRecorder()
	e.ServeHTTP(w, r)
	var detail []map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &detail); err != nil {
		t.Fatal(err)
	}
	expectedDetail := []map[string]any{{
		"message_transaction_id": transactionID.String(),
		"message":                messageID.String(),
		"recipient":              "person@example.test",
		"sent_on":                "2026-10-23T08:09:10.123456Z",
		"delivered_on":           nil,
		"opened_on":              nil,
		"clicked_on":             nil,
		"bounced_on":             nil,
		"bounced_reason":         nil,
		"bounced_type":           nil,
		"bounced_status":         nil,
	}}
	if w.Code != 200 || !reflect.DeepEqual(detail, expectedDetail) {
		t.Fatalf("detail JSON = %d %#v", w.Code, detail)
	}
	if _, present := detail[0]["message_id"]; present {
		t.Fatal("detail response contains message_id instead of Django's message field")
	}

	store.transactions = nil
	r = httptest.NewRequest("GET", "/communications/messages/"+messageID.String()+"/detail", nil)
	r.Header.Set("Accept-Language", "en")
	r.Header.Set("Authorization", "Bearer valid")
	w = httptest.NewRecorder()
	e.ServeHTTP(w, r)
	if w.Code != 404 || w.Body.String() != "{\"msg\":\"message not found.\"}\n" {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
}

func TestMessageReadsTransportAdmissionOrderAndOptions(t *testing.T) {
	for _, tc := range []struct {
		name, method, path, accept, authorization string
		forged                                    bool
		status, tokenCalls                        int
		bodyContains                              string
	}{
		{"format before identity", "GET", "/communications/messages/list?format=xml", "text/html", "", true, 404, 0, `"Not found."`},
		{"accept before identity", "GET", "/communications/messages/list", "text/html", "", true, 406, 0, `"Could not satisfy`},
		{"credential before method", "POST", "/communications/messages/list", "", "Bearer invalid", false, 401, 1, `"Given token not valid for any token type"`},
		{"anonymous options", "OPTIONS", "/communications/messages/list", "", "", false, 401, 0, `"Authentication credentials`},
		{"anonymous read", "GET", "/communications/messages/list", "", "", false, 401, 0, `"Authentication credentials`},
		{"anonymous forged scope", "GET", "/communications/messages/list", "", "", true, 403, 0, `"impersonation_invalid":"True"`},
		{"owner options", "OPTIONS", "/communications/messages/list", "", "Bearer valid", false, 200, 1, `"name":"Communication Messages List"`},
		{"owner wrong method", "POST", "/communications/messages/list", "", "Bearer valid", false, 405, 1, `"Method \"POST\" not allowed."`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := &messageReadsStoreHandlerTest{}
			tokens := &messageReadsTokensTest{}
			e, _ := messageReadsHandler(t, store, tokens)
			r := httptest.NewRequest(tc.method, tc.path, nil)
			r.Header.Set("Accept-Language", "en")
			r.Header.Set("Accept", tc.accept)
			r.Header.Set("Authorization", tc.authorization)
			if tc.forged {
				r.Header.Set("X-Impersonation-Id", "forged")
				r.Header.Set("User-Id", "a1000000-0000-4000-8000-000000700001")
			}
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if w.Code != tc.status || tokens.calls != tc.tokenCalls || !strings.Contains(w.Body.String(), tc.bodyContains) {
				t.Fatalf("%d %s calls=%d", w.Code, w.Body.String(), tokens.calls)
			}
			if store.listCalls != 0 || store.detailCalls != 0 {
				t.Fatal("transport admission/error path read message data", store)
			}
			if w.Header().Get("Allow") != "GET, OPTIONS" {
				t.Fatal(w.Header())
			}
			if tc.name == "owner options" {
				var value map[string]any
				if json.Unmarshal(w.Body.Bytes(), &value) != nil || value["name"] != "Communication Messages List" {
					t.Fatal(w.Body.String())
				}
			}
		})
	}
}

func TestMessageReadsAssociationPermissionPrecedesEveryDispatch(t *testing.T) {
	ownerID := "a1000000-0000-4000-8000-000000700001"
	athleteID := "a1000000-0000-4000-8000-000000700004"
	messageID := "a1000000-0000-4000-8000-000000702001"
	accounts := messageReadsAccountsTest{
		ownerID:   {ID: ownerID, Role: domain.RoleAssociation, Active: true},
		athleteID: {ID: athleteID, Role: domain.RoleAthlete, Active: true},
	}
	for _, tc := range []struct {
		name, actorID, method, path string
		status                      int
	}{
		{"athlete list options", athleteID, "OPTIONS", "/communications/messages/list", 403},
		{"athlete list post", athleteID, "POST", "/communications/messages/list", 403},
		{"athlete list head", athleteID, "HEAD", "/communications/messages/list", 403},
		{"athlete detail options", athleteID, "OPTIONS", "/communications/messages/" + messageID + "/detail", 403},
		{"athlete detail post", athleteID, "POST", "/communications/messages/" + messageID + "/detail", 403},
		{"athlete detail head", athleteID, "HEAD", "/communications/messages/" + messageID + "/detail", 403},
		{"owner detail options", ownerID, "OPTIONS", "/communications/messages/" + messageID + "/detail", 200},
		{"owner detail post", ownerID, "POST", "/communications/messages/" + messageID + "/detail", 405},
		{"owner detail head", ownerID, "HEAD", "/communications/messages/" + messageID + "/detail", 405},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := &messageReadsStoreHandlerTest{}
			tokens := &messageReadsTokensTest{sub: tc.actorID}
			e := messageReadsHandlerFor(t, store, tokens, accounts, nil)
			r := httptest.NewRequest(tc.method, tc.path, nil)
			r.Header.Set("Accept-Language", "en")
			r.Header.Set("Authorization", "Bearer valid")
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if w.Code != tc.status {
				t.Fatalf("status = %d, want %d: %s", w.Code, tc.status, w.Body.String())
			}
			if tc.status == 403 && !strings.Contains(w.Body.String(), "You do not have permission to perform this action.") {
				t.Fatal(w.Body.String())
			}
			if store.listCalls != 0 || store.detailCalls != 0 {
				t.Fatal("method dispatch bypassed association permission", store)
			}
		})
	}
}

type messageReadsRelationsHandlerTest struct {
	target      domain.Account
	association string
}

func (r messageReadsRelationsHandlerTest) AssociationOwnedBy(context.Context, string) (string, error) {
	return r.association, nil
}
func (r messageReadsRelationsHandlerTest) EligibleTarget(context.Context, string, string, string) (domain.Account, error) {
	return r.target, nil
}
func (r messageReadsRelationsHandlerTest) PrimaryExportOwner(context.Context) (domain.Account, error) {
	return domain.Account{}, domain.ErrAssociationNotFound
}

type messageReadsSessionsHandlerTest struct {
	record domain.ImpersonationSession
}

func (s messageReadsSessionsHandlerTest) Get(context.Context, string) (domain.ImpersonationSession, error) {
	return s.record, nil
}

func TestMessageReadsImpersonatedAthleteGroupCannotBypassAssociationPermission(t *testing.T) {
	ownerID := "a1000000-0000-4000-8000-000000700001"
	athleteID := "a1000000-0000-4000-8000-000000700004"
	associationID := "a1000000-0000-4000-8000-000000701001"
	sessionID := "a1000000-0000-4000-8000-000000799998"
	groupID := "a1000000-0000-4000-8000-000000799999"
	owner := domain.Account{ID: ownerID, Role: domain.RoleAssociation, Active: true}
	athlete := domain.Account{ID: athleteID, Role: domain.RoleAthlete, Active: true}
	accounts := messageReadsAccountsTest{ownerID: owner, athleteID: athlete}
	store := &messageReadsStoreHandlerTest{}
	tokens := &messageReadsTokensTest{sub: ownerID}
	h := NewMessageReadsHandler(service.NewMessageReads(store), service.NewActorAuthentication(accounts, tokens),
		service.NewIdentityResolver(
			accounts,
			messageReadsRelationsHandlerTest{target: athlete, association: associationID},
			messageReadsSessionsHandlerTest{record: domain.ImpersonationSession{
				ActorID: ownerID, TargetID: athleteID, AssociationID: associationID,
			}},
			service.NewPermissions(),
			service.NewScopedRequestGuard(nil, nil, nil),
		))
	e := echo.New()
	e.Any("/communications/messages/list", h.List)
	r := httptest.NewRequest("GET", "/communications/messages/list", nil)
	r.Header.Set("Accept-Language", "en")
	r.Header.Set("Authorization", "Bearer valid")
	r.Header.Set("X-Impersonation-Id", sessionID)
	r.Header.Set("User-Id", athleteID)
	r.Header.Set("X-Group-Id", groupID)
	w := httptest.NewRecorder()
	e.ServeHTTP(w, r)
	if w.Code != 403 || !strings.Contains(w.Body.String(), "Operazione non disponibile nel contesto dell’associazione.") {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	if store.listCalls != 0 || store.detailCalls != 0 {
		t.Fatal("group reference/impersonated athlete admission read message data", store)
	}
}

type messageListFaultSpool struct {
	messageListSpool
	fail   string
	closed bool
	reads  int
	writes int
	seeks  int
}

func (s *messageListFaultSpool) Write(p []byte) (int, error) {
	s.writes++
	if s.fail == "write" && s.writes == 5 {
		return 0, errors.New("spool write")
	}
	if s.fail == "short write" && s.writes == 5 {
		return len(p) - 1, nil
	}
	return s.messageListSpool.Write(p)
}
func (s *messageListFaultSpool) Seek(n int64, w int) (int64, error) {
	s.seeks++
	if s.fail == "seek" || (s.fail == "second seek" && s.seeks == 2) {
		return 0, errors.New("spool seek")
	}
	return s.messageListSpool.Seek(n, w)
}
func (s *messageListFaultSpool) Read(p []byte) (int, error) {
	s.reads++
	if s.fail == "read" && s.reads == 2 {
		return 0, errors.New("spool read")
	}
	return s.messageListSpool.Read(p)
}
func (s *messageListFaultSpool) Close() error { s.closed = true; return s.messageListSpool.Close() }

func TestMessageReadsPrivateSpoolCleanupAndPreResponseFailures(t *testing.T) {
	for _, kind := range []string{"success", "create", "write", "short write", "seek", "second seek", "read", "late store", "cancel"} {
		t.Run(kind, func(t *testing.T) {
			dir := t.TempDir()
			t.Setenv("TMPDIR", dir)
			tokens := &messageReadsTokensTest{sub: "a1000000-0000-4000-8000-000000700001"}
			accounts := messageReadsAccountsTest{"a1000000-0000-4000-8000-000000700001": {ID: "a1000000-0000-4000-8000-000000700001", Role: domain.RoleAssociation, Active: true}}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			store := &messageReadsStoreHandlerTest{visit: func(_ context.Context, consume func(domain.MessageReadRecord) error) error {
				for i := 0; i < 4; i++ {
					if err := consume(domain.MessageReadRecord{Message: strings.Repeat("x", 20000)}); err != nil {
						return err
					}
				}
				if kind == "late store" {
					return errors.New("late rows failure")
				}
				if kind == "cancel" {
					cancel()
				}
				return nil
			}}
			h := NewMessageReadsHandler(service.NewMessageReads(store), service.NewActorAuthentication(accounts, tokens), service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil))
			var spool *messageListFaultSpool
			h.newListSpool = func() (messageListSpool, error) {
				if kind == "create" {
					return nil, errors.New("create failed")
				}
				f, err := openMessageListSpool()
				if err != nil {
					return nil, err
				}
				file := f.(*os.File)
				info, err := file.Stat()
				if err != nil {
					t.Fatal(err)
				}
				if info.Mode().Perm() != 0600 {
					t.Fatalf("permissions: %v", info.Mode())
				}
				if _, err = os.Stat(file.Name()); !os.IsNotExist(err) {
					t.Fatalf("spool should be unlinked immediately: %v", err)
				}
				spool = &messageListFaultSpool{messageListSpool: f, fail: kind}
				return spool, nil
			}
			e := echo.New()
			e.GET("/communications/messages/list", h.List)
			r := httptest.NewRequest("GET", "/communications/messages/list", nil).WithContext(ctx)
			r.Header.Set("Authorization", "Bearer valid")
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if kind == "success" {
				if w.Code != 200 || !json.Valid(w.Body.Bytes()) {
					t.Fatal(w.Code, w.Body.String())
				}
			} else {
				if w.Code != 500 || strings.Contains(w.Body.String(), "xxxx") || strings.Contains(w.Body.String(), "spool") {
					t.Fatalf("partial success: %d %s", w.Code, w.Body.String())
				}
			}
			if kind != "create" && spool == nil {
				t.Fatal("failure path never created spool")
			}
			if spool != nil && !spool.closed {
				t.Fatal("spool not closed")
			}
			entries, err := os.ReadDir(dir)
			if err != nil || len(entries) != 0 {
				t.Fatal("spool leaked", entries, err)
			}
			if kind == "create" && store.listCalls != 0 {
				t.Fatal("read despite creation error")
			}
		})
	}
}

// Response errors after commit must be returned, with the spool still closed.
type messageListBrokenResponse struct {
	header http.Header
	status int
}

func (w *messageListBrokenResponse) Header() http.Header       { return w.header }
func (w *messageListBrokenResponse) WriteHeader(status int)    { w.status = status }
func (w *messageListBrokenResponse) Write([]byte) (int, error) { return 0, io.ErrClosedPipe }
func TestMessageReadsResponseWriteFailureClosesSpool(t *testing.T) {
	tokens := &messageReadsTokensTest{sub: "a1000000-0000-4000-8000-000000700001"}
	accounts := messageReadsAccountsTest{"a1000000-0000-4000-8000-000000700001": {ID: "a1000000-0000-4000-8000-000000700001", Role: domain.RoleAssociation, Active: true}}
	h := NewMessageReadsHandler(service.NewMessageReads(&messageReadsStoreHandlerTest{}), service.NewActorAuthentication(accounts, tokens), service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil))
	var spool *messageListFaultSpool
	h.newListSpool = func() (messageListSpool, error) {
		f, err := openMessageListSpool()
		if err != nil {
			return nil, err
		}
		spool = &messageListFaultSpool{messageListSpool: f}
		return spool, nil
	}
	r := httptest.NewRequest("GET", "/communications/messages/list", nil)
	r.Header.Set("Authorization", "Bearer valid")
	w := &messageListBrokenResponse{header: make(http.Header)}
	err := h.List(echo.New().NewContext(r, w))
	if !errors.Is(err, io.ErrClosedPipe) || spool == nil || !spool.closed {
		t.Fatal(err, spool)
	}
}

// Exercise the public production option with a deliberately unusable native
// temp directory. Observe the real file without replacing its factory.
func TestMessageReadsConfiguredSpoolDirectoryAndFailureCleanup(t *testing.T) {
	for _, kind := range []string{"success", "write", "late store", "missing directory"} {
		t.Run(kind, func(t *testing.T) {
			dir := t.TempDir()
			t.Setenv("TMPDIR", filepath.Join(dir, "unusable-native-temp"))
			selected := dir
			if kind == "missing directory" {
				selected = filepath.Join(dir, "missing")
			}
			h, _ := messageListLazyHandler(4, nil)
			var opened *os.File
			sentinel := errors.New("consumer spool failure")
			store := &messageReadsStoreHandlerTest{visit: func(_ context.Context, consume func(domain.MessageReadRecord) error) error {
				// The descriptor seam observes the configured constructor's factory; it
				// never chooses a directory or supplies a replacement spool.
				for i := 0; i < 4; i++ {
					if err := consume(domain.MessageReadRecord{Message: "payload"}); err != nil {
						return err
					}
				}
				if kind == "late store" {
					return sentinel
				}
				return nil
			}}
			configured := NewMessageReadsHandler(service.NewMessageReads(store), h.actors, h.identities, WithMessageListSpoolDirectory(selected))
			factory := configured.newListSpool
			configured.newListSpool = func() (messageListSpool, error) {
				spool, err := factory()
				if err != nil {
					return nil, err
				}
				opened = spool.(*os.File)
				if filepath.Dir(opened.Name()) != selected {
					t.Fatal("configured spool directory ignored")
				}
				info, err := opened.Stat()
				if err != nil || info.Mode().Perm() != 0600 {
					t.Fatal("private file mode lost", err)
				}
				if _, err := os.Stat(opened.Name()); !os.IsNotExist(err) {
					t.Fatal("spool not immediately unlinked", err)
				}
				if kind == "write" {
					return &messageListFaultSpool{messageListSpool: spool, fail: "write"}, nil
				}
				return spool, nil
			}
			e := echo.New()
			e.GET("/communications/messages/list", configured.List)
			r := httptest.NewRequest("GET", "/communications/messages/list", nil)
			r.Header.Set("Authorization", "Bearer valid")
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if kind == "success" {
				if w.Code != 200 || !json.Valid(w.Body.Bytes()) || !strings.Contains(w.Body.String(), "payload") {
					t.Fatal(w.Code, w.Body.String())
				}
			} else if w.Code != 500 || strings.Contains(w.Body.String(), "payload") || strings.Contains(w.Body.String(), selected) {
				t.Fatal("pre-response failure leaked", w.Code, w.Body.String())
			}
			if kind == "missing directory" {
				if opened != nil || store.listCalls != 0 {
					t.Fatal("failed creation read data or opened a spool")
				}
			} else if opened == nil {
				t.Fatal("configured spool never opened")
			} else if _, err := opened.Stat(); !errors.Is(err, os.ErrClosed) {
				t.Fatal("spool descriptor leaked", err)
			}
			entries, err := os.ReadDir(dir)
			if err != nil || len(entries) != 0 {
				t.Fatal("spool path leaked", entries, err)
			}
		})
	}
}
