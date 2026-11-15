package pdfexport

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// An auto-width table cell that is not a list of paragraphs is a layout no
// builder makes: an application-state error, never a handoff.
func TestUnexpectedLayoutIsAppState(t *testing.T) {
	_, err := newTable([][]tableCell{{{}}}, nil, 0).preliminaryWidths(nil, 100)
	if !errors.Is(err, ErrUnexpectedLayout) || !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("got %v", err)
	}
}
