package dto

import (
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// CourseLocationTextFields are CourseLocation's text fields in declaration order.
var CourseLocationTextFields = []SupplierField{
	{Name: "title", Max: 255, Null: true},
	{Name: "description", Max: 2048, Null: true, Blank: true},
	{Name: "address", Max: 255, Null: true, Blank: true},
}

// CourseLocationWrite is CourseLocationSerializer's validated data before the
// related-field lookups: Text holds present text fields (nil for None).
type CourseLocationWrite struct {
	ID                 *string
	Text               map[string]*string
	AssociationPresent bool
	AssociationID      *string
	// Documents is the validated documents_data list (dicts).
	Documents []domain.RequestObject
	// IndexedDocuments marks form keys documents_data[n]...: parse_html_list
	// input, not ported.
	IndexedDocuments bool
}

const (
	CourseLocationUnique = "unique:course location:course location id"
	courseLocationList   = "not-a-list:"
	courseLocationDict   = "not-a-dict:"
)

// ParseCourseLocationWrite ports CourseLocationSerializer(data=...,
// partial=...) field validation; uniqueness and the sport_association
// queryset and tenant scope are checked by the service.
func ParseCourseLocationWrite(body any, form bool) (CourseLocationWrite, map[string]any) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return CourseLocationWrite{}, map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(body)}}
	}
	write := CourseLocationWrite{Text: map[string]*string{}}
	errors := map[string]any{}
	if value, present := object.Lookup("course_location_id"); present {
		if value == nil {
			errors["course_location_id"] = []string{PaymentCategoryValidationNull}
		} else if id, ok := supplierUUID(value); ok {
			write.ID = &id
		} else {
			errors["course_location_id"] = []string{PaymentCategoryValidationUUID}
		}
	}
	if form {
		// ListField.get_value reads only documents_data[n] keys of HTML input.
		for _, field := range object {
			write.IndexedDocuments = write.IndexedDocuments || strings.HasPrefix(field.Name, "documents_data[")
		}
	} else if value, present := object.Lookup("documents_data"); present {
		switch items := value.(type) {
		case nil:
			errors["documents_data"] = []string{PaymentCategoryValidationNull}
		case []any:
			children := map[string]any{}
			for index, item := range items {
				switch child := item.(type) {
				case domain.RequestObject:
					write.Documents = append(write.Documents, child)
				case nil:
					children[strconv.Itoa(index)] = []string{PaymentCategoryValidationNull}
				default:
					children[strconv.Itoa(index)] = []string{courseLocationDict + pythonTypeName(item)}
				}
			}
			if len(children) > 0 {
				errors["documents_data"] = children
			}
		default:
			errors["documents_data"] = []string{courseLocationList + pythonTypeName(value)}
		}
	}
	for _, field := range CourseLocationTextFields {
		value, present := object.Lookup(field.Name)
		if !present {
			continue
		}
		text, codes := modelTextValue(field, value, form)
		if len(codes) > 0 {
			errors[field.Name] = codes
			continue
		}
		write.Text[field.Name] = text
	}
	if value, present := object.Lookup("sport_association"); present {
		write.AssociationPresent = true
		if value != nil {
			if id, code := supplierRelated(value); code != "" {
				errors["sport_association"] = []string{code}
			} else {
				write.AssociationID = &id
			}
		}
	}
	if len(errors) > 0 {
		return write, errors
	}
	return write, nil
}

// CourseLocationDocumentStep is what _handle_documents does with one item.
type CourseLocationDocumentStep int

const (
	// CourseLocationDocumentMissingKey raises ValidationError({'exception':
	// 'document key not present'}) (400).
	CourseLocationDocumentMissingKey CourseLocationDocumentStep = iota
	// CourseLocationDocumentUndecodable fails base64.b64decode (500).
	CourseLocationDocumentUndecodable
	// CourseLocationDocumentUpload creates a Document and saves it to storage.
	CourseLocationDocumentUpload
)

// CourseLocationDocument classifies one documents_data item in the order
// _handle_documents checks it.
func CourseLocationDocument(item domain.RequestObject) CourseLocationDocumentStep {
	document, hasDocument := item.Lookup("document")
	if _, hasFilename := item.Lookup("filename"); !hasDocument || !hasFilename {
		return CourseLocationDocumentMissingKey
	}
	if text, ok := document.(string); !ok || !pythonBase64(text) {
		return CourseLocationDocumentUndecodable
	}
	return CourseLocationDocumentUpload
}

// pythonBase64 reports whether base64.b64decode(text) succeeds: the text must
// be ASCII, then binascii.a2b_base64 (non-strict) skips characters outside
// the alphabet and stops at the first complete padding.
func pythonBase64(text string) bool {
	quad, pads := 0, 0
	for index := 0; index < len(text); index++ {
		c := text[index]
		if c >= 0x80 {
			return false
		}
		if c == '=' {
			if quad >= 2 {
				pads++
				if quad+pads >= 4 {
					return true
				}
			}
			continue
		}
		if !(c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '+' || c == '/') {
			continue
		}
		pads = 0
		quad = (quad + 1) % 4
	}
	return quad == 0
}
