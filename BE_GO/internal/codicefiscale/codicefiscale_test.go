package codicefiscale

import (
	"compress/gzip"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"
)

// testNow pins datetime.now() / time.localtime() for the golden vectors.
var testNow = time.Date(2026, 11, 9, 0, 0, 0, 0, time.UTC)

type encodeCase struct {
	lastname, firstname, gender, birthdate, birthplace string
	want                                               string
	err                                                error
	now                                                string // datetime.now() date; "" means testNow
}

// goldenCases were produced by python-codicefiscale 0.10.3 (python-slugify
// 8.0.4, text-unidecode 1.3, python-dateutil 2.9.0.post0) with TZ=UTC and
// datetime.now() frozen at now (testNow when unset).
var goldenCases = []encodeCase{
	{"Rossi", "Mario", "M", "12/05/1990", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "1990-05-12", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12.05.1990", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12 05 1990", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12-5-1990", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "19900512", "Roma", "RSSMRA90T05H501W", nil, ""},
	{"Rossi", "Mario", "M", "900512", "Roma", "RSSMRA90T05H501W", nil, ""},
	{"Rossi", "Mario", "M", "12/05/90", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/30", "Roma", "RSSMRA30E12H501W", nil, ""},
	{"Rossi", "Mario", "M", "05/12/1990", "Roma", "RSSMRA90T05H501W", nil, ""},
	{"Rossi", "Mario", "M", "13/05/1990", "Roma", "RSSMRA90E13H501M", nil, ""},
	{"Rossi", "Mario", "M", "1990/31/12", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "1990", "Roma", "RSSMRA90S09H501C", nil, ""},
	{"Rossi", "Mario", "M", "5", "Roma", "RSSMRA26S05H501K", nil, ""},
	{"Rossi", "Mario", "M", "05/1990", "Roma", "RSSMRA90E09H501Z", nil, ""},
	{"Rossi", "Mario", "M", "1990/05", "Roma", "RSSMRA90E09H501Z", nil, ""},
	{"Rossi", "Mario", "M", "31/12", "Roma", "RSSMRA26T31H501C", nil, ""},
	{"Rossi", "Mario", "M", "12/31", "Roma", "RSSMRA26T31H501C", nil, ""},
	{"Rossi", "Mario", "M", "29/02/2000", "Roma", "RSSMRA00B29H501Y", nil, ""},
	{"Rossi", "Mario", "M", "29/02/2001", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "31/04/1990", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "00/01/1990", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "01/13/1990", "Roma", "RSSMRA90A13H501E", nil, ""},
	{"Rossi", "Mario", "M", "199005121030", "Roma", "RSSMRA90T05H501W", nil, ""},
	{"Rossi", "Mario", "M", "19900512103045", "Roma", "RSSMRA90T05H501W", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990 10", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990 1030", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990 10 30", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990 10 300", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "12/05/1990 10 3000", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990 103000", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990 25", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "1/2/3/4", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "1990/1991", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "1990-05-12-1991", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "0001/01/01", "Roma", "", ErrNotMapped, ""},
	{"Rossi", "Mario", "M", "01/01/0999", "Roma", "", ErrNotMapped, ""},
	{"Rossi", "Mario", "M", "01/01/1000", "Roma", "", ErrNotMapped, ""},
	{"Rossi", "Mario", "M", "01/01/10000", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "1,2,1990", "Roma", "RSSMRA90T19H501F", nil, ""},
	{"Rossi", "Mario", "M", "12'05'1990", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", " ", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "0", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "12//05//1990", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "111111111111111111111111111111", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "01/01/2030", "Roma", "RSSMRA30A01H501I", nil, ""},
	{"Rossi", "Mario", "M", "01/01/2100", "Roma", "RSSMRA00A01H501C", nil, ""},
	{"Rossi", "Maria", "F", "12/05/1990", "Roma", "RSSMRA90E52H501O", nil, ""},
	{"Rossi", "Maria", "f", "01/05/1990", "Roma", "RSSMRA90E41H501I", nil, ""},
	{"Rossi", "Maria", "X", "12/05/1990", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Maria", "", "12/05/1990", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Maria", "X", "31/02/1990", "Roma", "", ErrInvalid, ""},
	{"D'Angelo", "Zoë", "F", "1985-02-28", "Roma", "DNGZOE85B68H501O", nil, ""},
	{"Núñez", "José María", "M", "1980-01-01", "Roma", "NNZJMR80A01H501K", nil, ""},
	{"Müller-Lüdenscheidt", "Jürgen", "M", "1970-07-07", "Roma", "MLLJGN70L07H501Y", nil, ""},
	{"O’Neill", "Seán", "M", "1970-07-07", "Roma", "NLLSNE70L07H501F", nil, ""},
	{"Łukasiewicz", "Paweł", "M", "1970-07-07", "Roma", "LKSPWL70L07H501C", nil, ""},
	{"Strauß", "Æthelred", "M", "1970-07-07", "Roma", "STRTLR70L07H501H", nil, ""},
	{"Fò", "Ugo", "M", "1970-07-07", "Roma", "FOXGUO70L07H501Q", nil, ""},
	{"", "", "M", "1970-07-07", "Roma", "XXXXXX70L07H501U", nil, ""},
	{"X", "A", "M", "1970-07-07", "Roma", "XXXAXX70L07H501X", nil, ""},
	{"De La Cruz", "Gianfranco", "M", "1970-07-07", "Roma", "DLCGFR70L07H501N", nil, ""},
	{"Ṭhakur", "Ḍīpak", "M", "1970-07-07", "Roma", "THKDPK70L07H501T", nil, ""},
	{"Ⅻ ﬁ", "Ǆ", "M", "1970-07-07", "Roma", "XFIDZX70L07H501A", nil, ""},
	{"Rossi 2", "Mario€", "M", "1970-07-07", "Roma", "RSSMRR70L07H501D", nil, ""},
	{"𝐑𝐨𝐬𝐬𝐢", "Mario😀", "M", "1970-07-07", "Roma", "RSSMRA70L07H501M", nil, ""},
	{"Ro\x00ssi", "Ma\x09ri\x0ao", "M", "1970-07-07", "Roma", "RSSMRA70L07H501M", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Roma (RM)", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Roma, RM", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "roma-rm", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "ROMA", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Roma (MI)", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Roma (Lazio", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "H501", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "h501", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "F205", "RSSMRA90E12F205N", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Milano", "RSSMRA90E12F205N", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Forlì", "RSSMRA90E12D704T", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Sant'Angelo Lodigiano", "RSSMRA90E12I274T", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Reggio nell'Emilia", "RSSMRA90E12H223L", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Germania", "RSSMRA90E12Z112V", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Z112", "RSSMRA90E12Z112V", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Stati Uniti d'America", "RSSMRA90E12Z404H", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Francia", "RSSMRA90E12Z110R", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Atlantide", "", ErrNotMapped, ""},
	{"Rossi", "Mario", "M", "12/05/1990", ",", "", ErrNotMapped, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "ND", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "01/01/1900", "Arizzano", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "  roma  ", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Bolzano/Bozen", "", ErrNotMapped, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Bozen", "RSSMRA90E12A952V", nil, ""},
	{"Rossi", "Mario", "M", "01/01/1950", "Bellizzi", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "01/01/1995", "Bellizzi", "RSSMRA95A01M294Q", nil, ""},
	{"Rossi", "Mario", "M", "01/01/1900", "Capranica", "RSSMRA00A01B688Z", nil, ""},
	{"Rossi", "Mario", "M", "01/01/1950", "Ampezzo", "RSSMRA50A01A267W", nil, ""},
	{"Rossi", "Mario", "M", "01/01/1920", "Arizzano", "RSSMRA20A01A409Q", nil, ""},
	{"Rossi", "Mario", "M", "01/01/1930", "Stroncone", "RSSMRA30A01I981Z", nil, ""},
	{"Rossi", "Mario", "M", "12/01/1927", "Stroncone", "RSSMRA27A12I981K", nil, ""},
	{"Rossi", "Mario", "M", "01/01/1930", "Abbasanta", "RSSMRA30A01A007G", nil, ""},
	{"Rossi", "Mario", "M", "05/05/1900", "Bagnorea", "RSSMRA00E05A577O", nil, ""},
	{"Rossi", "Mario", "M", "05/05/1925", "Bagnoregio", "RSSMRA25E05A577X", nil, ""},
	{"Rossi", "Mario", "M", "05/05/1920", "Bagnoregio", "", ErrNotMapped, ""},
	{"Rossi", "Mario", "M", "192712301200", "Bagnoregio", "RSSMRA27T30A577V", nil, ""},
	{"Rossi", "Mario", "M", "30/12/1927", "A577", "RSSMRA27T30A577V", nil, ""},
	{"Rossi", "Mario", "M", "01/01/1850", "Roma", "", ErrNotMapped, ""},
	{"Rossi", "Mario", "M", "01/01/1890", "Roma", "RSSMRA90A01H501W", nil, ""},
	{"Rossi", "Mario", "M", "01/01/1900", "Acquate", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "01/01/1920", "Acquate", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "01/01/1880", "Abano", "RSSMRA80A01A001B", nil, ""},
	{"Rossi", "Mario", "M", "01/01/2030", "Acquate", "", ErrNotMapped, ""},
	// Python's int() refuses strings over 4300 digits; the Decimal of a
	// lone trailing token does not.
	{"Rossi", "Mario", "M", "1-" + strings.Repeat("0", 4301) + "5", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "05-12-" + strings.Repeat("0", 4296) + "1990", "Roma", "RSSMRA90T05H501W", nil, ""},
	{"Rossi", "Mario", "M", "05-12-" + strings.Repeat("0", 4297) + "1990", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", strings.Repeat("0", 5000) + "1990", "Roma", "RSSMRA90S09H501C", nil, ""},
	{"Rossi", "Mario", "M", "1-1-" + strings.Repeat("9", 400), "Roma", "", ErrInvalid, ""},
	// html references, letters in dates, lone surrogates, long runs of
	// combining marks and every script of the transliteration table.
	{"R&amp;ssi", "Mario", "M", "12/05/1990", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario&#39;", "M", "12/05/1990", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "&#x41;", "M", "12/05/1990", "Roma", "RSSAXX90E12H501M", nil, ""},
	{"毛", "Mario", "M", "12/05/1990", "Roma", "MAOMRA90E12H501B", nil, ""},
	{"Rossi", "Mario", "M", "12 maggio 1990", "Roma", "", ErrInvalid, ""},
	{"Rossi", "Mario", "M", "1990-05-12T10:00", "Roma", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Roma ☃", "RSSMRA90E12H501K", nil, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Roma\xff", "", ErrUnsupported, ""}, // no Python str has this form
	{"R&ssi;", "Ma&#;rio", "M", "12/05/1990", "Roma &foo;", "", ErrNotMapped, ""},
	{"Rossi", "Mario", "M", "12/05/1990", "Roma &amp", "", ErrNotMapped, ""},
	{"Rossi", "Mario", "M", "02/2024", "Roma", "RSSMRA24B29H501G", nil, "2026-01-31"},
	{"Rossi", "Mario", "M", "1990", "Roma", "RSSMRA90A31H501Z", nil, "2026-01-31"},
	{"Rossi", "Mario", "M", "01/01/99", "Roma", "RSSMRA99A01H501F", nil, "2049-12-31"},
	{"Rossi", "Mario", "M", "01/01/99", "Roma", "RSSMRA99A01H501F", nil, "2050-03-30"},
	{"Rossi", "Mario", "M", "01/01/1990", "Roma", "RSSMRA90A01H501W", nil, "2101-07-01"},
	{"Rossi", "Mario", "M", "01/01/2099", "Roma", "RSSMRA99A01H501F", nil, "2101-07-01"},
	// decode() rebuilds 2100-02-29, which does not exist.
	{"Rossi", "Mario", "M", "29/02/2000", "Roma", "", ErrInvalid, "2101-07-01"},
	{"Rossi", "Mario", "M", "1990-05-12-10-30-45", "Roma", "RSSMRA90E12H501K", nil, ""},
}

func TestEncodeGolden(t *testing.T) {
	for _, tc := range goldenCases {
		now := testNow
		if tc.now != "" {
			var err error
			if now, err = time.Parse(time.DateOnly, tc.now); err != nil {
				t.Fatal(err)
			}
		}
		got, err := Encode(tc.lastname, tc.firstname, tc.gender, tc.birthdate, tc.birthplace, now)
		if got != tc.want || !errors.Is(err, tc.err) || (tc.err == nil && err != nil) {
			t.Errorf("Encode(%q, %q, %q, %.40q, %q) at %s = %q, %v; want %q, %v",
				tc.lastname, tc.firstname, tc.gender, tc.birthdate, tc.birthplace,
				now.Format(time.DateOnly), got, err, tc.want, tc.err)
		}
	}
}

func TestEncodeUsesNowInUTC(t *testing.T) {
	rome := time.FixedZone("CET", 3600)
	// 2026-01-01 00:30 in Rome is still 2025-12-31 in UTC: the missing day
	// and month come from the UTC date (Python: RSSMRA90T31H501M).
	got, err := Encode("Rossi", "Mario", "M", "1990", "Roma", time.Date(2026, 1, 1, 0, 30, 0, 0, rome))
	if err != nil || got != "RSSMRA90T31H501M" {
		t.Fatalf("Encode = %q, %v; want RSSMRA90T31H501M", got, err)
	}
}

func TestDataIndex(t *testing.T) {
	d, err := loadData()
	if err != nil {
		t.Fatal(err)
	}
	if len(d.municipalities) != 25227 || len(d.countries) != 365 || len(d.codes) != 10081 {
		t.Fatalf("index sizes %d/%d/%d", len(d.municipalities), len(d.countries), len(d.codes))
	}
	roma := d.codes["H501"]
	if len(roma) != 11 || roma[0].created.Format(isoLayout) != "1884-09-11T00:00:00" || !roma[10].deleted.IsZero() {
		t.Fatalf("H501 records: %+v", roma)
	}
}

func TestUnidecodeTable(t *testing.T) {
	for r, want := range map[rune]string{0: "\x00", 'é': "e", 'ß': "ss", 'Æ': "AE", '€': "EUR", 0x1F600: "", '毛': "Mao ", 0xFFFF: ""} {
		if got := unidecode(r); got != want {
			t.Errorf("unidecode(%U) = %q; want %q", r, got, want)
		}
	}
}

// pyString is a Python str given as code points; a lone surrogate takes its
// three-byte generalized UTF-8 form, as the request decoder hands it over.
func pyString(points []rune) string {
	var b strings.Builder
	for _, r := range points {
		if isSurrogate(r) {
			b.Write([]byte{0xe0 | byte(r>>12), 0x80 | byte(r>>6)&0x3f, 0x80 | byte(r)&0x3f})
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

// TestEncodeOracle replays testdata/golden.json.gz, produced by
// testdata/gen_golden.py from the real library.
func TestEncodeOracle(t *testing.T) {
	file, err := os.Open("testdata/golden.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	reader, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Last, First, Date, Place []rune
		Sex, Result              string
	}
	if err = json.NewDecoder(reader).Decode(&cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) < 3000 {
		t.Fatalf("%d cases", len(cases))
	}
	now := time.Date(2026, 11, 9, 13, 45, 12, 0, time.UTC)
	failures := 0
	for _, tc := range cases {
		got, err := Encode(pyString(tc.Last), pyString(tc.First), tc.Sex, pyString(tc.Date), pyString(tc.Place), now)
		switch {
		case errors.Is(err, ErrNotMapped):
			got = "notmapped"
		case errors.Is(err, ErrInvalid):
			got = "invalid"
		case err != nil:
			got = "error: " + err.Error()
		}
		if got != tc.Result {
			if failures++; failures <= 25 {
				t.Errorf("Encode(%q, %q, %q, %q, %q) = %q; want %q", pyString(tc.Last), pyString(tc.First), tc.Sex, pyString(tc.Date),
					pyString(tc.Place), got, tc.Result)
			}
		}
	}
	if failures > 0 {
		t.Errorf("%d of %d cases differ", failures, len(cases))
	}
}
