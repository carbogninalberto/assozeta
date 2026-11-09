package dto

import (
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// SendEmailInput is EmailSerializer's validated data.
type SendEmailInput struct{ Email, Subject, Message string }

// SendEmailInvalid is EmailSerializer.is_valid's ValidationError: List is a
// ValidationError(str) raised before DRF validation (rendered as a list),
// Fields the DRF field errors as codes (paymentCategoryValidationMessages).
type SendEmailInvalid struct {
	List   string
	Fields map[string][]string
}

func (e *SendEmailInvalid) Error() string { return "invalid email request" }

// SendEmailLookup is request.data.get(key) of a dict or QueryDict body.
func SendEmailLookup(data domain.RequestObject, key string) any {
	value, _ := data.Lookup(key)
	return value
}

// ValidateSendEmail is EmailSerializer.is_valid(raise_exception=True): the
// falsy checks in field order, email.split(',') (a non-string raises: the
// 500 page), check_email on every piece (validate_email; its own
// ValidationError), then DRF's email CharField(), subject
// CharField(max_length=100) and message CharField(max_length=10000).
func ValidateSendEmail(data domain.RequestObject) (SendEmailInput, error) {
	email, subject, message := SendEmailLookup(data, "email"), SendEmailLookup(data, "subject"), SendEmailLookup(data, "message")
	for _, check := range []struct {
		value any
		text  string
	}{{email, "Email is required"}, {subject, "Subject is required"}, {message, "Message is required"}} {
		if !PythonTruthy(check.value) {
			return SendEmailInput{}, &SendEmailInvalid{List: check.text}
		}
	}
	raw, ok := email.(string)
	if !ok {
		return SendEmailInput{}, domain.ErrSendEmailServerError
	}
	for _, piece := range strings.Split(raw, ",") {
		if !SupplierEmail(piece) {
			return SendEmailInput{}, &SendEmailInvalid{List: "Email is not valid."}
		}
	}
	fields := map[string][]string{}
	var input SendEmailInput
	for _, field := range []struct {
		name    string
		value   any
		maximum int
		target  *string
	}{{"email", email, int(^uint(0) >> 1), &input.Email}, {"subject", subject, 100, &input.Subject}, {"message", message, 10000, &input.Message}} {
		text, codes := parsePaymentCategoryString(field.value, field.maximum)
		if len(codes) > 0 {
			fields[field.name] = codes
			continue
		}
		*field.target = text
	}
	if len(fields) > 0 {
		return SendEmailInput{}, &SendEmailInvalid{Fields: fields}
	}
	return input, nil
}
