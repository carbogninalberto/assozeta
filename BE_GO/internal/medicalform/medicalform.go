// Package medicalform fills the PDF forms of the regional medical appointment
// documents the way docmanager.views.utils.fill_pdf_fields does when no image
// is inserted: pypdf reads the template and writes every page back with the
// form values, so the bytes depend on the values only through the /V string
// of each field. The skeleton is that output with a marker in every field
// (TESTS/fixtures/generate_medical_form_skeleton.py); Fill puts the values in
// as pypdf's TextStringObject writes them and moves the xref offsets.
package medicalform

import (
	"bytes"
	_ "embed"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf16"
)

//go:embed piemonte_biella.skeleton
var biellaSkeleton []byte

//go:embed piemonte_biella.fields
var biellaFields string

//go:embed pdfdoc.table
var pdfdocTable string

// SignatureKey is the field whose image fill_pdf_fields draws on the page.
const SignatureKey = "sport_association.president_signature"

type form struct {
	keys []string
	// parts[i] precedes slot i; the last part follows the last slot and ends before the xref table.
	parts [][]byte
	slots []int
	// offsets are the xref entries of the skeleton; trailer is the text between the table and startxref.
	offsets []xrefEntry
	header  []byte
	trailer []byte
	xref    int
}

type xrefEntry struct {
	offset     int
	generation string
	kind       byte
}

var (
	biella     form
	pdfdoc     = map[rune]byte{}
	marker     = regexp.MustCompile(`\(QQF(\d\d)QQ\)`)
	xrefHeader = regexp.MustCompile(`^xref\n(\d+) (\d+)\n`)
	errData    = errors.New("medicalform: generated data is not readable")
)

func init() {
	for _, line := range strings.Split(strings.TrimSuffix(pdfdocTable, "\n"), "\n") {
		point, value, found := strings.Cut(line, " ")
		r, errPoint := strconv.Atoi(point)
		b, errValue := strconv.Atoi(value)
		if !found || errPoint != nil || errValue != nil || b < 0 || b > 255 {
			panic(errData)
		}
		pdfdoc[rune(r)] = byte(b)
	}
	parsed, err := parse(biellaSkeleton, strings.Split(strings.TrimSuffix(biellaFields, "\n"), "\n"))
	if err != nil {
		panic(err)
	}
	biella = *parsed
}

func parse(skeleton []byte, keys []string) (*form, error) {
	start := bytes.LastIndex(skeleton, []byte("\nstartxref\n"))
	table := bytes.LastIndex(skeleton, []byte("\nxref\n"))
	if start < 0 || table < 0 || table > start {
		return nil, errData
	}
	table++
	declared, err := strconv.Atoi(strings.TrimSuffix(string(skeleton[start+len("\nstartxref\n"):]), "\n%%EOF\n"))
	if err != nil || declared != table {
		return nil, errData
	}
	head := xrefHeader.FindSubmatch(skeleton[table:])
	if head == nil || string(head[1]) != "0" {
		return nil, errData
	}
	count, _ := strconv.Atoi(string(head[2]))
	entries := skeleton[table+len(head[0]):]
	if len(entries) < count*20 {
		return nil, errData
	}
	result := &form{keys: keys, header: head[0], xref: table, trailer: skeleton[table+len(head[0])+count*20 : start+1]}
	for i := 0; i < count; i++ {
		line := entries[i*20 : i*20+20]
		offset, err := strconv.Atoi(string(line[:10]))
		if err != nil || line[10] != ' ' || line[16] != ' ' || string(line[18:]) != " \n" {
			return nil, errData
		}
		result.offsets = append(result.offsets, xrefEntry{offset: offset, generation: string(line[11:16]), kind: line[17]})
	}
	body, last := skeleton[:table], 0
	for _, match := range marker.FindAllSubmatchIndex(body, -1) {
		slot, _ := strconv.Atoi(string(body[match[2]:match[3]]))
		if slot >= len(keys) {
			return nil, errData
		}
		result.parts = append(result.parts, body[last:match[0]])
		result.slots = append(result.slots, slot)
		last = match[1]
	}
	result.parts = append(result.parts, body[last:])
	if len(result.slots) == 0 || bytes.Contains(body, []byte("QQF")) != (len(result.slots) > 0) {
		return nil, errData
	}
	return result, nil
}

// BiellaKeys are the keys of the form's fields ('-' in the field names read
// as '.'), in the order pypdf lists them.
func BiellaKeys() []string { return append([]string(nil), biella.keys...) }

// FillBiella is fill_pdf_fields(template, values) for the Piemonte Biella
// form when no image is inserted: a field without a value is written empty.
func FillBiella(values map[string]string) []byte { return biella.fill(values) }

func (f *form) fill(values map[string]string) []byte {
	var out bytes.Buffer
	out.Grow(f.xref + 4096)
	// starts[i] is where slot i begins in the skeleton; shift[i] the bytes added up to and including it.
	type moved struct{ end, delta int }
	shifts := make([]moved, 0, len(f.slots))
	position, delta := 0, 0
	for i, slot := range f.slots {
		out.Write(f.parts[i])
		position += len(f.parts[i])
		text := textString(values[f.keys[slot]])
		out.Write(text)
		position += len("(QQF00QQ)")
		delta += len(text) - len("(QQF00QQ)")
		shifts = append(shifts, moved{end: position, delta: delta})
	}
	out.Write(f.parts[len(f.parts)-1])
	at := func(offset int) int {
		added := 0
		for _, shift := range shifts {
			if shift.end > offset {
				break
			}
			added = shift.delta
		}
		return offset + added
	}
	table := out.Len()
	out.Write(f.header)
	for _, entry := range f.offsets {
		offset := entry.offset
		if entry.kind == 'n' {
			offset = at(offset)
		}
		fmt.Fprintf(&out, "%010d %s %c \n", offset, entry.generation, entry.kind)
	}
	out.Write(f.trailer)
	out.WriteString("startxref\n" + strconv.Itoa(table) + "\n%%EOF\n")
	return out.Bytes()
}

// textString is TextStringObject(value).write_to_stream: PDFDocEncoding when
// every character has a byte, else UTF-16BE with its BOM; every byte that is
// not an ASCII letter, digit or space is written as a three-digit octal escape.
func textString(value string) []byte {
	encoded := make([]byte, 0, len(value))
	plain := true
	for _, r := range value {
		b, known := pdfdoc[r]
		if !known {
			plain = false
			break
		}
		encoded = append(encoded, b)
	}
	if !plain {
		encoded = append(encoded[:0], 0xfe, 0xff)
		for _, unit := range utf16.Encode([]rune(value)) {
			encoded = append(encoded, byte(unit>>8), byte(unit))
		}
	}
	out := make([]byte, 0, len(encoded)+2)
	out = append(out, '(')
	for _, b := range encoded {
		if b == ' ' || b >= '0' && b <= '9' || b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' {
			out = append(out, b)
		} else {
			out = append(out, '\\', '0'+b>>6, '0'+b>>3&7, '0'+b&7)
		}
	}
	return append(out, ')')
}
