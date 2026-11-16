package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// A reportlab table without a column raises its identity text; Go answers a
// fixed placeholder address and the real row count (the header row included).
func TestExportPDFWithoutColumnsAnswersTheTableText(t *testing.T) {
	data := []any{
		domain.RequestObject{{Name: "sport_association_id", Value: "a"}},
		domain.RequestObject{{Name: "sport_association_id", Value: "b"}},
	}
	_, err := (&BusinessMCPTools{}).exportPDF(context.Background(), data, domain.RequestObject{}, "Titolo",
		domain.RequestObject{})
	if !errors.Is(err, ErrBusinessToolRaised) {
		t.Fatalf("expected ErrBusinessToolRaised, got %v", err)
	}
	const want = "<Table@0x0000000000000000 3 rows x 0 cols>... must have at least a row and column"
	if got := BusinessToolRaisedText(err); got != want {
		t.Fatalf("text %q, want %q", got, want)
	}
}

// A single course without a title fails slicing the sheet name; a multi-course
// PDF fails Paragraph(None)'s text.split().
func TestAttendanceWithoutCourseTitleAnswersTheDjangoText(t *testing.T) {
	tools := &BusinessMCPTools{}
	matrices := []*attendanceMatrix{{course: nil}}
	_, err := tools.attendanceFile(context.Background(), matrices, false,
		domain.RequestObject{{Name: "format", Value: "xlsx"}})
	if got := BusinessToolRaisedText(err); got != "'NoneType' object is not subscriptable" {
		t.Fatalf("xlsx text %q (%v)", got, err)
	}
	matrices = append(matrices, &attendanceMatrix{course: nil})
	_, err = tools.attendanceFile(context.Background(), matrices, false,
		domain.RequestObject{{Name: "format", Value: "pdf"}})
	if got := BusinessToolRaisedText(err); got != "'NoneType' object has no attribute 'split'" {
		t.Fatalf("pdf text %q (%v)", got, err)
	}
}

// An exception whose text the call site does not know keeps the generic text.
func TestRaisedWithoutTextAnswersTheGenericText(t *testing.T) {
	for _, err := range []error{newBusinessToolRaised("", nil), mcpRunOnly(context.Background(), "")} {
		if !errors.Is(err, ErrBusinessToolRaised) {
			t.Fatalf("expected ErrBusinessToolRaised, got %v", err)
		}
		if got := BusinessToolRaisedText(err); got != businessToolGenericText {
			t.Fatalf("text %q, want %q", got, businessToolGenericText)
		}
	}
}
