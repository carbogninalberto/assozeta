package service

import (
	"encoding/json"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/codicefiscale"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pandascsv"
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

// subscriptionImportMoment is a date cell: a Timestamp or a datetime, with
// its str().
type subscriptionImportMoment struct {
	text string
	at   time.Time
}

// subscriptionImportTextFields is SubscriptionImportService.TEXT_FIELDS: the
// fields whose columns are read as text.
var subscriptionImportTextFields = map[string]bool{"tax_code": true, "address_cap": true, "phone": true, "subscription_number": true}

// subscriptionImportTextColumns is text_columns(map): the columns the map
// gives to the fields read as text.
func subscriptionImportTextColumns(mapping any) map[string]bool {
	columns := map[string]bool{}
	keys, isObject := mapping.(domain.RequestObject)
	if !isObject {
		return columns
	}
	for _, entry := range subscriptionImportUnique(keys) {
		field := subscriptionImportFields[dto.PythonStrip(strings.ReplaceAll(entry.Name, "tutore", ""))]
		if column, isText := entry.Value.(string); isText && subscriptionImportTextFields[field] {
			columns[column] = true
		}
	}
	return columns
}

// subscriptionImportDate is import_date(text) for a value that is no date
// cell: dateutil.parser.parse(text, dayfirst=...) with the day first unless
// the text starts with the year (dto.PersonDateText), at now. parsed is false
// where parse raises (import_date answers None); a year before 1000 and the
// texts internal/pydateutil refuses are not ported.
func subscriptionImportDate(text string, now time.Time) (date time.Time, parsed bool, err error) {
	date, result, _ := dto.PersonDateText(text, now)
	switch result {
	case dto.DateutilParsed:
		return date, true, nil
	case dto.DateutilFailed:
		return time.Time{}, false, nil
	}
	return time.Time{}, false, ErrSubscriptionImportNotPorted
}

// subscriptionImportUnstorable is a value json.dumps does not encode (a
// time of day, a timedelta) or PostgreSQL does not store (an infinite
// float): a row that keeps one fails the import. text is str(value).
type subscriptionImportUnstorable struct {
	text   string
	truthy bool
}

// subscriptionImportRefused is a cell whose Python text is not reproduced.
type subscriptionImportRefused struct{}

// subscriptionImportCell is the Python value of a workbook cell after
// process_row_value's strip, 'nan' check, NaT check (an empty cell of a
// date column is None) and item() of a NumPy scalar.
func subscriptionImportCell(value pandasxlsx.Value) any {
	switch value.Kind {
	case pandasxlsx.String:
		text := dto.PythonStrip(value.S)
		if text == "nan" {
			return nil
		}
		return text
	case pandasxlsx.Int, pandasxlsx.Int64:
		return json.Number(strconv.FormatInt(value.I, 10))
	case pandasxlsx.Uint64, pandasxlsx.BigInt:
		return json.Number(value.Str())
	case pandasxlsx.Float:
		if math.IsInf(value.F, 0) {
			return subscriptionImportUnstorable{text: value.Str(), truthy: true}
		}
		return value.F
	case pandasxlsx.Bool, pandasxlsx.NumpyBool:
		return value.B
	case pandasxlsx.Time:
		if value.T.Year() < 1000 {
			return subscriptionImportRefused{} // strftime's year width
		}
		return subscriptionImportMoment{text: value.TimeText(), at: value.T}
	case pandasxlsx.Date:
		if value.T.Year() < 1000 {
			return subscriptionImportRefused{}
		}
		return subscriptionImportMoment{text: value.Str(), at: value.T}
	case pandasxlsx.TimeOfDay, pandasxlsx.Timedelta, pandasxlsx.PyTimedelta:
		return subscriptionImportUnstorable{text: value.Str(), truthy: value.Truthy()}
	}
	return nil // NaN, NaT
}

// subscriptionImportCSVCell is subscriptionImportCell for a cell of read_csv.
func subscriptionImportCSVCell(value pandascsv.Value) any {
	switch value.Kind {
	case pandascsv.String:
		text := dto.PythonStrip(value.S)
		if text == "nan" {
			return nil
		}
		return text
	case pandascsv.Int, pandascsv.Uint:
		return json.Number(value.Text())
	case pandascsv.Float:
		if math.IsInf(value.F, 0) {
			return subscriptionImportUnstorable{text: value.Text(), truthy: true}
		}
		return value.F
	case pandascsv.Bool:
		return value.B
	}
	return nil // NaN
}

// subscriptionImportTruthy is bool(value); text is str(value) for dateutil,
// refused for numbers (their reading depends on the current date).
func subscriptionImportTruthy(value any) (truthy bool, text string, err error) {
	switch typed := value.(type) {
	case nil:
		return false, "", nil
	case string:
		return typed != "", typed, nil
	case bool:
		if typed {
			return true, "True", nil
		}
		return false, "", nil
	case subscriptionImportUnstorable:
		return typed.truthy, typed.text, nil
	case subscriptionImportMoment:
		return true, typed.text, nil
	case json.Number:
		// str(int): the digits.
		return typed != "0", string(typed), nil
	case float64:
		return typed != 0, dto.PythonFloatText(typed), nil
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
// stores. An exception rolls the rows back: the effects are Failed.
func (s *SubscriptionImportUpload) subscriptionImportPlan(names []string, rows [][]any, data domain.RequestObject) (domain.SubscriptionImportEffects, error) {
	effects := domain.SubscriptionImportEffects{StatusID: s.uploads.ids()}
	columns := make(map[string]int, len(names))
	for index, name := range names {
		columns[name] = index
	}
	mapping, _ := data.Lookup("map")
	defaults, hasDefaults := data.Lookup("default")
	static, hasStatic := data.Lookup("unmapped_static")
	staticTutor, hasStaticTutor := data.Lookup("unmapped_static_tutor")
	now := s.uploads.now()
	failed := func() (domain.SubscriptionImportEffects, error) {
		effects.Failed, effects.Certificates, effects.Drafts = true, nil, nil
		return effects, nil
	}
	for _, cells := range rows {
		keys, isObject := mapping.(domain.RequestObject)
		if !isObject {
			return failed() // data['map'].keys()
		}
		row := subscriptionImportRow{associate: domain.RequestObject{}, tutor: domain.RequestObject{}}
		for _, entry := range subscriptionImportUnique(keys) {
			field, known := subscriptionImportFields[dto.PythonStrip(strings.ReplaceAll(entry.Name, "tutore", ""))]
			if !known {
				return failed() // KeyError
			}
			var value any
			switch column := entry.Value.(type) {
			case nil:
			case string:
				if index, found := columns[column]; found {
					value = cells[index]
					if _, refused := value.(subscriptionImportRefused); refused {
						return effects, ErrSubscriptionImportNotPorted
					}
				}
			case domain.RequestObject, []any:
				return failed() // unhashable in row.keys()
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
					date, parsed, err := subscriptionImportDate(text, s.uploads.now())
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
					date, parsed, err := subscriptionImportDate(text, s.uploads.now())
					if err != nil {
						return effects, err
					}
					if parsed {
						certificate.Expiration = &date
						row.medical = domain.RequestObject{{Name: "medical_id", Value: certificate.ID}, {Name: "filename", Value: nil},
							{Name: "certificate_expring_date", Value: date.Format("02/01/2006")}}
					}
					effects.Certificates = append(effects.Certificates, certificate)
				}
			}
			if moment, cell := value.(subscriptionImportMoment); cell {
				// stored_value: a date cell is stored as day/month/year.
				value = moment.at.Format("02/01/2006")
			}
			target := &row.associate
			if strings.Contains(entry.Name, "tutore") {
				target = &row.tutor
			}
			if value != nil {
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
				return failed() // .items()
			}
			for _, field := range object {
				subscriptionImportSet(&row.associate, field.Name, field.Value)
			}
		}
		if hasStaticTutor && len(row.tutor) > 0 {
			object, isObject := staticTutor.(domain.RequestObject)
			if !isObject {
				return failed()
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
				return failed() // re.search on a value that is not text
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
	for index := range effects.Drafts {
		if object, isObject := effects.Drafts[index].Data.(domain.RequestObject); isObject && !subscriptionImportStorable(object) {
			return failed() // json.dumps or the jsonb column refuses the value at bulk_create
		}
		effects.Drafts[index].JSON = dto.PythonJSONDumps(effects.Drafts[index].Data)
		effects.Drafts[index].Data = nil
	}
	return effects, nil
}

// subscriptionImportStorable reports whether no value of the draft is a
// subscriptionImportUnstorable cell (request values are JSON already).
func subscriptionImportStorable(object domain.RequestObject) bool {
	for _, field := range object {
		switch typed := field.Value.(type) {
		case subscriptionImportUnstorable:
			return false
		case domain.RequestObject:
			if !subscriptionImportStorable(typed) {
				return false
			}
		}
	}
	return true
}
