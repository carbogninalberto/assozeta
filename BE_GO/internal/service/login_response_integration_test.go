//go:build responseintegration

package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/loginimage"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type responseQueries struct{ count atomic.Int64 }

func (q *responseQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	sql := strings.ToLower(strings.TrimSpace(d.SQL))
	if strings.HasPrefix(sql, "select") || strings.HasPrefix(sql, "insert") || strings.HasPrefix(sql, "update") {
		q.count.Add(1)
	}
	return ctx
}
func (*responseQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

type responseFixtureCase struct {
	Name, ID, Outcome          string
	Owner                      *string
	Role, Queries, Instructors int
	Admin, Created             bool
	Flags                      []bool
	Expected                   json.RawMessage
}

// The Django fixture records business queries. Go additionally acknowledges one
// server fence for each profile, association, settings or card transaction reached.
func responseFenceQueries(tc responseFixtureCase) int {
	if tc.Queries == 0 {
		return 0
	}
	if tc.Queries == 1 {
		return 1
	}
	if tc.Outcome == "association" {
		return 2
	}
	return 4
}

func responseChoices(t *testing.T, flags []bool) domain.RegistrationChoices {
	t.Helper()
	values := map[string]string{"AWS_ACCESS_KEY_ID": "fixture-access", "AWS_SECRET_ACCESS_KEY": "fixture-secret", "AWS_STORAGE_BUCKET_NAME": "fixture-bucket", "AWS_S3_REGION_NAME": "us-east-1", "AWS_S3_ENDPOINT_URL": "http://127.0.0.1:9000", "AWS_LOCATION": "private", "DBTYPE": "POSTGRES", "DBHOST": "/tmp/synthetic", "DBNAME": "synthetic", "DBUSER": "synthetic", "SECRET_KEY": "synthetic-session-secret", "DEBUG": "False", "SOCIAL_AUTH_GOOGLE_OAUTH2_KEY": "", "SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET": "", "JWT_SECRET_KEY": "synthetic-private", "JWT_PUBLIC_KEY": "synthetic-public"}
	for i, key := range []string{"FAMILY_REGISTRATION_ENABLED", "FEDERATION_REGISTRATION_ENABLED", "MULTIPLE_REGISTRATION_ENABLED"} {
		values[key] = strconv.FormatBool(flags[i])
	}
	cfg, err := config.Parse(values)
	if err != nil {
		t.Fatal(err)
	}
	return domain.RegistrationChoices{Family: cfg.FamilyRegistrationEnabled, Federation: cfg.FederationRegistrationEnabled, Multiple: cfg.MultipleRegistrationEnabled}
}
func TestLoginResponsePostgres(t *testing.T) {
	path := os.Getenv("ASSOZETA_LOGIN_RESPONSE_FIXTURE")
	if path == "" {
		t.Fatal("run TESTS/adapters/django/accounts.py")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Now    string
		Tokens jwt.Pair
		Cases  []responseFixtureCase
		Files  []struct {
			Name, Lines string
			Process     map[string]string
			Expected    []bool
		}
		Booleans []struct {
			Value    string
			Expected bool
		}
	}
	if json.Unmarshal(data, &fixture) != nil || len(fixture.Cases) != 48 {
		t.Fatal("invalid response fixture")
	}
	now, err := time.Parse(time.RFC3339Nano, fixture.Now)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range fixture.Booleans {
		values := map[string]string{"AWS_ACCESS_KEY_ID": "fixture-access", "AWS_SECRET_ACCESS_KEY": "fixture-secret", "AWS_STORAGE_BUCKET_NAME": "fixture-bucket", "AWS_S3_REGION_NAME": "us-east-1", "AWS_S3_ENDPOINT_URL": "http://127.0.0.1:9000", "AWS_LOCATION": "private", "DBTYPE": "POSTGRES", "DBHOST": "/tmp/synthetic", "DBNAME": "synthetic", "DBUSER": "synthetic", "SECRET_KEY": "synthetic-session-secret", "DEBUG": "False", "SOCIAL_AUTH_GOOGLE_OAUTH2_KEY": "", "SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET": "", "JWT_SECRET_KEY": "synthetic-private", "JWT_PUBLIC_KEY": "synthetic-public"}
		for _, key := range []string{"FAMILY_REGISTRATION_ENABLED", "FEDERATION_REGISTRATION_ENABLED", "MULTIPLE_REGISTRATION_ENABLED"} {
			values[key] = tc.Value
		}
		cfg, err := config.Parse(values)
		if err != nil || cfg.FamilyRegistrationEnabled != tc.Expected || cfg.FederationRegistrationEnabled != tc.Expected || cfg.MultipleRegistrationEnabled != tc.Expected {
			t.Fatal("Django-characterized registration boolean differs", err)
		}
	}
	for _, tc := range fixture.Files {
		t.Run("dotenv "+tc.Name, func(t *testing.T) {
			for _, name := range []string{"FAMILY_REGISTRATION_ENABLED", "FEDERATION_REGISTRATION_ENABLED", "MULTIPLE_REGISTRATION_ENABLED", "MISSING", "LATER", "YES", "SWITCH"} {
				t.Setenv(name, "")
				if err := os.Unsetenv(name); err != nil {
					t.Fatal(err)
				}
			}
			for key, value := range tc.Process {
				t.Setenv(key, value)
			}
			path := filepath.Join(t.TempDir(), "synthetic.env")
			if err := os.WriteFile(path, []byte("AWS_ACCESS_KEY_ID=fixture-access\nAWS_SECRET_ACCESS_KEY=fixture-secret\nAWS_STORAGE_BUCKET_NAME=fixture-bucket\nAWS_S3_REGION_NAME=us-east-1\nAWS_S3_ENDPOINT_URL=http://127.0.0.1:9000\nAWS_LOCATION=private\nSECRET_KEY=synthetic-session-secret\nDEBUG=False\nDBTYPE=POSTGRES\nDBHOST=/tmp/fixture\nDBNAME=fixture\nDBUSER=fixture\nSOCIAL_AUTH_GOOGLE_OAUTH2_KEY=\nSOCIAL_AUTH_GOOGLE_OAUTH2_SECRET=\nJWT_SECRET_KEY=synthetic-private\nJWT_PUBLIC_KEY=synthetic-public\n"+tc.Lines), 0600); err != nil {
				t.Fatal(err)
			}
			cfg, err := config.Load(path)
			if err != nil || !reflect.DeepEqual([]bool{cfg.FamilyRegistrationEnabled, cfg.FederationRegistrationEnabled, cfg.MultipleRegistrationEnabled}, tc.Expected) {
				t.Fatal("Django-characterized dotenv differs", err)
			}
		})
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	counter := &responseQueries{}
	cfg.Tracer = counter
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	images, err := loginimage.NewProcessor(os.Getenv("ASSOZETA_LOGIN_JPEG_CODEC"), loginimage.ProcessorLimits{
		Codec: loginimage.JPEGLimits{MaxRasterBytes: 64 << 20, MaxEncodedBytes: 4 << 20, Concurrency: 2, Timeout: 5 * time.Second}, MaxURIBytes: 8 << 20, MaxHeaderBytes: 4 << 20, MaxTailBytes: 4 << 20, MaxDecodeBytes: 64 << 20, MaxTransformBytes: 64 << 20})
	if err != nil {
		t.Fatal(err)
	}
	builder := func(db *sqlx.DB, tc responseFixtureCase) *LoginResponses {
		clock := func() time.Time { return now }
		return NewLoginResponses(NewLoginProfiles(repository.NewLoginProfiles(db), clock), NewAssociationProfiles(repository.NewAssociationProfiles(db), clock), repository.NewLoginResponseSettings(db), NewMembershipCardConfigurations(repository.NewMembershipCardConfigurations(db), clock), images, responseChoices(t, tc.Flags))
	}
	account := func(tc responseFixtureCase) domain.Account {
		return domain.Account{ID: tc.ID, Role: tc.Role, Superuser: tc.Admin, Active: true, ConnectedUserID: tc.Owner}
	}
	compare := func(actual any, expected json.RawMessage) {
		t.Helper()
		value, err := json.Marshal(actual)
		if err != nil {
			t.Fatal(err)
		}
		var a, b any
		da, db := json.NewDecoder(bytes.NewReader(value)), json.NewDecoder(bytes.NewReader(expected))
		da.UseNumber()
		db.UseNumber()
		if da.Decode(&a) != nil || db.Decode(&b) != nil || !reflect.DeepEqual(a, b) {
			t.Fatalf("response differs: got %s want %s", value, expected)
		}
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			service := builder(db, tc)
			counter.count.Store(0)
			result, err := service.Build(context.Background(), account(tc), fixture.Tokens, domain.LoginAuditContext{})
			switch tc.Outcome {
			case "role":
				if result != nil || !errors.Is(err, ErrLoginResponseRole) {
					t.Fatal("invalid role returned response", err)
				}
			case "association":
				if result != nil || !errors.Is(err, domain.ErrAssociationNotFound) {
					t.Fatal("missing association returned response", err)
				}
			default:
				if err != nil || result == nil {
					t.Fatal("response failed", err)
				}
				compare(result, tc.Expected)
			}
			if counter.count.Load() != int64(tc.Queries+responseFenceQueries(tc)) {
				t.Fatalf("queries %d want %d", counter.count.Load(), tc.Queries+responseFenceQueries(tc))
			}
			if db.Stats().InUse != 0 {
				t.Fatal("response retained database connection")
			}
		})
	}
	t.Run("mixed image response concurrency", func(t *testing.T) {
		var wg sync.WaitGroup
		counter.count.Store(0)
		want := int64(0)
		for i := 0; i < 32; i++ {
			tc := fixture.Cases[36+i%12]
			queries := tc.Queries
			if tc.Created {
				queries = 7
			}
			want += int64(queries + responseFenceQueries(tc))
			service := builder(db, tc)
			wg.Add(1)
			go func() {
				defer wg.Done()
				result, err := service.Build(context.Background(), account(tc), fixture.Tokens, domain.LoginAuditContext{})
				if err != nil || result == nil {
					t.Error("image response failed", err)
					return
				}
				encoded, err := json.Marshal(result)
				if err != nil {
					t.Error(err)
					return
				}
				var got, want any
				a, b := json.NewDecoder(bytes.NewReader(encoded)), json.NewDecoder(bytes.NewReader(tc.Expected))
				a.UseNumber()
				b.UseNumber()
				if a.Decode(&got) != nil || b.Decode(&want) != nil || !reflect.DeepEqual(got, want) {
					t.Error("concurrent image response differs", tc.Name)
				}
			}()
		}
		wg.Wait()
		if counter.count.Load() != want || db.Stats().InUse != 0 {
			t.Fatal("image response query/pool bound", counter.count.Load(), want)
		}
	})
	t.Run("missing image decoder returns no token response", func(t *testing.T) {
		target, source := fixture.Cases[36], fixture.Cases[38]
		var original string
		if err := db.Get(&original, "SELECT avatar_image FROM bakney_user WHERE user_id=$1", target.ID); err != nil {
			t.Fatal(err)
		}
		if _, err := db.Exec("UPDATE bakney_user SET avatar_image=(SELECT avatar_image FROM bakney_user WHERE user_id=$1) WHERE user_id=$2", source.ID, target.ID); err != nil {
			t.Fatal(err)
		}
		defer func() {
			if _, err := db.Exec("UPDATE bakney_user SET avatar_image=$1 WHERE user_id=$2", original, target.ID); err != nil {
				t.Error(err)
			}
		}()
		counter.count.Store(0)
		result, err := builder(db, target).Build(context.Background(), account(target), fixture.Tokens, domain.LoginAuditContext{})
		if result != nil || !errors.Is(err, loginimage.ErrImageDecoderUnavailable) {
			t.Fatal("missing codec exposed response tokens", err)
		}
		if counter.count.Load() != 8 || db.Stats().InUse != 0 {
			t.Fatal("image failure reached card or retained connection", counter.count.Load())
		}
	})
	t.Run("bounded concurrent response composition", func(t *testing.T) {
		var tc responseFixtureCase
		for _, candidate := range fixture.Cases {
			if candidate.Instructors == 512 {
				tc = candidate
			}
		}
		if tc.ID == "" {
			t.Fatal("missing concurrency fixture")
		}
		service := builder(db, tc)
		counter.count.Store(0)
		var wg sync.WaitGroup
		for i := 0; i < 32; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
				defer cancel()
				result, err := service.Build(ctx, account(tc), fixture.Tokens, domain.LoginAuditContext{})
				if err != nil || result == nil {
					t.Error("concurrent response failed", err)
					return
				}
				if result.UserData.InstructorID == nil {
					t.Error("missing instructor")
				}
			}()
		}
		wg.Wait()
		if counter.count.Load() != 352 || db.Stats().InUse != 0 {
			t.Fatalf("query bound/pool release: %d", counter.count.Load())
		}
	})
	t.Run("instructor permission required only for collaborator", func(t *testing.T) {
		restricted := cfg.Copy()
		restricted.RuntimeParams["role"] = "login_instructor_denied"
		roleDB := sqlx.NewDb(stdlib.OpenDB(*restricted), "pgx")
		defer roleDB.Close()
		roleDB.SetMaxOpenConns(1)
		for _, tc := range []responseFixtureCase{fixture.Cases[6], fixture.Cases[14]} {
			result, err := builder(roleDB, tc).Build(context.Background(), account(tc), fixture.Tokens, domain.LoginAuditContext{})
			if tc.Role == 3 {
				var pg *pgconn.PgError
				if result != nil || !errors.As(err, &pg) || pg.Code != "42501" {
					t.Fatal("required instructor failure not propagated", err)
				}
			} else {
				if err != nil {
					t.Fatal("owner acquired instructor dependency", err)
				}
				compare(result, tc.Expected)
			}
			if roleDB.Stats().InUse != 0 {
				t.Fatal("permission failure retained pool connection")
			}
		}
	})
	t.Run("fresh instructor and settings transitions", func(t *testing.T) {
		tc := fixture.Cases[14]
		service := builder(db, tc)
		var originalID string
		if err := db.Get(&originalID, `SELECT instructor_id FROM application_instructor WHERE associated_user_id=$1 ORDER BY instructor_id LIMIT 1`, tc.ID); err != nil {
			t.Fatal(err)
		}
		var tables []byte
		var temporary bool
		if err := db.Get(&tables, `SELECT tables_settings FROM bakney_user WHERE user_id=$1`, tc.ID); err != nil {
			t.Fatal(err)
		}
		if err := db.Get(&temporary, `SELECT temporary_invoice_deletion FROM bakney_user WHERE user_id=$1`, tc.ID); err != nil {
			t.Fatal(err)
		}
		defer func() {
			if _, err := db.Exec(`UPDATE application_instructor SET associated_user_id=$2 WHERE instructor_id=$1`, originalID, tc.ID); err != nil {
				t.Error(err)
			}
			if _, err := db.Exec(`UPDATE bakney_user SET tables_settings=$2::jsonb,temporary_invoice_deletion=$3 WHERE user_id=$1`, tc.ID, tables, temporary); err != nil {
				t.Error(err)
			}
		}()
		if _, err := db.Exec(`UPDATE application_instructor SET associated_user_id=NULL WHERE instructor_id=$1`, originalID); err != nil {
			t.Fatal(err)
		}
		if _, err := db.Exec(`UPDATE bakney_user SET tables_settings='[9007199254740993]'::jsonb,temporary_invoice_deletion=true WHERE user_id=$1`, tc.ID); err != nil {
			t.Fatal(err)
		}
		result, err := service.Build(context.Background(), account(tc), fixture.Tokens, domain.LoginAuditContext{})
		if err != nil || result.UserData.InstructorID != nil || string(result.TablesSettings) != "[9007199254740993]" || !*result.UserData.TemporaryInvoiceDeletion {
			t.Fatal("response reused stale settings", err)
		}
	})
	t.Run("cancelled and missing selected account", func(t *testing.T) {
		tc := fixture.Cases[0]
		service := builder(db, tc)
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		result, err := service.Build(ctx, account(tc), fixture.Tokens, domain.LoginAuditContext{})
		if result != nil || !errors.Is(err, context.Canceled) {
			t.Fatal("cancelled response escaped", err)
		}
		missing := account(tc)
		missing.ID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
		result, err = service.Build(context.Background(), missing, fixture.Tokens, domain.LoginAuditContext{})
		if result != nil || !errors.Is(err, domain.ErrAccountNotFound) {
			t.Fatal("missing account returned profile", err)
		}
	})
}
