package main

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestSchedulerBoundsRejectBeforeEnvironmentOrDatabase(t *testing.T) {
	for _, arguments := range [][]string{
		{"--page-size", "0"}, {"--page-size", "65"},
		{"--poll-interval", "0s"}, {"--poll-interval", "6s"},
		{"--tick-timeout", "0s"}, {"--tick-timeout", "6s"},
		{"--source-timeout", "0s"}, {"--source-timeout", "3s", "--tick-timeout", "2s"},
		{"--startup-timeout", "0s"}, {"--startup-timeout", "31s"},
	} {
		t.Run(strings.Join(arguments, " "), func(t *testing.T) {
			// An unreadable explicit environment must not obscure invalid bounds,
			// and no attempted startup connection may precede their validation.
			arguments = append(arguments, "--env-file", "/missing/private-fixture.env")
			err := run(arguments)
			if err == nil || err.Error() != "invalid scheduler execution bounds" {
				t.Fatalf("expected fixed bounds rejection, got %v", err)
			}
		})
	}
}

func TestSchedulerArgumentErrorsDoNotExposeInput(t *testing.T) {
	const canary = "private-password-canary"
	for _, args := range [][]string{{"--unknown-" + canary}, {"--poll-interval", canary}, {canary}} {
		err := run(args)
		if err == nil || strings.Contains(err.Error(), canary) {
			t.Fatalf("expected sanitized argument rejection, got %v", err)
		}
	}
}

type diagnosticSequence struct {
	results []domain.SchedulePageResult
	errors  []error
}

func (s *diagnosticSequence) Tick(context.Context, domain.ScheduleCursor) (domain.SchedulePageResult, error) {
	result, err := s.results[0], s.errors[0]
	s.results, s.errors = s.results[1:], s.errors[1:]
	return result, err
}

func TestSchedulerDiagnosticsPreserveFailuresWithoutLoggingSecretsOrAuthority(t *testing.T) {
	var output bytes.Buffer
	failure := errors.New("private-sql-canary SELECT payload FROM secret")
	cursor := domain.ScheduleCursor{AfterID: 2, ThroughID: 4}
	next := &diagnosticSequence{
		results: []domain.SchedulePageResult{{NextCursor: cursor}, {NextCursor: cursor}, {NextCursor: cursor}, {NextCursor: cursor}, {}},
		errors:  []error{failure, failure, domain.ErrOccurrenceInactive, domain.ErrRestoreBusy, nil},
	}
	ticker := &diagnosticTicker{next: next, logger: slog.New(slog.NewJSONHandler(&output, nil))}
	for index, want := range []error{failure, failure, domain.ErrOccurrenceInactive, domain.ErrRestoreBusy, nil} {
		result, got := ticker.Tick(context.Background(), cursor)
		if !errors.Is(got, want) || (index < 4 && result.NextCursor != cursor) {
			t.Fatalf("diagnostic wrapper changed result/error: %+v %v", result, got)
		}
	}
	text := output.String()
	for _, forbidden := range []string{"private-sql-canary", "SELECT payload", "scheduler ready", "owner active"} {
		if strings.Contains(text, forbidden) {
			t.Fatalf("diagnostic exposed a secret or implied authority: %s", text)
		}
	}
	for _, expected := range []string{"scheduler degraded", "scheduler inactive", "scheduler deferred", "scheduler page completed"} {
		if strings.Count(text, expected) != 1 {
			t.Fatalf("expected one bounded state-transition diagnostic %q: %s", expected, text)
		}
	}
}
