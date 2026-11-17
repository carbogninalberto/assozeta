package pycodecs

// This file ports RFC 3491 nameprep as CPython 3.13's encodings.idna runs it
// (Lib/encodings/idna.py, Lib/stringprep.py):
//
//	Map      RFC 3454 B.1 (map to nothing) and B.2 (map_table_b2)
//	Normalize Unicode 3.2.0 NFKC
//	Prohibit RFC 3454 C.1.2, C.2.2 and C.3 to C.9
//	Bidi     RFC 3454 D.1/D.2 if the label holds a RandALCat character
//
// The tables and the normalization database come from the reference
// interpreter through testdata/gen_idna.py and are embedded in
// idna_tables.json.gz. Working on []rune keeps the lone surrogates that
// punycode can decode (CPython's str can hold them); nameprep then refuses
// them as C.5 prohibited characters.

import (
	"bytes"
	"compress/gzip"
	_ "embed"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"sync"
)

// idna_tables.json.gz is written by testdata/gen_idna.py. Its comment
// documents how each table is derived from CPython.
//
//go:embed idna_tables.json.gz
var idnaBlob []byte

// idnaTableJSON is the on-disk form. A row of nfkd or b2 starts with the code
// point it applies to; the rest is the replacement.
type idnaTableJSON struct {
	Version             string   `json:"version"`
	CurrentVersion      string   `json:"current_version"`
	NFKD                [][]rune `json:"nfkd"`
	Combining           [][2]int `json:"combining"`
	Compose             [][3]int `json:"compose"`
	B1                  [][2]int `json:"b1"`
	B2                  [][]rune `json:"b2"`
	Prohibited          [][2]int `json:"prohibited"`
	D1                  [][2]int `json:"d1"`
	D2                  [][2]int `json:"d2"`
	PrintableProhibited []rune   `json:"printable_prohibited"`
}

// codePointRange is an inclusive range of code points of a stringprep table.
type codePointRange struct{ first, last rune }

// idnaTables is the loaded form of idna_tables.json.gz.
type idnaTables struct {
	unicodeVersion      string
	nfkd                map[rune][]rune
	combining           map[rune]int
	compose             map[[2]rune]rune
	b1                  []codePointRange
	b2                  map[rune][]rune
	prohibited          []codePointRange
	d1                  []codePointRange
	d2                  []codePointRange
	printableProhibited map[rune]bool
}

// version is the Unicode version of the normalization database (3.2.0).
func (t *idnaTables) version() string { return t.unicodeVersion }

var (
	idnaOnce   sync.Once
	idnaLoaded *idnaTables
)

// idnaTablesOf loads the embedded tables once.
func idnaTablesOf() *idnaTables {
	idnaOnce.Do(func() { idnaLoaded = loadIDNATables() })
	return idnaLoaded
}

func loadIDNATables() *idnaTables {
	reader, err := gzip.NewReader(bytes.NewReader(idnaBlob))
	if err != nil {
		panic("pycodecs: idna_tables.json.gz: " + err.Error())
	}
	plain, err := io.ReadAll(reader)
	if err != nil {
		panic("pycodecs: idna_tables.json.gz: " + err.Error())
	}
	var file idnaTableJSON
	if err := json.Unmarshal(plain, &file); err != nil {
		panic("pycodecs: idna_tables.json.gz: " + err.Error())
	}
	if file.Version != "3.2.0" {
		panic("pycodecs: idna_tables.json.gz: wrong Unicode version " + file.Version)
	}
	tables := &idnaTables{
		unicodeVersion:      file.Version,
		nfkd:                make(map[rune][]rune, len(file.NFKD)),
		combining:           make(map[rune]int, len(file.Combining)),
		compose:             make(map[[2]rune]rune, len(file.Compose)),
		b2:                  make(map[rune][]rune, len(file.B2)),
		printableProhibited: make(map[rune]bool, len(file.PrintableProhibited)),
	}
	for _, entry := range file.NFKD {
		if len(entry) < 2 {
			panic("pycodecs: idna_tables.json.gz: empty nfkd row")
		}
		tables.nfkd[entry[0]] = entry[1:]
	}
	for _, entry := range file.Combining {
		tables.combining[rune(entry[0])] = entry[1]
	}
	for _, entry := range file.Compose {
		tables.compose[[2]rune{rune(entry[0]), rune(entry[1])}] = rune(entry[2])
	}
	for _, entry := range file.B2 {
		if len(entry) < 2 {
			panic("pycodecs: idna_tables.json.gz: empty b2 row")
		}
		tables.b2[entry[0]] = entry[1:]
	}
	tables.b1 = runeRanges(file.B1)
	tables.prohibited = runeRanges(file.Prohibited)
	tables.d1 = runeRanges(file.D1)
	tables.d2 = runeRanges(file.D2)
	for _, cp := range file.PrintableProhibited {
		tables.printableProhibited[cp] = true
	}
	return tables
}

func runeRanges(pairs [][2]int) []codePointRange {
	out := make([]codePointRange, len(pairs))
	for i, pair := range pairs {
		out[i] = codePointRange{rune(pair[0]), rune(pair[1])}
	}
	return out
}

// in reports whether r is in one of the table's ranges (binary search).
func inTable(table []codePointRange, r rune) bool {
	low, high := 0, len(table)-1
	for low <= high {
		middle := (low + high) / 2
		switch {
		case r < table[middle].first:
			high = middle - 1
		case r > table[middle].last:
			low = middle + 1
		default:
			return true
		}
	}
	return false
}

// hangulCompose is the algorithmic Hangul composition: L+V, then LV+T.
func hangulCompose(first, second rune) (rune, bool) {
	if first >= 0x1100 && first <= 0x1112 && second >= 0x1161 && second <= 0x1175 {
		return 0xAC00 + ((first-0x1100)*21+(second-0x1161))*28, true
	}
	if first >= 0xAC00 && first <= 0xD7A3 && (first-0xAC00)%28 == 0 &&
		second >= 0x11A8 && second <= 0x11C2 {
		return first + second - 0x11A7, true
	}
	return 0, false
}

// idnaNFKC is unicodedata.ucd_3_2_0.normalize("NFKC", s) over []rune.
//
// The decomposition is the 3.2.0 NFKD table (full decompositions, so no
// recursion is needed). The canonical ordering uses the CURRENT combining
// classes: a stable insertion sort inside each run of non-starters. The
// composition uses the CURRENT pairs, Hangul algorithmically; it follows
// CPython's loop, which only compares with the last character that was NOT
// composed into the starter, so a mark composed into the starter stops
// blocking equal-class marks (U+00DC U+0304 is U+01D5).
func idnaNFKC(input []rune) []rune {
	tables := idnaTablesOf()
	decomposed := make([]rune, 0, len(input))
	for _, r := range input {
		if expansion, ok := tables.nfkd[r]; ok {
			decomposed = append(decomposed, expansion...)
		} else {
			decomposed = append(decomposed, r)
		}
	}
	// Canonical ordering.
	for i := 1; i < len(decomposed); i++ {
		class := tables.combining[decomposed[i]]
		if class == 0 {
			continue
		}
		for j := i; j > 0; j-- {
			previous := tables.combining[decomposed[j-1]]
			if previous == 0 || previous <= class {
				break
			}
			decomposed[j-1], decomposed[j] = decomposed[j], decomposed[j-1]
		}
	}
	// Canonical composition.
	out := make([]rune, 0, len(decomposed))
	starter := -1
	lastClass := 0
	for _, r := range decomposed {
		class := tables.combining[r]
		composed := false
		if starter >= 0 && (lastClass == 0 || lastClass < class) {
			if composite, ok := hangulCompose(out[starter], r); ok {
				out[starter] = composite
				composed = true
			} else if composite, ok := tables.compose[[2]rune{out[starter], r}]; ok {
				out[starter] = composite
				composed = true
			}
		}
		if !composed {
			out = append(out, r)
			if class == 0 {
				starter = len(out) - 1
			}
			lastClass = class
		}
	}
	return out
}

// nameprep is encodings.idna.nameprep over []rune. The error is CPython's
// UnicodeEncodeError: a span of the normalized label and its reason.
func nameprep(label []rune) ([]rune, *idnaError) {
	tables := idnaTablesOf()
	mapped := make([]rune, 0, len(label))
	for _, r := range label {
		if inTable(tables.b1, r) {
			continue
		}
		if replacement, ok := tables.b2[r]; ok {
			mapped = append(mapped, replacement...)
		} else {
			mapped = append(mapped, r)
		}
	}
	normalized := idnaNFKC(mapped)
	for i, r := range normalized {
		if inTable(tables.prohibited, r) {
			return nil, &idnaError{start: i, end: i + 1, reason: "Invalid character " + pythonCharRepr(tables, r)}
		}
	}
	randAL := make([]bool, len(normalized))
	any := false
	for i, r := range normalized {
		if inTable(tables.d1, r) {
			randAL[i] = true
			any = true
		}
	}
	if any {
		for i, r := range normalized {
			if inTable(tables.d2, r) {
				return nil, &idnaError{start: i, end: i + 1, reason: "Violation of BIDI requirement 2"}
			}
		}
		if !randAL[0] {
			return nil, &idnaError{start: 0, end: 1, reason: "Violation of BIDI requirement 3"}
		}
		if !randAL[len(normalized)-1] {
			return nil, &idnaError{
				start: len(normalized) - 1, end: len(normalized),
				reason: "Violation of BIDI requirement 3",
			}
		}
	}
	return normalized, nil
}

// pythonCharRepr is Python's repr of one character, as the "Invalid
// character {c!r}" reason formats it. The generator records the prohibited
// characters that repr leaves literal (the printable ones of the current
// Unicode database); every other prohibited character is escaped by value,
// as Python does for a non-printable character.
func pythonCharRepr(tables *idnaTables, r rune) string {
	if tables.printableProhibited[r] {
		return "'" + string(r) + "'"
	}
	switch {
	case r <= 0xFF:
		return fmt.Sprintf(`'\x%02x'`, r)
	case r <= 0xFFFF:
		return fmt.Sprintf(`'\u%04x'`, r)
	default:
		return fmt.Sprintf(`'\U%08x'`, r)
	}
}

// pythonBytesRepr is Python's repr of a bytes object, as the round-trip
// reason embeds it.
func pythonBytesRepr(data []byte) string {
	quote := byte('\'')
	if bytes.IndexByte(data, '\'') >= 0 && bytes.IndexByte(data, '"') < 0 {
		quote = '"'
	}
	var out strings.Builder
	out.WriteByte('b')
	out.WriteByte(quote)
	for _, b := range data {
		switch {
		case b == quote || b == '\\':
			out.WriteByte('\\')
			out.WriteByte(b)
		case b == '\t':
			out.WriteString(`\t`)
		case b == '\n':
			out.WriteString(`\n`)
		case b == '\r':
			out.WriteString(`\r`)
		case b >= 0x20 && b < 0x7F:
			out.WriteByte(b)
		default:
			fmt.Fprintf(&out, `\x%02x`, b)
		}
	}
	out.WriteByte(quote)
	return out.String()
}
