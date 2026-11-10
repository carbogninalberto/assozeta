package service

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/codicefiscale"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pandasxlsx"
)

// subscriptionImportFields is SubscriptionMaps.KEY_TO_FIELD_ASSOCIATE.
var subscriptionImportFields = map[string]string{"nome": "first_name", "cognome": "last_name", "sesso": "sex", "codice fiscale": "tax_code",
	"data di nascita": "born_date", "città di nascita": "born_city", "indirizzo": "address", "cap": "address_cap", "email": "email",
	"città di residenza": "address_city", "telefono": "phone", "scadenza certificato medico": "certificate_expiring_date",
	"numero tessera": "subscription_number", "tipo tessera": "subscription_type", "data iscrizione": "start_date",
	"data scadenza iscrizione": "end_date", "data tesseramento": "membership_start_date",
	"data scadenza tesseramento": "membership_end_date"}

// subscriptionImportMandatory is SubscriptionMaps.get_mandatory_fields().
var subscriptionImportMandatory = []string{"first_name", "last_name", "sex", "tax_code", "born_date", "born_city", "address", "address_cap",
	"address_city"}

// subscriptionImportTaxCode is process_import_rows' pattern for ASCII text
// ('$' also matches before a final line feed).
var subscriptionImportTaxCode = regexp.MustCompile(`^[a-zA-Z]{6}[0-9]{2}[abcdehlmprstABCDEHLMPRST][0-9]{2}[a-zA-Z][0-9]{3}[a-zA-Z]\n?$`)

var subscriptionImportSpacedMoment = regexp.MustCompile(`^([0-9]{4}-[0-9]{2}-[0-9]{2}) ([0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?)$`)

// subscriptionImportDateWords are the first letters of dateutil's month and
// weekday names: text with none of them and no digit is not a date.
var subscriptionImportDateWords = []string{"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "mon", "tue",
	"wed", "thu", "fri", "sat", "sun"}

// subscriptionImportUnstorable is a cell json.dumps cannot encode: a
// Timestamp or a datetime.
type subscriptionImportUnstorable struct{ text string }

// subscriptionImportDate is dateutil.parser.parse(text).date() on the ported
// forms (dto.DateutilDate, and 'YYYY-MM-DD HH:MM:SS[.ffffff]' as str() of a
// datetime). parsed is false where parse raises; other text is refused.
func subscriptionImportDate(text string) (date time.Time, parsed bool, err error) {
	candidate := text
	if match := subscriptionImportSpacedMoment.FindStringSubmatch(text); match != nil {
		candidate = match[1] + "T" + match[2]
	}
	date, result := dto.DateutilDate(candidate)
	switch result {
	case dto.DateutilParsed:
		return date, true, nil
	case dto.DateutilFailed:
		return time.Time{}, false, nil
	}
	lowered := strings.ToLower(text)
	for _, r := range text {
		if unicode.IsDigit(r) || unicode.IsNumber(r) {
			return time.Time{}, false, ErrSubscriptionImportNotPorted
		}
	}
	for _, word := range subscriptionImportDateWords {
		if strings.Contains(lowered, word) {
			return time.Time{}, false, ErrSubscriptionImportNotPorted
		}
	}
	return time.Time{}, false, nil
}

// subscriptionImportCell is the Python value of a cell after
// process_row_value's strip, 'nan' check and NaT check (an empty cell of a
// date column is None).
func subscriptionImportCell(value pandasxlsx.Value) any {
	switch value.Kind {
	case pandasxlsx.String:
		text := dto.PythonStrip(value.S)
		if text == "nan" {
			return nil
		}
		return text
	case pandasxlsx.Int:
		return json.Number(strconv.FormatInt(value.I, 10))
	case pandasxlsx.Float:
		return value.F
	case pandasxlsx.Time:
		return subscriptionImportUnstorable{text: value.TimeText()}
	case pandasxlsx.NaT:
		return nil
	}
	return nil
}

// subscriptionImportTruthy is bool(value); text is str(value) for dateutil,
// refused for numbers (their reading depends on the current date).
func subscriptionImportTruthy(value any) (truthy bool, text string, err error) {
	switch typed := value.(type) {
	case nil:
		return false, "", nil
	case string:
		return typed != "", typed, nil
	case subscriptionImportUnstorable:
		return true, typed.text, nil
	case json.Number:
		if typed == "0" {
			return false, "", nil
		}
	case float64:
		if typed == 0 {
			return false, "", nil
		}
	}
	return true, "", ErrSubscriptionImportNotPorted
}

type subscriptionImportRow struct {
	associate, tutor domain.RequestObject
	medical          domain.RequestObject
}

func subscriptionImportSet(target *domain.RequestObject, name string, value any) {
	for index := range *target {
		if (*target)[index].Name == name {
			(*target)[index].Value = value
			return
		}
	}
	*target = append(*target, domain.RequestField{Name: name, Value: value})
}

// subscriptionImportUnique is a parsed JSON object as a Python dict: a
// repeated key keeps its first position and its last value.
func subscriptionImportUnique(object domain.RequestObject) domain.RequestObject {
	unique := make(domain.RequestObject, 0, len(object))
	for _, field := range object {
		subscriptionImportSet(&unique, field.Name, field.Value)
	}
	return unique
}

// subscriptionImportPlan is process_import_rows over the frame: the
// certificates in the order they are created and the drafts bulk_create
// stores. An exception the view swallows keeps the certificates created
// before it and drops every draft.
func (s *SubscriptionImportUpload) subscriptionImportPlan(frame *pandasxlsx.Frame, data domain.RequestObject) (domain.SubscriptionImportEffects, error) {
	effects := domain.SubscriptionImportEffects{StatusID: s.uploads.ids()}
	columns := make(map[string]int, len(frame.Columns))
	for index, name := range frame.Columns {
		columns[name] = index
	}
	mapping, _ := data.Lookup("map")
	defaults, hasDefaults := data.Lookup("default")
	static, hasStatic := data.Lookup("unmapped_static")
	staticTutor, hasStaticTutor := data.Lookup("unmapped_static_tutor")
	now := s.uploads.now()
	storable := true
	for _, cells := range frame.Rows {
		keys, isObject := mapping.(domain.RequestObject)
		if !isObject {
			return effects, nil // data['map'].keys()
		}
		row := subscriptionImportRow{associate: domain.RequestObject{}, tutor: domain.RequestObject{}}
		for _, entry := range subscriptionImportUnique(keys) {
			field, known := subscriptionImportFields[dto.PythonStrip(strings.ReplaceAll(entry.Name, "tutore", ""))]
			if !known {
				return effects, nil // KeyError
			}
			var value any
			switch column := entry.Value.(type) {
			case nil:
			case string:
				if index, found := columns[column]; found {
					value = subscriptionImportCell(cells[index])
				}
			case domain.RequestObject, []any:
				return effects, nil // unhashable in row.keys()
			default:
				// A number or a boolean is no column name.
			}
			switch field {
			case "membership_start_date", "membership_end_date", "born_date":
				truthy, text, err := subscriptionImportTruthy(value)
				if err != nil {
					return effects, err
				}
				if truthy {
					date, parsed, err := subscriptionImportDate(text)
					if err != nil {
						return effects, err
					}
					value = nil
					if parsed {
						value = date.Format("02/01/2006")
					}
				}
			case "sex":
				if value == nil {
					value = "A"
				}
			case "certificate_expiring_date":
				truthy, text, err := subscriptionImportTruthy(value)
				if err != nil {
					return effects, err
				}
				if truthy {
					certificate := domain.SubscriptionImportCertificate{ID: s.uploads.ids()}
					date, parsed, err := subscriptionImportDate(text)
					if err != nil {
						return effects, err
					}
					if parsed {
						certificate.Expiration = &date
						row.medical = domain.RequestObject{{Name: "medical_id", Value: certificate.ID}, {Name: "filename", Value: nil},
							{Name: "certificate_expring_date", Value: date.Format("02/01/2006")}}
					}
					effects.Certificates = append(effects.Certificates, certificate)
					if _, cell := value.(subscriptionImportUnstorable); cell && parsed {
						// stored_certificate_date: a date cell is stored as day/month/year.
						value = date.Format("02/01/2006")
					}
				}
			}
			target := &row.associate
			if strings.Contains(entry.Name, "tutore") {
				target = &row.tutor
			}
			if value != nil {
				if _, unstorable := value.(subscriptionImportUnstorable); unstorable {
					storable = false
				}
				subscriptionImportSet(target, field, value)
			} else if hasDefaults {
				object, isObject := defaults.(domain.RequestObject)
				if !isObject {
					return effects, ErrSubscriptionImportNotPorted // key in a str, a list or None
				}
				if fallback, present := subscriptionImportUnique(object).Lookup(entry.Name); present {
					subscriptionImportSet(target, field, fallback)
				}
			}
		}
		for _, person := range []*domain.RequestObject{&row.associate, &row.tutor} {
			code, present := person.Lookup("tax_code")
			if city, _ := person.Lookup("born_city"); !present || code == nil || city != nil {
				continue
			}
			text, isText := code.(string)
			if !isText {
				continue // decode raises on anything but text
			}
			name, found, err := codicefiscale.DecodedBirthplaceName(text, now)
			if err != nil {
				return effects, ErrSubscriptionImportNotPorted
			}
			if found {
				subscriptionImportSet(person, "born_city", name)
			}
		}
		if hasStatic {
			object, isObject := static.(domain.RequestObject)
			if !isObject {
				return effects, nil // .items()
			}
			for _, field := range object {
				subscriptionImportSet(&row.associate, field.Name, field.Value)
			}
		}
		if hasStaticTutor && len(row.tutor) > 0 {
			object, isObject := staticTutor.(domain.RequestObject)
			if !isObject {
				return effects, nil
			}
			for _, field := range object {
				subscriptionImportSet(&row.tutor, field.Name, field.Value)
			}
		}
		valid := true
		for _, name := range subscriptionImportMandatory {
			value, present := row.associate.Lookup(name)
			if text, isText := value.(string); !present || value == nil || isText && text == "" {
				valid = false
				break
			}
		}
		if valid {
			code, _ := row.associate.Lookup("tax_code")
			text, isText := code.(string)
			if !isText {
				return effects, nil // re.search on a value that is not text
			}
			for _, r := range text {
				if r > unicode.MaxASCII {
					// IGNORECASE folds a few letters beyond ASCII into the classes.
					return effects, ErrSubscriptionImportNotPorted
				}
			}
			valid = subscriptionImportTaxCode.MatchString(text)
		}
		stored := domain.RequestObject{{Name: "associate", Value: row.associate}, {Name: "associate_tutor", Value: row.tutor}}
		if row.medical != nil {
			stored = append(stored, domain.RequestField{Name: "medical_certificate", Value: row.medical})
		}
		effects.Drafts = append(effects.Drafts, domain.SubscriptionImportDraft{ID: s.uploads.ids(), Data: stored, Valid: valid})
	}
	if !storable {
		effects.Drafts = nil // json.dumps raises inside bulk_create
		return effects, nil
	}
	for index := range effects.Drafts {
		effects.Drafts[index].JSON = dto.PythonJSONDumps(effects.Drafts[index].Data)
		effects.Drafts[index].Data = nil
	}
	return effects, nil
}
