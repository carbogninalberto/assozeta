//go:build profileupdateintegration

package service

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

// The existing response projection deliberately returns every tenant group.
// This proves constant statement count, not constant response size/work.
func TestProfileUpdateCollaboratorResponsePostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN")
	if dsn == "" {
		t.Fatal("run accounts.py --only profile-update")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	q := &profileMutationQueries{}
	cfg.Tracer = q
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	const person = "00000000-0000-4000-8000-000000099009"
	const owner = "00000000-0000-4000-8000-000000000008"
	const association = "00000000-0000-4000-8000-000000000080"
	const foreign = "00000000-0000-4000-8000-000000000090"
	clean := func() {
		cleanup, stop := context.WithTimeout(context.Background(), 5*time.Second)
		defer stop()
		if _, e := db.ExecContext(cleanup, `DELETE FROM application_group WHERE group_id::text LIKE '81000000-0000-4000-8000-%'`); e != nil {
			t.Error(e)
		}
	}
	var collision, base int
	if err = db.GetContext(ctx, &collision, `SELECT count(*) FROM application_group WHERE group_id::text LIKE '81000000-0000-4000-8000-%'`); err != nil || collision != 0 {
		t.Fatal("private fixture namespace collision", err)
	}
	defer clean()
	if err = db.GetContext(ctx, &base, `SELECT count(*) FROM application_group WHERE sport_association_id=$1`, association); err != nil {
		t.Fatal(err)
	}
	account := &domain.Account{ID: person, Role: domain.RoleCollaborator, ConnectedUserID: func() *string { v := owner; return &v }()}
	identity := domain.ResolvedIdentity{Actor: account, Effective: account, ScopeUser: &domain.Account{ID: owner, Role: domain.RoleAssociation}}
	flow := NewProfileUpdateResponses(NewLoginProfiles(repository.NewLoginProfiles(db), time.Now), NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), nil, domain.RegistrationChoices{})
	for _, count := range []int{0, 16, 128} {
		clean()
		if _, err = db.ExecContext(ctx, `INSERT INTO application_group(group_id,name,description,sport_association_id) SELECT ('81000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,'Profile response '||i,'fixture',CASE WHEN i<=$1 THEN $2::uuid ELSE $3::uuid END FROM generate_series(1,$1::int+33) i`, count, association, foreign); err != nil {
			t.Fatal(err)
		}
		q.reset()
		result, e := flow.Read(ctx, identity)
		if e != nil || result.UserData.UserID != person || result.UserData.SportAssociation == nil || result.UserData.SportAssociation.ID != association || len(result.UserData.SportAssociation.Groups) != base+count || result.UserData.PreviewAndCustomFeatures != nil {
			t.Fatal("scoped response projection", count, result, e)
		}
		// One person SELECT + two association SELECTs + two acknowledged shared fences.
		q.expect(t, 3+2, 0, 0)
	}
	clean()
	for _, scope := range []string{"", "00000000-0000-4000-8000-000000000009"} {
		wrong := identity
		wrong.ScopeUser = &domain.Account{ID: scope}
		q.reset()
		result, e := flow.Read(ctx, wrong)
		if !errors.Is(e, domain.ErrInvalidScopedRequest) || result != nil {
			t.Fatal("foreign scope accepted", e)
		}
		q.expect(t, 1+1, 0, 0) // No association query, only the personal snapshot/fence.
	}
	q.reset()
	tx, err := repository.NewProfileUpdates(db).BeginProfileUpdate(ctx)
	if err != nil {
		t.Fatal(err)
	}
	locked, err := tx.ProfilePersonForUpdate(ctx, person)
	finish := tx.Rollback()
	if err != nil || finish != nil || locked.ConnectedUserID == nil || *locked.ConnectedUserID != owner {
		t.Fatal("locked owner not projected", locked, err, finish)
	}
	q.expect(t, 1+1, 0, 0)
}
