package dto

import (
	"regexp"
	"sort"
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
// related-field lookups: Text holds present text fields (nil for None). The
// primary key is read-only: a course_location_id in the body is ignored.
type CourseLocationWrite struct {
	Text               map[string]*string
	AssociationPresent bool
	AssociationID      *string
	// Documents is the validated documents_data list (dicts).
	Documents []domain.RequestObject
}

const (
	courseLocationList = "not-a-list:"
	courseLocationDict = "not-a-dict:"
)

// ParseCourseLocationWrite ports CourseLocationSerializer(data=...,
// partial=...) field validation; the sport_association queryset and tenant
// scope are checked by the service.
func ParseCourseLocationWrite(body any, form, partial bool) (CourseLocationWrite, map[string]any) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return CourseLocationWrite{}, map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(body)}}
	}
	write := CourseLocationWrite{Text: map[string]*string{}}
	errors := map[string]any{}
	var items any
	var present bool
	if form {
		items, present = courseLocationFormDocuments(object, partial)
	} else {
		items, present = object.Lookup("documents_data")
	}
	if present {
		switch items := items.(type) {
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
			errors["documents_data"] = []string{courseLocationList + pythonTypeName(items)}
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

var courseLocationIndexed = regexp.MustCompile(`^documents_data\[([0-9]+)\](.*)$`)

// courseLocationFormDocuments is ListField.get_value of an HTML input: every value of the key
// documents_data when present, else (not for a partial update, which skips this) the list DRF's
// parse_html_list builds from documents_data[N]<name> keys, each index a dict from its .name keys.
func courseLocationFormDocuments(object domain.RequestObject, partial bool) (any, bool) {
	if values, present := object.List("documents_data"); present {
		return values, true
	}
	if partial {
		return nil, false
	}
	type indexed struct {
		number string
		value  any
		dict   domain.RequestObject
		isDict bool
	}
	byIndex := map[string]*indexed{}
	order := []*indexed{}
	for _, field := range object {
		// Python's `.` skips a newline and `$` also matches before one final newline.
		candidate := strings.TrimSuffix(field.Name, "\n")
		match := courseLocationIndexed.FindStringSubmatch(candidate)
		if match == nil || strings.Contains(candidate, "\n") {
			continue
		}
		number := strings.TrimLeft(match[1], "0")
		if number == "" {
			number = "0"
		}
		entry := byIndex[number]
		if entry == nil {
			entry = &indexed{number: number}
			byIndex[number] = entry
			order = append(order, entry)
		}
		if match[2] == "" {
			entry.value, entry.isDict, entry.dict = field.Value, false, nil
			continue
		}
		if !entry.isDict {
			entry.value, entry.isDict, entry.dict = nil, true, domain.RequestObject{}
		}
		replaced := false
		for i := range entry.dict {
			if entry.dict[i].Name == match[2] {
				entry.dict[i].Value, replaced = field.Value, true
			}
		}
		if !replaced {
			entry.dict = append(entry.dict, domain.RequestField{Name: match[2], Value: field.Value})
		}
	}
	if len(order) == 0 {
		return nil, false
	}
	sort.SliceStable(order, func(i, j int) bool {
		if len(order[i].number) != len(order[j].number) {
			return len(order[i].number) < len(order[j].number)
		}
		return order[i].number < order[j].number
	})
	items := make([]any, 0, len(order))
	for _, entry := range order {
		if !entry.isDict {
			items = append(items, entry.value)
			continue
		}
		// DictField reads the .name keys of the html dict (parse_html_dict with an empty prefix).
		result := domain.RequestObject{}
		for _, field := range entry.dict {
			key := strings.TrimSuffix(field.Name, "\n")
			if !strings.HasPrefix(key, ".") || len(key) < 2 || strings.Contains(key, "\n") {
				continue
			}
			name, replaced := key[1:], false
			for i := range result {
				if result[i].Name == name {
					result[i].Value, replaced = field.Value, true
				}
			}
			if !replaced {
				result = append(result, domain.RequestField{Name: name, Value: field.Value})
			}
		}
		items = append(items, result)
	}
	return items, true
}
