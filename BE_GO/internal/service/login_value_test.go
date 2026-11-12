package service

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
)

func TestLoginValueText(t *testing.T) {
	for _, tc := range []struct {
		name  string
		value any
		want  string
	}{
		{"text", "  é\n", "  é\n"}, {"null", nil, "None"}, {"true", true, "True"}, {"false", false, "False"},
		{"integer", json.Number("123456789012345678901234567890"), "123456789012345678901234567890"},
		{"negative zero integer", json.Number("-0"), "0"}, {"float", json.Number("1.0"), "1.0"},
		{"negative zero float", json.Number("-0.0"), "-0.0"}, {"decimal exponent", json.Number("1e15"), "1000000000000000.0"},
		{"large exponent", json.Number("1e16"), "1e+16"}, {"small fixed", json.Number("1e-4"), "0.0001"},
		{"small exponent", json.Number("1e-5"), "1e-05"}, {"overflow", json.Number("1e400"), "inf"},
		{"list", []any{nil, true, json.Number("3.0"), "x"}, "[None, True, 3.0, 'x']"},
		{"ordered object", domain.RequestObject{{Name: "z", Value: false}, {Name: "a", Value: []any{"x"}}}, "{'z': False, 'a': ['x']}"},
		{"quotes", []any{"a'b", "a\"b", "a'\"b"}, `["a'b", 'a"b', 'a\'"b']`},
		{"escapes", []any{"\x00\a\b\t\n\v\f\r\x1f\u007f\u00a0\u2028😀"}, "['\\x00\\x07\\x08\\t\\n\\x0b\\x0c\\r\\x1f\\x7f\\xa0\\u2028😀']"},
		{"upload", domain.RequestUpload{Name: "login.txt", Size: 2}, "login.txt"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := loginValueText(context.Background(), tc.value)
			if err != nil || got != tc.want {
				t.Fatalf("representation = %q, %v; want %q", got, err, tc.want)
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := loginValueText(ctx, "x"); !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled conversion succeeded")
	}
	if _, err := loginValueText(context.Background(), make(chan int)); err == nil {
		t.Fatal("unknown internal value accepted")
	}
	for _, raw := range []string{"bad", "true", "null", "\"text\"", "01", "", "+1", "--1"} {
		if _, err := loginValueText(context.Background(), json.Number(raw)); err == nil {
			t.Fatal("invalid internal number accepted")
		}
	}
	nested := any(nil)
	for i := 0; i < 1100; i++ {
		nested = []any{nested}
	}
	if _, err := loginValueText(context.Background(), nested); err == nil {
		t.Fatal("unbounded recursive input accepted")
	}
}

func TestRawPasswordNoCandidatesPrecedesValueError(t *testing.T) {
	tx := &loginTransactionStub{}
	s := NewPasswordLogin(tx, passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), 1)
	got, err := s.Select(context.Background(), nil, nil)
	if err != nil || got.Failure != "not_found" || !tx.committed {
		t.Fatal("missing candidate must precede password evaluation")
	}
}
func TestRawPasswordValueFailureRollsBack(t *testing.T) {
	tx := &loginTransactionStub{rows: []domain.Account{{ID: "1", Username: "name", Password: "!unused", Active: true}}}
	s := NewPasswordLogin(tx, passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), 1)
	got, err := s.Select(context.Background(), "name", nil)
	if !errors.Is(err, passwordcrypto.ErrLoginPasswordValue) || got.Account != nil || tx.committed || !tx.rolled || len(tx.writes) != 0 {
		t.Fatal("raw password error leaked selection or transaction")
	}
}

// Deterministic cancellation checks conversion in progress without a timing race.
type loginConversionContext struct {
	context.Context
	remaining int
}

func (c *loginConversionContext) Err() error {
	c.remaining--
	if c.remaining <= 0 {
		return context.Canceled
	}
	return nil
}
func TestLoginValueConversionCancellationAndConcurrency(t *testing.T) {
	value := []any{strings.Repeat("\u00a0", 65536)}
	ctx := &loginConversionContext{Context: context.Background(), remaining: 8}
	if got, err := loginValueText(ctx, value); got != "" || !errors.Is(err, context.Canceled) {
		t.Fatal("partial conversion escaped cancellation")
	}
	var group sync.WaitGroup
	for i := 0; i < 32; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			got, err := loginValueText(context.Background(), domain.RequestObject{{Name: "a", Value: []any{true, json.Number("1.0")}}})
			if err != nil || got != "{'a': [True, 1.0]}" {
				t.Error("concurrent conversion changed output")
			}
		}()
	}
	group.Wait()
}

func TestRawPasswordCannotBeUpgradedByDependency(t *testing.T) {
	for _, encoded := range []string{"old", "legacy"} {
		tx := &loginTransactionStub{rows: []domain.Account{{ID: "1", Username: "name", Password: encoded, Active: true}}}
		got, err := NewPasswordLogin(tx, loginHasherStub{}, 1).Select(context.Background(), "name", json.Number("123"))
		if !errors.Is(err, passwordcrypto.ErrLoginPasswordValue) || got.Account != nil || len(tx.writes) != 0 || tx.committed || !tx.rolled {
			t.Fatal("non-string credential was made usable by replacement")
		}
	}
}
