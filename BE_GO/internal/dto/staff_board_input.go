package dto

import (
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// StaffBoardMessageInput is StaffBoardMessageInputSerializer's validated data.
// A nil pointer or false DocumentSet means the key is absent; a set document
// with a nil value is an explicit null (content replaced a rich document).
type StaffBoardMessageInput struct {
	Content     *string
	DocumentSet bool
	Document    any
	Pinned      *bool
}

// StaffBoardInputField lists DRF-ordered error codes for one field or for
// non_field_errors. Codes reuse the payment-category vocabulary, plus "json"
// and "text:<message>" for literal Italian messages from the serializer.
type StaffBoardInputField struct {
	Name  string
	Codes []string
}

type StaffBoardInputError struct{ Fields []StaffBoardInputField }

func (e *StaffBoardInputError) Error() string { return "invalid staff-board message input" }

const staffBoardEmptyMessage = "text:Scrivi un messaggio o aggiungi un’immagine."

func staffBoardNonField(code string) error {
	return &StaffBoardInputError{Fields: []StaffBoardInputField{{Name: "non_field_errors", Codes: []string{code}}}}
}

// ParseStaffBoardMessageInput ports DRF 3.17 field resolution for the serializer:
// content (CharField, max 10000, no blank, then validate_content), document
// (JSONField) and pinned (BooleanField), then the serializer's validate(). Form
// bodies carry last values as strings and follow Field.get_value: an empty
// optional value is skipped, a form document is parsed as JSON, and a missing
// pinned in a non-partial form validates as false (default_empty_html).
func ParseStaffBoardMessageInput(body any, form, partial bool) (StaffBoardMessageInput, error) {
	var input StaffBoardMessageInput
	if body == nil {
		return input, staffBoardNonField("no-data")
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return input, staffBoardNonField("dictionary:" + paymentCategoryInputType(body))
	}
	var fieldErrors []StaffBoardInputField
	fail := func(name string, codes ...string) {
		fieldErrors = append(fieldErrors, StaffBoardInputField{Name: name, Codes: codes})
	}
	if value, present := object.Lookup("content"); present && !(form && value == "") {
		text, isString := value.(string)
		switch {
		case isString && pythonStrip(text) == "":
			fail("content", PaymentCategoryValidationBlank)
		default:
			if cleaned, codes := parsePaymentCategoryString(value, 10000); len(codes) > 0 {
				fail("content", codes...)
			} else if !isString {
				fail("content", "text:Il messaggio deve essere un testo.")
			} else {
				input.Content = &cleaned
			}
		}
	}
	var document any
	documentPresent := false
	if value, present := object.Lookup("document"); present {
		if form {
			text, _ := value.(string)
			parsed, err := ParseProfileTablesJSON([]byte(text))
			if err != nil {
				fail("document", "json")
			} else {
				document, documentPresent = parsed, true
			}
		} else if value == nil {
			fail("document", PaymentCategoryValidationNull)
		} else {
			document, documentPresent = value, true
		}
	}
	if value, present := object.Lookup("pinned"); present && !(form && value == "") {
		if value == nil {
			fail("pinned", PaymentCategoryValidationNull)
		} else if pinned, err := parsePaymentCategoryBoolean(value); err != nil {
			fail("pinned", PaymentCategoryValidationBoolean)
		} else {
			input.Pinned = &pinned
		}
	} else if !present && form && !partial {
		pinned := false
		input.Pinned = &pinned
	}
	if len(fieldErrors) > 0 {
		return StaffBoardMessageInput{}, &StaffBoardInputError{Fields: fieldErrors}
	}
	switch {
	case documentPresent:
		cleaned, text, err := ValidateStaffBoardDocument(document)
		var invalid *StaffBoardDocumentError
		if errors.As(err, &invalid) {
			return StaffBoardMessageInput{}, staffBoardNonField("text:" + invalid.Message)
		}
		if err != nil {
			return StaffBoardMessageInput{}, err
		}
		input.Content, input.Document, input.DocumentSet = &text, cleaned, true
	case input.Content != nil:
		input.DocumentSet = true
	case !partial:
		return StaffBoardMessageInput{}, staffBoardNonField(staffBoardEmptyMessage)
	}
	return input, nil
}
