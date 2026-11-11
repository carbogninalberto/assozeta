// Package codicefiscale ports codicefiscale.encode from python-codicefiscale
// 0.10.3, with the parts of python-slugify 8.0.4, text-unidecode 1.3 and
// python-dateutil 2.9.0.post0 it relies on, keeping its output byte for byte.
//
// Only text that is no Python str (malformed UTF-8 other than a lone
// surrogate) is reported with ErrUnsupported; dates written with words go
// through internal/pydateutil.
package codicefiscale

import (
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrNotMapped is the library's "not mapped to code" ValueError: no
	// birthplace matches the given name or code at the birth date.
	ErrNotMapped = errors.New("codicefiscale: birthplace not mapped to code")
	// ErrInvalid is any other exception the library raises.
	ErrInvalid = errors.New("codicefiscale: invalid input")
	// ErrUnsupported marks input outside the ported subset.
	ErrUnsupported = refusal.New("codicefiscale: input outside the ported subset")
)

const (
	consonants = "bcdfghjklmnpqrstvwxyz"
	vowels     = "aeiou"
	months     = "ABCDEHLMPRST"
	// omocodiaLetters[i] replaces the digit i in omocode variants.
	omocodiaLetters = "LMNPQRSTUV"
)

// cinValues maps a code character to its (even, odd) position values.
var cinValues = func() map[byte][2]int {
	odd := []int{1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23}
	m := make(map[byte][2]int, 36)
	for i := range 10 {
		m[byte('0'+i)] = [2]int{i, odd[i]}
	}
	for i := range 26 {
		m[byte('A'+i)] = [2]int{i, odd[i]}
	}
	return m
}()

// Encode returns codicefiscale.encode(lastname, firstname, gender, birthdate,
// birthplace) as computed when datetime.now() is now (taken in UTC).
func Encode(lastname, firstname, gender, birthdate, birthplace string, now time.Time) (string, error) {
	now = now.UTC()
	data, err := loadData()
	if err != nil {
		return "", err
	}
	lastnameCode, err := encodeLastname(lastname)
	if err != nil {
		return "", err
	}
	firstnameCode, err := encodeFirstname(firstname)
	if err != nil {
		return "", err
	}
	date, dateCode, err := encodeBirthdate(birthdate, gender, now)
	if err != nil {
		return "", err
	}
	placeCode, err := encodeBirthplace(data, birthplace, date)
	if err != nil {
		return "", err
	}
	code := lastnameCode + firstnameCode + dateCode + placeCode
	cin, err := encodeCIN(code)
	if err != nil {
		return "", err
	}
	code += cin
	if err := decode(data, code, now); err != nil {
		return "", err
	}
	return code, nil
}

func encodeLastname(lastname string) (string, error) {
	slug, err := slugify(lastname)
	if err != nil {
		return "", err
	}
	return consonantsAndVowels(pick(slug, consonants), pick(slug, vowels)), nil
}

func encodeFirstname(firstname string) (string, error) {
	slug, err := slugify(firstname)
	if err != nil {
		return "", err
	}
	cons := pick(slug, consonants)
	if len(cons) > 3 {
		cons = cons[:1] + cons[2:]
	}
	return consonantsAndVowels(cons, pick(slug, vowels)), nil
}

// pick keeps the bytes of the ASCII slug that belong to set.
func pick(slug, set string) string {
	var b strings.Builder
	for i := 0; i < len(slug); i++ {
		if strings.IndexByte(set, slug[i]) >= 0 {
			b.WriteByte(slug[i])
		}
	}
	return b.String()
}

func consonantsAndVowels(cons, vows string) string {
	return strings.ToUpper((first(cons, 3) + first(vows, 3) + "XXX")[:3])
}

func first(s string, n int) string {
	return s[:min(n, len(s))]
}

// encodeBirthdate ports encode_birthdate and also returns the parsed date,
// which encode_birthplace would parse again with the same result.
func encodeBirthdate(birthdate, gender string, now time.Time) (time.Time, string, error) {
	date, err := getDate(birthdate, now)
	if err != nil {
		if errors.Is(err, ErrUnsupported) {
			return time.Time{}, "", err
		}
		return time.Time{}, "", ErrInvalid
	}
	// Only "m", "M", "f" and "F" have str.upper() in ("M", "F").
	female := gender == "F" || gender == "f"
	if !female && gender != "M" && gender != "m" {
		return time.Time{}, "", ErrInvalid
	}
	day := date.Day()
	if female {
		day += 40
	}
	year := strconv.Itoa(date.Year())
	yearCode := year[min(2, len(year)):]
	dayCode := strconv.Itoa(day)
	if len(dayCode) < 2 {
		dayCode = "0" + dayCode
	}
	return date, yearCode + string(months[date.Month()-1]) + dayCode, nil
}

func encodeBirthplace(data *lookupData, birthplace string, date time.Time) (string, error) {
	if birthplace == "" {
		return "", ErrInvalid
	}
	p, err := data.birthplace(birthplace, date)
	if err != nil {
		return "", err
	}
	if p == nil {
		// re.split(r",|\(", birthplace)[0]
		withoutProvince := birthplace
		if i := strings.IndexAny(birthplace, ",("); i >= 0 {
			withoutProvince = birthplace[:i]
		}
		if p, err = data.birthplace(withoutProvince, date); err != nil {
			return "", err
		}
	}
	if p == nil {
		return "", ErrNotMapped
	}
	return p.code, nil
}

// birthplace ports _get_birthplace(name, date) for a non-None date.
func (d *lookupData) birthplace(name string, date time.Time) (*place, error) {
	slug, err := slugify(name)
	if err != nil {
		return nil, err
	}
	options, ok := d.municipalities[slug]
	if !ok {
		options, ok = d.countries[slug]
	}
	if !ok {
		options = d.codes[strings.ToUpper(slug)]
	}
	if len(options) == 0 {
		return nil, nil
	}
	for _, p := range options {
		// None created/deleted stand for datetime.min/max.
		if (p.created.IsZero() || !date.Before(p.created)) && (p.deleted.IsZero() || !date.After(p.deleted)) {
			return p, nil
		}
	}
	return birthplaceFallback(options, date), nil
}

// birthplaceFallback ports _get_birthplace_fallback.
func birthplaceFallback(options []*place, date time.Time) *place {
	for i := 0; i+1 < len(options); i++ {
		p, next := options[i], options[i+1]
		deleted, created := p.deleted, next.created
		if deleted.IsZero() || created.IsZero() {
			continue
		}
		if !date.Before(deleted) && !deleted.After(created) {
			// Sub saturates beyond ~292 years, which keeps the comparison right.
			if !p.created.IsZero() && p.deleted.Sub(p.created) <= 24*time.Hour {
				return p
			}
			return next
		}
	}
	return nil
}

// encodeCIN ports encode_cin.
func encodeCIN(code string) (string, error) {
	if len(code) != 15 && len(code) != 16 {
		return "", ErrInvalid
	}
	total := 0
	for i := range 15 {
		v, ok := cinValues[code[i]]
		if !ok {
			return "", ErrInvalid // KeyError
		}
		if (i+1)%2 != 0 {
			total += v[1]
		} else {
			total += v[0]
		}
	}
	return string(rune('A' + total%26)), nil
}

// decode ports the checks decode(code) makes; any failure is ErrInvalid.
func decode(data *lookupData, code string, now time.Time) error {
	// decode_raw
	slug, err := slugify(code)
	if err != nil {
		return err
	}
	code = strings.ToUpper(strings.ReplaceAll(slug, "-", ""))
	if !validSyntax(code) {
		return ErrInvalid
	}
	yearValue, err := strconv.Atoi(decodeOmocodia(code[6:8]))
	if err != nil {
		return ErrInvalid
	}
	month := strings.IndexByte(months, code[8]) + 1
	day, err := strconv.Atoi(decodeOmocodia(code[9:11]))
	if err != nil {
		return ErrInvalid
	}
	if day > 40 {
		day -= 40
	}

	currentYear := now.Year()
	prefix := strconv.Itoa(currentYear)
	prefix = prefix[:max(len(prefix)-2, 0)]
	suffix := strconv.Itoa(yearValue) // str(int(...)).zfill(2)
	if len(suffix) < 2 {
		suffix = "0" + suffix
	}
	birthYear, err := strconv.Atoi(prefix + suffix)
	if err != nil {
		return ErrInvalid
	}
	if birthYear > currentYear {
		birthYear -= 100
	}
	birthdate, err := getDate(strconv.Itoa(birthYear)+"/"+strconv.Itoa(month)+"/"+strconv.Itoa(day), now)
	if err != nil {
		return ErrInvalid
	}

	placeCode := code[11:12] + decodeOmocodia(code[12:15])
	p, err := data.birthplace(placeCode, birthdate)
	if err != nil {
		return err
	}
	if p == nil {
		return ErrInvalid
	}
	cin, err := encodeCIN(code)
	if err != nil || cin != code[15:] {
		return ErrInvalid
	}
	return nil
}

// validSyntax matches CODICEFISCALE_RE against an upper case code.
func validSyntax(code string) bool {
	if len(code) != 16 {
		return false
	}
	isLetter := func(c byte) bool { return 'A' <= c && c <= 'Z' }
	isAlnum := func(c byte) bool { return isLetter(c) || isASCIIDigit(c) }
	for i := 0; i < 16; i++ {
		c := code[i]
		var ok bool
		switch {
		case i < 6, i == 11, i == 15:
			ok = isLetter(c)
		case i == 8:
			ok = strings.IndexByte(months, c) >= 0
		default:
			ok = isAlnum(c)
		}
		if !ok {
			return false
		}
	}
	return true
}

// decodeOmocodia translates omocodia letters back to digits.
func decodeOmocodia(s string) string {
	b := []byte(s)
	for i, c := range b {
		if j := strings.IndexByte(omocodiaLetters, c); j >= 0 {
			b[i] = byte('0' + j)
		}
	}
	return string(b)
}
