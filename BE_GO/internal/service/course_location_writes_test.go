package service

import (
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// A clock with nanosecond precision (Linux) must not refuse an upload: the
// stored name uses the creation time truncated to microseconds.
func TestCourseLocationDocumentsAcceptNanosecondClock(t *testing.T) {
	now := func() time.Time { return time.Date(2026, 11, 10, 19, 45, 3, 123456789, time.UTC) }
	uploads := NewDocumentUploads(nil, nil, nil, false, now, func() string { return "" })
	writes := NewCourseLocationWrites(nil, now, func() string { return "" }).WithUploads(uploads)
	item := domain.RequestObject{{Name: "document", Value: "UmVnb2xhbWVudG8="}, {Name: "filename", Value: "regolamento.pdf"}}
	stored, failure, refused := writes.courseLocationDocuments(dto.CourseLocationWrite{Documents: []domain.RequestObject{item}})
	if failure != nil || refused != nil || len(stored) != 1 || stored[0].filename != "regolamento.pdf" {
		t.Fatalf("stored=%v failure=%v refused=%v", stored, failure, refused)
	}
}
