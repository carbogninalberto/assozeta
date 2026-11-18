package service

import (
	"context"
	"encoding/json"
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
	// runErr is RunReportStatements' answer (its statements' database error).
	runErr error
	// runs is the timeout text of each RunReportStatements call.
	runs []string
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

func (s *timeoutCheckStore) RunReportStatements(_ context.Context, timeoutText string, statements []domain.ReportStatement) ([][][]*string, error) {
	s.ran = true
	s.runs = append(s.runs, timeoutText)
	if s.runErr != nil {
		return nil, s.runErr
	}
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

// jsonbOperatorError is PostgreSQL's answer to filters {"meta__has_key": 12}:
// the count statement's ? operator does not accept an integer.
func jsonbOperatorError() *pgconn.PgError {
	return &pgconn.PgError{Severity: "ERROR", Code: "42883",
		Message: "operator does not exist: jsonb ? integer"}
}

// countErrorArguments is query_data on Payment with a JSON-key filter the
// count raises on and a negative limit (execute_query's deferred exception).
func countErrorArguments() domain.RequestObject {
	return mcpObj("model_name", "Payment", "filters", mcpObj("meta__has_key", json.Number("12")), "limit", json.Number("-1"))
}

// TestBusinessMCPCountErrorBeforeDeferred is execute_query's order: the count
// runs first, with statement_timeout reset to '0' (Django's SET comes after
// it), and its database error is the answer rather than the deferred
// 'Negative indexing is not supported.'
func TestBusinessMCPCountErrorBeforeDeferred(t *testing.T) {
	server := jsonbOperatorError()
	store := &timeoutCheckStore{runErr: &domain.ReportQueryError{Err: server}}
	tools := NewBusinessMCPTools(store, "a1000000-0000-4000-8000-000000000001", time.Now)
	raised := false
	_, err := tools.Execute(WithMCPRaised(context.Background(), &raised), "query_data", countErrorArguments())
	if !errors.Is(err, ErrBusinessToolRaised) {
		t.Fatalf("err %v, want ErrBusinessToolRaised", err)
	}
	if got, want := BusinessToolRaisedText(err), postgresErrorText(server); got != want {
		t.Fatalf("text %q, want %q", got, want)
	}
	if len(store.runs) != 1 || store.runs[0] != "0" {
		t.Fatalf("ran with %v, want one untimed run", store.runs)
	}
}

// TestBusinessMCPDeferredExceptionAfterCount is the same order without a
// database error: the count succeeds and the deferred exception is the tool's
// {'error': str(e)} result.
func TestBusinessMCPDeferredExceptionAfterCount(t *testing.T) {
	store := &timeoutCheckStore{}
	tools := NewBusinessMCPTools(store, "a1000000-0000-4000-8000-000000000001", time.Now)
	raised := false
	result, err := tools.Execute(WithMCPRaised(context.Background(), &raised), "query_data", countErrorArguments())
	if err != nil {
		t.Fatalf("err %v, want the tool's error result", err)
	}
	object, ok := result.(domain.RequestObject)
	if !ok {
		t.Fatalf("result %#v, want the error object", result)
	}
	message, present := object.Lookup("error")
	if !present || message != "Negative indexing is not supported." {
		t.Fatalf("result %#v, want {'error': 'Negative indexing is not supported.'}", result)
	}
	if len(store.runs) != 1 || store.runs[0] != "0" {
		t.Fatalf("ran with %v, want one untimed run", store.runs)
	}
	if !raised {
		t.Fatal("the exception was not marked as raised")
	}
}

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

// TestSavedReportRunsDeferredCount is RunGroup's side of the same order: the
// deferred exception's statements run first (the count, untimed) and the
// exception is then the tool's exception.
func TestSavedReportRunsDeferredCount(t *testing.T) {
	store := &timeoutCheckStore{report: domain.SavedReport{ID: "a1000000-0000-4000-8000-000000000011", Name: "Report",
		ToolName: "query_data", UIConfig: "{}", Params: `{"model_name":"Tags","limit":-1}`}}
	reports := NewSavedReports(store, time.Now)
	identity := domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: "a1000000-0000-4000-8000-000000000002"}}
	_, err := reports.RunGroup(context.Background(), identity, "a1000000-0000-4000-8000-000000000011", nil, "")
	if !errors.Is(err, dto.ErrReportFailed) || err.Error() != "Negative indexing is not supported." {
		t.Fatalf("err %v, want the deferred exception", err)
	}
	if len(store.runs) != 1 || store.runs[0] != "0" {
		t.Fatalf("ran with %v, want one untimed run", store.runs)
	}
}
