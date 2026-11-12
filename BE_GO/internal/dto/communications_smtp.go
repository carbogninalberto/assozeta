package dto

import (
	"errors"
	"math/big"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ErrCommunicationSMTPInvalid is configuration_smtp_update's generic
// {"msg": "info not valid."}: every validation error collapses into it.
var ErrCommunicationSMTPInvalid = errors.New("info not valid.")

// CommunicationSMTPPatch holds the present fields of a partial
// CommunicationConfigurationPatchSerializer; a nil outer pointer is absent.
type CommunicationSMTPPatch struct {
	Host, User, Password, SenderName **string
	Port                             **int64
	Encryption                       *string
}

// CommunicationConfigurationInfo is CommunicationConfigurationSerializer (never the password).
type CommunicationConfigurationInfo struct {
	Host         *string `json:"email_smtp_host"`
	Port         *int64  `json:"email_smtp_port"`
	User         *string `json:"email_smtp_user"`
	SenderName   *string `json:"email_sender_name"`
	Encryption   string  `json:"email_encryption"`
	DailyLimit   int64   `json:"daily_email_limit"`
	DailyBalance int64   `json:"daily_email_balance"`
}

func CommunicationConfigurationInfoFrom(row domain.CommunicationConfigurationRecord) CommunicationConfigurationInfo {
	return CommunicationConfigurationInfo{Host: row.Host, Port: row.Port, User: row.User, SenderName: row.SenderName,
		Encryption: row.Encryption, DailyLimit: row.DailyLimit, DailyBalance: row.DailyBalance}
}

var communicationSMTPEncryptions = map[string]bool{"SSL": true, "TLS": true, "NONE": true}

// ParseCommunicationSMTPPatch ports the partial serializer: CharField(255,
// null, blank) text fields, PositiveIntegerField port (0..2147483647, null),
// and the SSL/TLS/NONE choice; read-only and unknown keys are ignored.
func ParseCommunicationSMTPPatch(body any, form bool) (CommunicationSMTPPatch, error) {
	var patch CommunicationSMTPPatch
	object, ok := body.(domain.RequestObject)
	if !ok {
		return patch, ErrCommunicationSMTPInvalid
	}
	for _, target := range []struct {
		name  string
		field ***string
	}{{"email_smtp_host", &patch.Host}, {"email_smtp_user", &patch.User}, {"email_smtp_password", &patch.Password},
		{"email_sender_name", &patch.SenderName}} {
		value, present := object.Lookup(target.name)
		if !present {
			continue
		}
		text, codes := modelTextValue(SupplierField{Name: target.name, Max: 255, Null: true, Blank: true}, value, form)
		if len(codes) > 0 {
			return patch, ErrCommunicationSMTPInvalid
		}
		*target.field = &text
	}
	if value, present := object.Lookup("email_smtp_port"); present {
		var port *int64
		if form && value == "" {
			// get_value: an empty HTML input of a nullable integer is None.
			value = nil
		}
		if value != nil {
			number, code := DRFInteger(value)
			if code != "" || number.Sign() < 0 || number.Cmp(big.NewInt(2147483647)) > 0 {
				return patch, ErrCommunicationSMTPInvalid
			}
			parsed := number.Int64()
			port = &parsed
		}
		patch.Port = &port
	}
	if value, present := object.Lookup("email_encryption"); present {
		text, isString := value.(string)
		if !isString || !communicationSMTPEncryptions[text] {
			return patch, ErrCommunicationSMTPInvalid
		}
		patch.Encryption = &text
	}
	return patch, nil
}
