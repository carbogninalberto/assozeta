package service

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
)

// courseLocationDocumentWriter is the writer the document tests walk with: the
// document storage with no stores behind it (courseLocationDocuments only
// validates the storage name).
func courseLocationDocumentWriter() *CourseLocationWrites {
	now := func() time.Time { return time.Date(2026, 11, 10, 19, 45, 3, 123456000, time.UTC) }
	uploads := NewDocumentUploads(nil, nil, nil, false, now, func() string { return "" })
	return NewCourseLocationWrites(nil, now, func() string { return "" }).WithUploads(uploads)
}

// courseLocationDocumentItem is one documents_data item with a filename value.
func courseLocationDocumentItem(filename any) domain.RequestObject {
	return domain.RequestObject{{Name: "document", Value: "UmVnb2xhbWVudG8="}, {Name: "filename", Value: filename}}
}

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

// A filename Django cannot store stops the walk with the 500: a value that is
// not a str creates the row with str() of it, then os.path.join raises.
func TestCourseLocationDocumentsNonTextFilenameKeepsRow(t *testing.T) {
	writes := courseLocationDocumentWriter()
	stored, failure, refused := writes.courseLocationDocuments(dto.CourseLocationWrite{
		Documents: []domain.RequestObject{courseLocationDocumentItem(json.Number("123"))},
	})
	if refused != nil || !errors.Is(failure, errCourseLocationDocumentStore) {
		t.Fatalf("failure=%v refused=%v", failure, refused)
	}
	if len(stored) != 1 || stored[0].filename != "123" || !stored[0].rowOnly || stored[0].content != nil {
		t.Fatalf("stored=%+v", stored)
	}
}

// A None filename fails the INSERT: no Document row, the 500.
func TestCourseLocationDocumentsNoneFilenameKeepsNoRow(t *testing.T) {
	writes := courseLocationDocumentWriter()
	stored, failure, refused := writes.courseLocationDocuments(dto.CourseLocationWrite{
		Documents: []domain.RequestObject{courseLocationDocumentItem(nil)},
	})
	if refused != nil || !errors.Is(failure, errCourseLocationDocumentStore) || len(stored) != 0 {
		t.Fatalf("stored=%+v failure=%v refused=%v", stored, failure, refused)
	}
}

// A 256-character name overflows varchar(255): no Document row, the 500.
func TestCourseLocationDocumentsLongFilenameKeepsNoRow(t *testing.T) {
	writes := courseLocationDocumentWriter()
	long := strings.Repeat("a", 256)
	stored, failure, refused := writes.courseLocationDocuments(dto.CourseLocationWrite{
		Documents: []domain.RequestObject{courseLocationDocumentItem(long)},
	})
	if refused != nil || !errors.Is(failure, errCourseLocationDocumentStore) || len(stored) != 0 {
		t.Fatalf("stored=%+v failure=%v refused=%v", stored, failure, refused)
	}
}

// A name Storage.save rejects is written by the INSERT, then raises a
// SuspiciousFileOperation (the 400 page): the row stays, nothing is stored or
// linked.
func TestCourseLocationDocumentsTraversalFilenameKeepsRow(t *testing.T) {
	writes := courseLocationDocumentWriter()
	stored, failure, refused := writes.courseLocationDocuments(dto.CourseLocationWrite{
		Documents: []domain.RequestObject{courseLocationDocumentItem("../x.pdf")},
	})
	var suspicious *documentkey.SuspiciousFileError
	if refused != nil || !errors.As(failure, &suspicious) {
		t.Fatalf("failure=%v refused=%v", failure, refused)
	}
	if len(stored) != 1 || stored[0].filename != "../x.pdf" || !stored[0].rowOnly {
		t.Fatalf("stored=%+v", stored)
	}
}

// The items before the failing one keep their uploads: the earlier Document
// rows are stored and linked, then the 500.
func TestCourseLocationDocumentsKeepsEarlierUploads(t *testing.T) {
	writes := courseLocationDocumentWriter()
	stored, failure, refused := writes.courseLocationDocuments(dto.CourseLocationWrite{
		Documents: []domain.RequestObject{
			courseLocationDocumentItem("regolamento.pdf"),
			courseLocationDocumentItem(json.Number("123")),
		},
	})
	if refused != nil || !errors.Is(failure, errCourseLocationDocumentStore) {
		t.Fatalf("failure=%v refused=%v", failure, refused)
	}
	if len(stored) != 2 || stored[0].rowOnly || stored[0].filename != "regolamento.pdf" ||
		stored[0].content == nil || !stored[1].rowOnly || stored[1].filename != "123" {
		t.Fatalf("stored=%+v", stored)
	}
}
