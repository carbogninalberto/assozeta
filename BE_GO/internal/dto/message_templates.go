package dto

import (
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

var MessageTemplateVariables = []string{"customer_name", "association_name", "date", "time", "link"}

var messageSample = map[string]string{
	"customer_name": "Giulia Rossi", "association_name": "ASD Aurora", "date": "2026-12-15",
	"time": "18:30", "link": "https://example.org/info",
}

var (
	messageToken  = regexp.MustCompile(`\{\{[ \t]*([a-z_]+)[ \t]*\}\}`)
	messageLocale = regexp.MustCompile(`^[a-z]{2,3}(-[a-z0-9]{2,8})*$`)
	messageTime   = regexp.MustCompile(`^([01][0-9]|2[0-3]):[0-5][0-9]$`)
	messageLink   = regexp.MustCompile(`^https?://[^/\x00-\x20\x7f?#]+[^\x00-\x20\x7f]*$`)
	messagePhone  = regexp.MustCompile(`^\+[1-9][0-9]{6,14}$`)
)

type MessageTemplateError struct{ Field, Detail string }

func (e *MessageTemplateError) Error() string { return e.Detail }

func InvalidMessageTemplate(field, detail string) error {
	return &MessageTemplateError{Field: field, Detail: detail}
}

type MessageText struct {
	Subject string `json:"subject"`
	Body    string `json:"body"`
}

func MessageTemplateID(value any) (string, error) {
	text, ok := value.(string)
	id, err := uuid.Parse(text)
	if !ok || err != nil || id.String() != strings.ToLower(text) {
		return "", domain.ErrMessageTemplateNotFound
	}
	return id.String(), nil
}

func messageTextField(data domain.RequestObject, field string, limit int, required bool) (string, error) {
	value, exists := data.Lookup(field)
	if !exists {
		value = ""
	}
	text, ok := value.(string)
	if !ok || !utf8.ValidString(text) || strings.ContainsRune(text, 0) ||
		utf8.RuneCountInString(text) > limit || (required && strings.TrimSpace(text) == "") {
		return "", InvalidMessageTemplate(field, "Testo mancante o non valido.")
	}
	return text, nil
}

func MessageTemplateChannel(data domain.RequestObject) (string, error) {
	value, _ := data.Lookup("channel")
	switch value {
	case "email", "whatsapp", "sms":
		return value.(string), nil
	}
	return "", InvalidMessageTemplate("channel", "Canale non valido.")
}

func MessageTemplateLocale(data domain.RequestObject) (string, error) {
	value, err := messageTextField(data, "locale", 35, true)
	if err != nil {
		return "", err
	}
	value = strings.ToLower(strings.TrimSpace(value))
	if !messageLocale.MatchString(value) {
		return "", InvalidMessageTemplate("locale", "Usa un codice lingua come it o en-gb.")
	}
	return value, nil
}

func OutgoingMessageText(data domain.RequestObject, channel string, required bool) (MessageText, error) {
	subject, err := messageTextField(data, "subject", 100, required && channel == "email")
	if err != nil {
		return MessageText{}, err
	}
	if (channel != "email" && subject != "") || strings.ContainsAny(subject, "\r\n") {
		return MessageText{}, InvalidMessageTemplate("subject", "Oggetto non valido.")
	}
	body, err := messageTextField(data, "body", 10000, required)
	return MessageText{Subject: subject, Body: body}, err
}

func ValidateMessageTemplate(data domain.RequestObject, current domain.MessageTemplate) (domain.MessageTemplate, error) {
	fields := domain.RequestObject{
		{Name: "channel", Value: current.Channel}, {Name: "locale", Value: current.Locale},
		{Name: "name", Value: current.Name}, {Name: "subject", Value: current.Subject}, {Name: "body", Value: current.Body},
		{Name: "enabled", Value: current.Enabled}, {Name: "is_default", Value: current.IsDefault},
	}
	// RequestObject.Lookup returns the first matching field.
	data = append(append(domain.RequestObject{}, data...), fields...)
	channel, err := MessageTemplateChannel(data)
	if err != nil {
		return current, err
	}
	content, err := OutgoingMessageText(data, channel, true)
	if err != nil {
		return current, err
	}
	locale, err := MessageTemplateLocale(data)
	if err != nil {
		return current, err
	}
	name, err := messageTextField(data, "name", 100, true)
	if err != nil {
		return current, err
	}
	current.Channel, current.Locale, current.Name = channel, locale, strings.TrimSpace(name)
	current.Subject, current.Body = content.Subject, content.Body
	for _, field := range []struct {
		name   string
		output *bool
	}{{"enabled", &current.Enabled}, {"is_default", &current.IsDefault}} {
		value, _ := data.Lookup(field.name)
		flag, ok := value.(bool)
		if !ok {
			return current, InvalidMessageTemplate(field.name, "Valore booleano richiesto.")
		}
		*field.output = flag
	}
	current.IsDefault = current.Enabled && current.IsDefault
	return current, nil
}

func SampleMessageTemplate(template domain.MessageTemplate) (MessageText, error) {
	return RenderMessageTemplate(template, messageSample)
}

func RenderMessageTemplate(template domain.MessageTemplate, values map[string]string) (MessageText, error) {
	var data domain.RequestObject
	for _, field := range []domain.RequestField{{Name: "subject", Value: template.Subject}, {Name: "body", Value: template.Body}} {
		value := field.Value.(string)
		rest := messageToken.ReplaceAllString(value, "")
		if strings.Contains(rest, "{{") || strings.Contains(rest, "}}") ||
			strings.Contains(value, "{{{") || strings.Contains(value, "}}}") {
			return MessageText{}, InvalidMessageTemplate(field.Name, "Sintassi variabile non valida. Usa {{nome_variabile}}.")
		}
		var renderErr error
		field.Value = messageToken.ReplaceAllStringFunc(value, func(token string) string {
			if renderErr != nil {
				return ""
			}
			name := messageToken.FindStringSubmatch(token)[1]
			if _, known := messageSample[name]; !known {
				renderErr = InvalidMessageTemplate(field.Name, "Variabile sconosciuta: "+name+".")
			} else if values[name] == "" {
				renderErr = InvalidMessageTemplate(name, "Inserisci un valore per questa variabile.")
			}
			return values[name]
		})
		if renderErr != nil {
			return MessageText{}, renderErr
		}
		data = append(data, field)
	}
	return OutgoingMessageText(data, template.Channel, true)
}

func MessageTemplateContext(data domain.RequestObject) (map[string]string, error) {
	values := make(map[string]string, len(MessageTemplateVariables))
	for _, field := range []struct {
		name  string
		limit int
	}{{"date", 10}, {"time", 5}, {"link", 2048}} {
		value, err := messageTextField(data, field.name, field.limit, false)
		if err != nil {
			return nil, err
		}
		values[field.name] = strings.TrimSpace(value)
	}
	if date := values["date"]; date != "" {
		if parsed, err := time.Parse(time.DateOnly, date); err != nil || parsed.Year() < 1 || parsed.Format(time.DateOnly) != date {
			return nil, InvalidMessageTemplate("date", "Data non valida.")
		}
	}
	if value := values["time"]; value != "" && !messageTime.MatchString(value) {
		return nil, InvalidMessageTemplate("time", "Ora non valida.")
	}
	if value := values["link"]; value != "" && !messageLink.MatchString(value) {
		return nil, InvalidMessageTemplate("link", "Usa un link HTTP o HTTPS.")
	}
	return values, nil
}

func MessageRecipientName(recipient domain.MessageRecipient) string {
	return strings.TrimSpace(recipient.FirstName + " " + recipient.LastName)
}

func MessageRecipientAddress(recipient domain.MessageRecipient, channel string) string {
	if channel == "email" {
		email := strings.TrimSpace(recipient.Email)
		if utf8.RuneCountInString(email) <= 254 && !strings.ContainsAny(email, ", \t\n\r\f\v") && SupplierEmail(email) {
			return email
		}
		return ""
	}
	phone := strings.NewReplacer(" ", "", "(", "", ")", "", ".", "", "-", "").Replace(strings.TrimSpace(recipient.Phone))
	if strings.HasPrefix(phone, "00") {
		phone = "+" + phone[2:]
	}
	if messagePhone.MatchString(phone) {
		return phone
	}
	return ""
}

func WhatsAppMessageURL(row domain.OutgoingMessage) string {
	return "https://wa.me/" + strings.TrimPrefix(row.Recipient, "+") + "?text=" + strings.ReplaceAll(url.QueryEscape(row.Body), "+", "%20")
}
