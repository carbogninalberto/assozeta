package service

import (
	"context"
	"errors"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jackc/pgx/v5/pgconn"
)

// ErrBusinessToolRaised is an exception raised inside a business tool whose
// str(e) Go does not reproduce exactly (a PostgreSQL error text carries the
// statement; some exporter texts carry object addresses). The
// BusinessToolRaised value carries the closest text, which both hosts answer.
var ErrBusinessToolRaised = errors.New("business tool raised an exception whose text is not reproduced")

// businessToolGenericText is the closest text of an exception whose Django
// text the call site cannot know.
const businessToolGenericText = "Errore durante l'esecuzione dello strumento."

// BusinessToolRaised is an exception of a business tool with the closest text
// of Django's str(e). Is keeps errors.Is(err, ErrBusinessToolRaised) true.
type BusinessToolRaised struct {
	Text string
	Err  error
}

func (e *BusinessToolRaised) Error() string {
	if e.Text != "" {
		return e.Text
	}
	return ErrBusinessToolRaised.Error()
}

func (e *BusinessToolRaised) Unwrap() error { return e.Err }

func (e *BusinessToolRaised) Is(target error) bool { return target == ErrBusinessToolRaised }

// BusinessToolRaisedText is the closest Django text of a raised business tool
// exception (the generic text when the error carries none).
func BusinessToolRaisedText(err error) string {
	var raised *BusinessToolRaised
	if errors.As(err, &raised) && raised.Text != "" {
		return raised.Text
	}
	return businessToolGenericText
}

// newBusinessToolRaised carries text as the closest str(e); "" is the generic
// text.
func newBusinessToolRaised(text string, err error) error {
	if text == "" {
		text = businessToolGenericText
	}
	return &BusinessToolRaised{Text: text, Err: err}
}

// closestBusinessToolText is the closest text of a report exception: psycopg2
// writes a PostgreSQL error's server message, DETAIL, HINT and CONTEXT lines
// (and a LINE for the statement, which Go does not reproduce because its
// statement has placeholders instead of literals).
func closestBusinessToolText(err error) string {
	var query *domain.ReportQueryError
	if errors.As(err, &query) {
		var pg *pgconn.PgError
		if errors.As(query, &pg) {
			return postgresErrorText(pg)
		}
	}
	return businessToolGenericText
}

// postgresErrorText is psycopg2's str(e) for a server error, in libpq's order:
// message, LINE (skipped), DETAIL, HINT, CONTEXT. libpq writes two spaces
// after the label; the text ends with a newline. pgconn stores CONTEXT in
// Where.
func postgresErrorText(pg *pgconn.PgError) string {
	var text strings.Builder
	text.WriteString(pg.Message)
	text.WriteString("\n")
	if pg.Detail != "" {
		text.WriteString("DETAIL:  " + pg.Detail + "\n")
	}
	if pg.Hint != "" {
		text.WriteString("HINT:  " + pg.Hint + "\n")
	}
	if where := postgresClientContext(pg.Where); where != "" {
		text.WriteString("CONTEXT:  " + where + "\n")
	}
	return text.String()
}

// The agent (application.agent.core._execute_tool) renders a tool's result
// and an exception the tool raised differently: json.dumps(result,
// ensure_ascii=False) versus json.dumps({'error': str(e)}) with the field
// hint. The MCP transport renders both alike, so the tools only record that
// the {"error": ...} value they return stands for a raised exception.

type mcpRaisedKey struct{}

// WithMCPRaised returns a context in which the business tools set *raised
// when their {"error": ...} result stands for a raised exception.
func WithMCPRaised(ctx context.Context, raised *bool) context.Context {
	return context.WithValue(ctx, mcpRaisedKey{}, raised)
}

func markMCPRaised(ctx context.Context) {
	if raised, ok := ctx.Value(mcpRaisedKey{}).(*bool); ok && raised != nil {
		*raised = true
	}
}

// postgresClientContext drops the context lines PostgreSQL adds for a bound
// parameter ("unnamed portal parameter $N = '...'"): Go binds its values,
// psycopg2 sends them as literals, so Django's text never has them.
func postgresClientContext(where string) string {
	lines := strings.Split(where, "\n")
	kept := lines[:0]
	for _, line := range lines {
		if strings.HasPrefix(line, "unnamed portal parameter $") {
			continue
		}
		kept = append(kept, line)
	}
	return strings.Join(kept, "\n")
}
