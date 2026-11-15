package dto

import (
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// A form body without a documents_data key carries the ListField HTML input
// (documents_data[N].name), one dict per index.
func TestParseSubscriptionMembershipWriteFormIndexedDocuments(t *testing.T) {
	body := domain.RequestObject{
		{Name: "membership_type", Value: "Ordinaria"},
		{Name: "documents_data[0].document", Value: "UmVnb2xhbWVudG8="},
		{Name: "documents_data[0].filename", Value: "regolamento.pdf"},
		{Name: "documents_data[1].document", Value: "QQ=="},
		{Name: "documents_data[1].filename", Value: "allegato.pdf"},
	}
	write, invalid := ParseSubscriptionMembershipWrite(body, true, false)
	if invalid != nil {
		t.Fatalf("invalid=%v", invalid)
	}
	if len(write.Documents) != 2 {
		t.Fatalf("documents=%v", write.Documents)
	}
	for _, want := range [][2]string{{"document", "UmVnb2xhbWVudG8="}, {"filename", "regolamento.pdf"}} {
		value, present := write.Documents[0].Lookup(want[0])
		if !present || value != want[1] {
			t.Errorf("documents[0][%s]=%v present=%v", want[0], value, present)
		}
	}
	if value, _ := write.Documents[1].Lookup("filename"); value != "allegato.pdf" {
		t.Errorf("documents[1][filename]=%v", value)
	}
	if value, present := write.Text["membership_type"]; !present || value == nil || *value != "Ordinaria" {
		t.Errorf("membership_type=%v present=%v", value, present)
	}
}

// documents_data=abc is ListField.get_value's every value of the key: a list of
// str, so each item fails DictField with the per-index dict error.
func TestParseSubscriptionMembershipWriteFormDocumentsValueIsNotADict(t *testing.T) {
	body := domain.RequestObject{{Name: "documents_data", Value: "abc"}}
	write, invalid := ParseSubscriptionMembershipWrite(body, true, false)
	want := map[string]any{"documents_data": map[string]any{"0": []string{courseLocationDict + "str"}}}
	if !reflect.DeepEqual(invalid, want) {
		t.Fatalf("invalid=%v want=%v", invalid, want)
	}
	if write.Documents != nil {
		t.Fatalf("documents=%v", write.Documents)
	}
}

// A partial update skips the HTML list, so documents_data[N] keys alone add no
// document; the same body on add is one dict (whose keys the service checks).
func TestParseSubscriptionMembershipWriteFormIndexedDocumentsPartial(t *testing.T) {
	body := domain.RequestObject{
		{Name: "documents_data[0].filename", Value: "regolamento.pdf"},
	}
	write, invalid := ParseSubscriptionMembershipWrite(body, true, true)
	if invalid != nil || write.Documents != nil {
		t.Fatalf("documents=%v invalid=%v", write.Documents, invalid)
	}
	add, invalid := ParseSubscriptionMembershipWrite(body, true, false)
	if invalid != nil || len(add.Documents) != 1 {
		t.Fatalf("documents=%v invalid=%v", add.Documents, invalid)
	}
	if value, present := add.Documents[0].Lookup("filename"); !present || value != "regolamento.pdf" {
		t.Errorf("documents[0][filename]=%v present=%v", value, present)
	}
}

// A multipart file part of an indexed item stays the dict value (parse_html_dict
// hands the file to DictField): _handle_documents then stores str(file).
func TestParseSubscriptionMembershipWriteFormIndexedDocumentsFilePart(t *testing.T) {
	body := domain.RequestObject{
		{Name: "documents_data[0].document", Value: "UmVnb2xhbWVudG8="},
		{Name: "documents_data[0].filename", Value: domain.RequestFile{Name: "regolamento.pdf"}},
	}
	write, invalid := ParseSubscriptionMembershipWrite(body, true, false)
	if invalid != nil || len(write.Documents) != 1 {
		t.Fatalf("documents=%v invalid=%v", write.Documents, invalid)
	}
	value, present := write.Documents[0].Lookup("filename")
	if file, isFile := value.(domain.RequestFile); !present || !isFile || file.Name != "regolamento.pdf" {
		t.Fatalf("documents[0][filename]=%v present=%v", value, present)
	}
}
