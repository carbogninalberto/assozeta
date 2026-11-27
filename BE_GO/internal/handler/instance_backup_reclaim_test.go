package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
	"github.com/labstack/echo/v4"
)

type reclaimAccess struct {
	service.InstanceAdminBootstrap
	owner bool
}

func (a reclaimAccess) Access(context.Context, domain.Account) (*dto.InstanceAccess, error) {
	return &dto.InstanceAccess{IsAdministrator: a.owner}, nil
}

type reclaimRunner struct {
	service.InstanceBackupActionsRunner
	method, path string
	body         []byte
}

func (r *reclaimRunner) CallJSON(_ context.Context, method, path string, payload []byte, _ time.Duration) (*updaterrunner.Reply, error) {
	r.method, r.path, r.body = method, path, payload
	return &updaterrunner.Reply{Status: 409, Body: []byte(`{"error":"Manutenzione già avviata."}`)}, nil
}

func TestReclaimSpaceBackupAction(t *testing.T) {
	const actor = "a1000000-0000-4000-8000-000000700001"
	const path = "/instance/admin/backups/reclaim-space"
	accounts := messageReadsAccountsTest{actor: {ID: actor, Role: domain.RoleAssociation, Active: true}}
	for _, tc := range []struct {
		name, method, auth, body string
		owner                    bool
		want                     int
	}{
		{"anonymous", "POST", "", `{"confirm":true}`, false, 401},
		{"queue denied", "POST", "Bearer valid", `{"confirm":true}`, false, 403},
		{"cancel denied", "DELETE", "Bearer valid", "{}", false, 403},
		{"missing confirmation", "POST", "Bearer valid", "{}", true, 400},
		{"negative confirmation", "POST", "Bearer valid", `{"confirm":false}`, true, 400},
		{"queue refused by updater", "POST", "Bearer valid", `{"confirm":true}`, true, 409},
		{"cancel refused by updater", "DELETE", "Bearer valid", "{}", true, 409},
	} {
		t.Run(tc.name, func(t *testing.T) {
			admin := service.NewInstanceAdmin(nil, reclaimAccess{owner: tc.owner}, service.InstanceAdminEnvironment{}, nil)
			actors := service.NewActorAuthentication(accounts, &messageReadsTokensTest{sub: actor})
			identities := service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil)
			audit := func(*http.Request) (domain.LoginAuditContext, error) { return domain.LoginAuditContext{}, nil }
			runner := &reclaimRunner{}
			h := NewInstanceBackupActionsHandler(NewInstanceAdminHandler(admin, actors, identities, audit, paymentcategorylifecycle.New()),
				service.NewInstanceBackupActions(nil, runner, nil, nil, nil, service.BackupActionsLimits{}))
			reclaim, ok := any(h).(interface{ ReclaimSpace(echo.Context) error })
			if !ok {
				t.Fatal("missing reclaim-space backup action")
			}
			router := echo.New()
			router.Any(path, reclaim.ReclaimSpace)
			req := httptest.NewRequest(tc.method, path, strings.NewReader(tc.body))
			req.Header.Set("Authorization", tc.auth)
			req.Header.Set("Content-Type", "application/json")
			rec := httptest.NewRecorder()
			router.ServeHTTP(rec, req)
			if rec.Code != tc.want {
				t.Fatalf("got %d %s, want %d", rec.Code, rec.Body.String(), tc.want)
			}
			if tc.want == 409 {
				var body map[string]bool
				if err := json.Unmarshal(runner.body, &body); err != nil {
					t.Fatalf("invalid runner request %q: %v", runner.body, err)
				}
				if runner.method != tc.method || runner.path != "/backups/reclaim-space" || body["confirm"] != (tc.method == "POST") {
					t.Fatalf("unexpected runner call %s %s %s", runner.method, runner.path, runner.body)
				}
				if !strings.Contains(rec.Body.String(), "Manutenzione già avviata.") {
					t.Fatalf("missing runner reason in %s", rec.Body.String())
				}
			} else if runner.method != "" {
				t.Fatalf("unauthorized or unconfirmed request reached the runner")
			}
		})
	}
}
