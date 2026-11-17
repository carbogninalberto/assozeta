package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/jackc/pgx/v5/pgconn"
)

// timeoutCheckStore is QueryBuilder's store with the instance's AI settings
// and a CheckReportTimeout that records the text it was sent and answers the
// configured error.
type timeoutCheckStore struct {
	integration string
	report      domain.SavedReport
	checks      []string
	checkErr    error
	ran         bool
}

func (s *timeoutCheckStore) SavedReports(context.Context, string, string) ([]domain.SavedReport, error) {
	return nil, nil
}

func (s *timeoutCheckStore) InsertSavedReport(context.Context, string, string, string, domain.SavedReportWrite) error {
	return nil
}

func (s *timeoutCheckStore) ActiveUserExists(context.Context, string) (bool, error) { return true, nil }

func (s *timeoutCheckStore) AssociationExists(context.Context, string) (bool, error) {
	return true, nil
}

func (s *timeoutCheckStore) ReportIntegration(context.Context) (bool, *string, error) {
	return s.integration != "", &s.integration, nil
}

func (s *timeoutCheckStore) CheckReportTimeout(_ context.Context, timeoutText string) error {
	s.checks = append(s.checks, timeoutText)
	return s.checkErr
}

func (s *timeoutCheckStore) RunReportStatements(_ context.Context, _ string, statements []domain.ReportStatement) ([][][]*string, error) {
	s.ran = true
	results := make([][][]*string, 0, len(statements))
	for range statements {
		text := "1"
		results = append(results, [][]*string{{&text}})
	}
	return results, nil
}

func (s *timeoutCheckStore) ActiveAssociationForUser(context.Context, string) (string, error) {
	return "a1000000-0000-4000-8000-000000000001", nil
}

func (s *timeoutCheckStore) SavedReport(context.Context, string, string, string) (domain.SavedReport, error) {
	return s.report, nil
}

func (s *timeoutCheckStore) UpdateSavedReport(context.Context, string, domain.SavedReportChange) error {
	return nil
}

func (s *timeoutCheckStore) DeleteSavedReport(context.Context, string) error { return nil }

// invalidTimeoutIntegration stores a query_timeout AISettingsSerializer
// accepts (3000000 seconds) whose product exceeds PostgreSQL's int range.
const invalidTimeoutIntegration = `{"ai": {"revision": 1, "settings": {"max_results": 100, "query_timeout": 3000000}}}`

func invalidStatementTimeout() *pgconn.PgError {
	return &pgconn.PgError{Severity: "ERROR", Code: "22023",
		Message: `invalid value for parameter "statement_timeout": "3000000000"`}
}

// TestBusinessMCPChecksReportTimeout is execute_aggregation's SET point: an
// unusable timeout raises the PostgreSQL error before 'No valid group_by
// fields', so the MCP result is {'error': <PostgreSQL text>}.
func TestBusinessMCPChecksReportTimeout(t *testing.T) {
	server := invalidStatementTimeout()
	store := &timeoutCheckStore{integration: invalidTimeoutIntegration,
		checkErr: &domain.ReportQueryError{Err: server}}
	tools := NewBusinessMCPTools(store, "a1000000-0000-4000-8000-000000000001", time.Now)
	raised := false
	_, err := tools.Execute(WithMCPRaised(context.Background(), &raised), "aggregate_data",
		mcpObj("model_name", "Tags", "aggregations", []any{mcpObj("function", "count", "field", "*")},
			"group_by", []any{"nope"}))
	if !errors.Is(err, ErrBusinessToolRaised) {
		t.Fatalf("err %v, want ErrBusinessToolRaised", err)
	}
	if got, want := BusinessToolRaisedText(err), postgresErrorText(server); got != want {
		t.Fatalf("text %q, want %q", got, want)
	}
	if len(store.checks) != 1 || store.checks[0] != "3000000000" {
		t.Fatalf("checked %v, want the exact product text", store.checks)
	}
	if store.ran {
		t.Fatal("the statements ran after the rejected timeout")
	}
}

// TestBusinessMCPUsesAcceptableReportTimeout checks the ordinary path: the
// check runs once with the text and the statements then run.
func TestBusinessMCPUsesAcceptableReportTimeout(t *testing.T) {
	store := &timeoutCheckStore{integration: `{"ai": {"settings": {"max_results": 100, "query_timeout": 5}}}`}
	tools := NewBusinessMCPTools(store, "a1000000-0000-4000-8000-000000000001", time.Now)
	raised := false
	if _, err := tools.Execute(WithMCPRaised(context.Background(), &raised), "count_data",
		mcpObj("model_name", "Tags")); err != nil {
		t.Fatal(err)
	}
	if len(store.checks) != 1 || store.checks[0] != "5000" {
		t.Fatalf("checked %v, want [5000]", store.checks)
	}
	if !store.ran {
		t.Fatal("the statements did not run after an accepted timeout")
	}
}

// TestSavedReportChecksReportTimeout is the saved-report run at the same
// point: the rejected SET is the tool's exception (JSON 500), not the
// group_by answer.
func TestSavedReportChecksReportTimeout(t *testing.T) {
	store := &timeoutCheckStore{integration: invalidTimeoutIntegration,
		checkErr: &domain.ReportQueryError{Err: invalidStatementTimeout()},
		report: domain.SavedReport{ID: "a1000000-0000-4000-8000-000000000010", Name: "Report",
			ToolName: "aggregate_data", UIConfig: "{}",
			Params: `{"model_name":"Tags","aggregations":[{"function":"count","field":"*"}],"group_by":["nope"]}`}}
	reports := NewSavedReports(store, time.Now)
	identity := domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: "a1000000-0000-4000-8000-000000000002"}}
	_, err := reports.RunGroup(context.Background(), identity, "a1000000-0000-4000-8000-000000000010", nil, "")
	if !errors.Is(err, dto.ErrReportFailed) {
		t.Fatalf("err %v, want dto.ErrReportFailed", err)
	}
	if len(store.checks) != 1 || store.checks[0] != "3000000000" {
		t.Fatalf("checked %v, want the exact product text", store.checks)
	}
	if store.ran {
		t.Fatal("the statements ran after the rejected timeout")
	}
}
