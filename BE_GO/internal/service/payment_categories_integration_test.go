//go:build paymentcategoryintegration

package service

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

const (
	paymentCategoryDBOwner        = "19000000-0000-4000-8000-000000000001"
	paymentCategoryDBForeignOwner = "19000000-0000-4000-8000-000000000002"
	paymentCategoryDBOwnerless    = "19000000-0000-4000-8000-000000000003"
	paymentCategoryDBAssociation  = "19000000-0000-4000-8000-000000000010"
	paymentCategoryDBForeignAssoc = "19000000-0000-4000-8000-000000000011"
	paymentCategoryDBOwn          = "19000000-0000-4000-8000-000000000020"
	paymentCategoryDBGlobal       = "19000000-0000-4000-8000-000000000021"
	paymentCategoryDBForeign      = "19000000-0000-4000-8000-000000000022"
	paymentCategoryDBVAT          = "19000000-0000-4000-8000-000000000030"
)

func openPaymentCategoryIntegrationDB(t *testing.T) *sqlx.DB {
	t.Helper()
	dsn := os.Getenv("ASSOZETA_PAYMENT_CATEGORY_DSN")
	cfg, err := pgx.ParseConfig(dsn)
	if dsn == "" || err != nil || !strings.Contains(cfg.Database, "payment_category") || cfg.User == "" {
		t.Fatal("required private payment-category PostgreSQL fixture")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	db.SetMaxOpenConns(8)
	db.SetMaxIdleConns(8)
	t.Cleanup(func() { _ = db.Close() })
	cleanupPaymentCategoryIntegration(t, db)
	t.Cleanup(func() { cleanupPaymentCategoryIntegration(t, db) })
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	users := []struct {
		ID       string `json:"id"`
		Username string `json:"username"`
		Email    string `json:"email"`
	}{
		{paymentCategoryDBOwner, "payment-category-owner", "payment-category-owner@example.test"},
		{paymentCategoryDBForeignOwner, "payment-category-foreign", "payment-category-foreign@example.test"},
		{paymentCategoryDBOwnerless, "payment-category-ownerless", "payment-category-ownerless@example.test"},
	}
	usersJSON, err := json.Marshal(users)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `INSERT INTO bakney_user
 (password,is_superuser,username,first_name,last_name,email,is_staff,is_active,date_joined,user_id,role,
  two_fa,stripe_on_boarding_completed,enumerate_invoices,online_payments,balance_sheet_year,
  balance_sheet_start_day,balance_sheet_start_month,temporary_invoice_deletion,auto_archive,auto_mark_attendance,
  full_installments_plan,payment_date_equal_invoice_date,starting_number_invoices,auto_paid_payment,tables_settings,
  deleted,collaborator_role,dark_mode,integration_google_auto_sync,subscription_duration_equal_sport_year,
  subscription_duration,membership_duration,subscription_start_day,subscription_start_month,custom_end_date,
  subscription_end_day,subscription_end_month,membership_starting_number,disable_account_creation,
  force_account_creation,medical_certificate_notifications,hide_category_name,show_zero_payments,imported_from_associami)
 SELECT '!test-fixture',FALSE,r.username,'Fixture','Category',r.email,FALSE,TRUE,now(),r.id,1,
  FALSE,FALSE,FALSE,TRUE,1,1,1,FALSE,FALSE,FALSE,FALSE,FALSE,1,FALSE,'{}'::jsonb,
  FALSE,1,FALSE,FALSE,FALSE,1,1,1,1,FALSE,1,1,1,FALSE,FALSE,FALSE,FALSE,FALSE,FALSE
 FROM jsonb_to_recordset($1::jsonb) AS r(id uuid,username text,email text)`, string(usersJSON)); err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `INSERT INTO application_sportassociation
 (sport_association_id,user_id,denomination,tax_code,additional_sections,subscription_fee,
  multiple_subscription_fee,subscription_fee_plans,multiple_membership_fee,membership_fee_plans,
  enable_quotes_management,configuration,reviewed,deleted,review_url_enabled,imported_from_associami,
  show_regulation_to_members,show_regulation_to_both,show_regulation_to_athletes,
  show_demand_to_members,show_demand_to_both,show_demand_to_athletes)
 VALUES($1,$2,'Payment Category Owner','19000000001','{}'::jsonb,0,FALSE,'[]'::jsonb,FALSE,'[]'::jsonb,
  TRUE,'{}'::jsonb,FALSE,FALSE,FALSE,FALSE,TRUE,TRUE,TRUE,TRUE,TRUE,TRUE),
 ($3,$4,'Payment Category Foreign','19000000002','{}'::jsonb,0,FALSE,'[]'::jsonb,FALSE,'[]'::jsonb,
  TRUE,'{}'::jsonb,FALSE,FALSE,FALSE,FALSE,TRUE,TRUE,TRUE,TRUE,TRUE,TRUE)`,
		paymentCategoryDBAssociation, paymentCategoryDBOwner, paymentCategoryDBForeignAssoc, paymentCategoryDBForeignOwner); err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `INSERT INTO application_vatmanagement(vat_management_id,vat,deduction)
 VALUES($1,16,2)`, paymentCategoryDBVAT); err != nil {
		t.Fatal(err)
	}
	return db
}

func insertPaymentCategoryIntegration(t *testing.T, db *sqlx.DB, id, name string, association *string, created time.Time, deleted bool, vat *string) {
	t.Helper()
	if _, err := db.Exec(`INSERT INTO application_paymentcategory
 (payment_category_id,name,creation_date,sport_association_id,expense,tax_deductible,archived,type,vat_management_id,deleted)
 VALUES($1,$2,$3,$4,FALSE,FALSE,FALSE,1,$5,$6)`, id, name, created, association, vat, deleted); err != nil {
		t.Fatal(err)
	}
}

func cleanupPaymentCategoryIntegration(t *testing.T, db *sqlx.DB) {
	t.Helper()
	ids := []string{paymentCategoryDBOwner, paymentCategoryDBForeignOwner, paymentCategoryDBOwnerless}
	prefixes := []string{"19000000-0000-4000-8000-0000000000%"}
	if _, err := db.Exec(`DELETE FROM application_paymentcategory WHERE payment_category_id::text LIKE ANY($1)`, prefixes); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`DELETE FROM application_vatmanagement WHERE vat_management_id::text LIKE ANY($1)`, prefixes); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`DELETE FROM application_auditlogindex WHERE log_entry_id IN
 (SELECT id FROM auditlog_logentry WHERE object_pk LIKE ANY($1))`, prefixes); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`DELETE FROM auditlog_logentry WHERE object_pk LIKE ANY($1)`, prefixes); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`DELETE FROM application_sportassociation WHERE user_id=ANY($1)`, ids); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`DELETE FROM bakney_user WHERE user_id=ANY($1)`, ids); err != nil {
		t.Fatal(err)
	}
}

func paymentCategoryDBIdentity() domain.ResolvedIdentity {
	owner := &domain.Account{ID: paymentCategoryDBOwner, Role: domain.RoleAssociation, Active: true, Email: "payment-category-owner@example.test"}
	return domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner}
}

func TestPaymentCategoryRepositoryListLive(t *testing.T) {
	db := openPaymentCategoryIntegrationDB(t)
	association := paymentCategoryDBAssociation
	foreignAssociation := paymentCategoryDBForeignAssoc
	vat := paymentCategoryDBVAT
	created := time.Date(2026, 10, 23, 12, 0, 0, 0, time.UTC)
	insertPaymentCategoryIntegration(t, db, paymentCategoryDBOwn, "own", &association, created, false, &vat)
	insertPaymentCategoryIntegration(t, db, paymentCategoryDBGlobal, "global", nil, created.Add(time.Second), true, nil)
	insertPaymentCategoryIntegration(t, db, paymentCategoryDBForeign, "foreign", &foreignAssociation, created.Add(2*time.Second), false, nil)
	rows, err := NewPaymentCategories(repository.NewPaymentCategories(db), time.Now).List(context.Background(), paymentCategoryDBIdentity())
	if err != nil || len(rows) != 2 || rows[0].ID != paymentCategoryDBGlobal || rows[1].ID != paymentCategoryDBOwn || rows[1].VAT == nil {
		t.Fatal(rows, err)
	}
	if _, err = db.Exec(`UPDATE application_sportassociation SET deleted=true WHERE sport_association_id=$1`, association); err != nil {
		t.Fatal(err)
	}
	// Serializer PrimaryKeyRelatedField uses Django's original_objects default
	// manager, while mutation authorization uses the active-association manager.
	tx, err := repository.NewPaymentCategories(db).BeginPaymentCategories(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	exists, lookupErr := tx.AssociationExists(context.Background(), association)
	if rollbackErr := tx.Rollback(); rollbackErr != nil {
		t.Fatal(rollbackErr)
	}
	if lookupErr != nil || !exists {
		t.Fatal("related-field validation lost existing deleted association", exists, lookupErr)
	}
	rows, err = NewPaymentCategories(repository.NewPaymentCategories(db), time.Now).List(context.Background(), paymentCategoryDBIdentity())
	if err != nil || len(rows) != 1 || rows[0].ID != paymentCategoryDBGlobal {
		t.Fatal("owner without association must see globals", rows, err)
	}
}

func TestPaymentCategoryMutationsAtomicLive(t *testing.T) {
	for _, fault := range []string{"category", "audit", "index"} {
		t.Run(fault, func(t *testing.T) {
			db := openPaymentCategoryIntegrationDB(t)
			categoryID := "19000000-0000-4000-8000-000000000040"
			switch fault {
			case "category":
				_, err := db.Exec(`CREATE FUNCTION payment_category_fail_category() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic category failure'; END $$;
 CREATE TRIGGER payment_category_fail_category BEFORE INSERT ON application_paymentcategory FOR EACH ROW WHEN (NEW.payment_category_id='19000000-0000-4000-8000-000000000040') EXECUTE FUNCTION payment_category_fail_category()`)
				if err != nil {
					t.Fatal(err)
				}
				defer db.Exec(`DROP TRIGGER payment_category_fail_category ON application_paymentcategory; DROP FUNCTION payment_category_fail_category()`)
			case "audit":
				_, err := db.Exec(`CREATE FUNCTION payment_category_fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic audit failure'; END $$;
 CREATE TRIGGER payment_category_fail_audit BEFORE INSERT ON auditlog_logentry FOR EACH ROW WHEN (NEW.object_pk='19000000-0000-4000-8000-000000000040') EXECUTE FUNCTION payment_category_fail_audit()`)
				if err != nil {
					t.Fatal(err)
				}
				defer db.Exec(`DROP TRIGGER payment_category_fail_audit ON auditlog_logentry; DROP FUNCTION payment_category_fail_audit()`)
			case "index":
				_, err := db.Exec(`CREATE FUNCTION payment_category_fail_index() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic index failure'; END $$;
 CREATE TRIGGER payment_category_fail_index BEFORE INSERT ON application_auditlogindex FOR EACH ROW EXECUTE FUNCTION payment_category_fail_index()`)
				if err != nil {
					t.Fatal(err)
				}
				defer db.Exec(`DROP TRIGGER payment_category_fail_index ON application_auditlogindex; DROP FUNCTION payment_category_fail_index()`)
			}
			input := dto.PaymentCategoryWrite{NamePresent: true, Name: stringPointer("atomic"),
				CategoryIDPresent: true, CategoryID: &categoryID,
				VAT: dto.PaymentCategoryVATWrite{Present: true, Value: dto.PaymentCategoryVATInput{
					ID:        stringPointer("19000000-0000-4000-8000-000000000041"),
					VAT:       func() *uint16 { value := uint16(16); return &value }(),
					Deduction: func() *uint16 { value := uint16(2); return &value }(),
				}}}
			if _, err := NewPaymentCategories(repository.NewPaymentCategories(db), time.Now).Add(context.Background(), paymentCategoryDBIdentity(), input, domain.LoginAuditContext{}); err == nil {
				t.Fatal("synthetic fault did not fail mutation")
			}
			var count int
			if err := db.Get(&count, `SELECT count(*) FROM application_paymentcategory WHERE payment_category_id=$1`, categoryID); err != nil || count != 0 {
				t.Fatal("category write survived rollback", count, err)
			}
			if err := db.Get(&count, `SELECT count(*) FROM application_vatmanagement WHERE vat_management_id=$1`, "19000000-0000-4000-8000-000000000041"); err != nil || count != 0 {
				t.Fatal("VAT write survived rollback", count, err)
			}
			if err := db.Get(&count, `SELECT count(*) FROM auditlog_logentry WHERE object_pk LIKE '19000000-0000-4000-8000-00000000004%'`); err != nil || count != 0 {
				t.Fatal("audit survived rollback", count, err)
			}
		})
	}
}

func TestPaymentCategoryConcurrentPartialUpdatesLive(t *testing.T) {
	db := openPaymentCategoryIntegrationDB(t)
	association := paymentCategoryDBAssociation
	insertPaymentCategoryIntegration(t, db, paymentCategoryDBOwn, "before", &association, time.Now().UTC(), false, nil)
	flow := NewPaymentCategories(repository.NewPaymentCategories(db), time.Now)
	name := "after-name"
	archived := true
	inputs := []dto.PaymentCategoryWrite{{NamePresent: true, Name: &name}, {ArchivedPresent: true, Archived: &archived}}
	start := make(chan struct{})
	results := make(chan error, 2)
	var wait sync.WaitGroup
	for i := range inputs {
		wait.Add(1)
		go func(input dto.PaymentCategoryWrite) {
			defer wait.Done()
			<-start
			_, err := flow.Update(context.Background(), paymentCategoryDBIdentity(), paymentCategoryDBOwn, input, domain.LoginAuditContext{})
			results <- err
		}(inputs[i])
	}
	close(start)
	wait.Wait()
	close(results)
	for err := range results {
		if err != nil {
			t.Fatal(err)
		}
	}
	var row struct {
		Name     string `db:"name"`
		Archived bool   `db:"archived"`
	}
	if err := db.Get(&row, `SELECT name,archived FROM application_paymentcategory WHERE payment_category_id=$1`, paymentCategoryDBOwn); err != nil {
		t.Fatal(err)
	}
	if row.Name != name || !row.Archived {
		t.Fatal("concurrent partial update lost a field", row)
	}
}

func TestPaymentCategoryDeletePreservesReferencesLive(t *testing.T) {
	db := openPaymentCategoryIntegrationDB(t)
	association := paymentCategoryDBAssociation
	insertPaymentCategoryIntegration(t, db, paymentCategoryDBOwn, "referenced", &association, time.Now().UTC(), false, nil)
	flow := NewPaymentCategories(repository.NewPaymentCategories(db), time.Now)
	for i := 0; i < 2; i++ {
		if err := flow.Delete(context.Background(), paymentCategoryDBIdentity(), paymentCategoryDBOwn, domain.LoginAuditContext{}); err != nil {
			t.Fatal(err)
		}
	}
	var deleted bool
	if err := db.Get(&deleted, `SELECT deleted FROM application_paymentcategory WHERE payment_category_id=$1`, paymentCategoryDBOwn); err != nil || !deleted {
		t.Fatal("category was not soft-deleted", deleted, err)
	}
	var audits int
	if err := db.Get(&audits, `SELECT count(*) FROM auditlog_logentry WHERE object_pk=$1 AND changes='{"deleted":["False","True"]}'::jsonb`, paymentCategoryDBOwn); err != nil || audits != 1 {
		t.Fatal("repeated delete audit", audits, err)
	}
}
