package service

import (
	"database/sql"
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

func auditDeepColumn(depth int) sql.NullString {
	return sql.NullString{String: strings.Repeat("[", depth) + strings.Repeat("]", depth), Valid: true}
}

// TestAuditLoadDepthBand pins the X15 column-load bands: a detail row reads a
// column nested up to 9935, a list row (minimal or full) up to 9934, and one
// level more is the plain 500 page.
func TestAuditLoadDepthBand(t *testing.T) {
	row := func(depth int) domain.AuditLogRow {
		return domain.AuditLogRow{Changes: auditDeepColumn(depth)}
	}
	if changes, _, err := auditLoad(row(dto.AuditDetailJSONDepth), dto.AuditDetailBand()); err != nil || changes == nil {
		t.Fatalf("detail %d: %v", dto.AuditDetailJSONDepth, err)
	}
	_, _, err := auditLoad(row(dto.AuditDetailJSONDepth+1), dto.AuditDetailBand())
	if !errors.Is(err, dto.ErrAuditUnhandled) {
		t.Fatalf("detail %d: %v", dto.AuditDetailJSONDepth+1, err)
	}
	if _, _, err := auditLoad(row(dto.AuditListJSONDepth), dto.AuditListBand()); err != nil {
		t.Fatalf("list %d: %v", dto.AuditListJSONDepth, err)
	}
	_, _, err = auditLoad(row(dto.AuditListJSONDepth+1), dto.AuditListBand())
	if !errors.Is(err, dto.ErrAuditUnhandled) {
		t.Fatalf("list %d: %v", dto.AuditListJSONDepth+1, err)
	}
	// serialized_data is loaded too, and it is not part of the response.
	deep := domain.AuditLogRow{SerializedData: auditDeepColumn(dto.AuditListJSONDepth + 1)}
	if _, _, err := auditLoad(deep, dto.AuditListBand()); !errors.Is(err, dto.ErrAuditUnhandled) {
		t.Fatalf("serialized_data: %v", err)
	}
}
