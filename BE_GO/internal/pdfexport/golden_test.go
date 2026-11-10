package pdfexport

import (
	"bytes"
	"compress/zlib"
	"encoding/ascii85"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"
)

// The golden documents come from reportlab through the contract's own
// builders (TESTS/fixtures/generate_pdf_golden.py), in the decoded form of
// the opt-in PDF standard (TESTS/contracts/pdf.py normalize): a case reportlab
// raises on must fail here with ErrLayout.

type goldenCase struct {
	Name     string          `json:"name"`
	Spec     json.RawMessage `json:"spec"`
	Raises   string          `json:"raises"`
	Document string          `json:"document"`
}

var (
	testHeader   = "%PDF-1.4\n%\x93\x8c\x8b\x9e ReportLab Generated PDF document http://www.reportlab.com\n"
	testObject   = regexp.MustCompile(`^(\d+) 0 obj\n`)
	testLength   = regexp.MustCompile(`/Length (\d+)([ \n])`)
	testFilter   = regexp.MustCompile(`/Filter \[ ([A-Za-z0-9/ ]+) \]`)
	testDate     = regexp.MustCompile(`\(D:(\d{14}[+-]\d\d'\d\d')\)`)
	testTrailer  = regexp.MustCompile(`^trailer\n<<\n/ID \n\[<([0-9a-f]{32})><([0-9a-f]{32})>\]\n% ReportLab generated PDF document -- digest \(http://www\.reportlab\.com\)\n\n/Info (\d+) 0 R\n/Root (\d+) 0 R\n/Size (\d+)\n>>\nstartxref\n(\d+)\n%%EOF\n$`)
	testSeparate = "\n--part--\n"
	testImage    = regexp.MustCompile(`/FormXob\.[0-9a-f]{32}`)
)

// normalizeDocument is TESTS/contracts/pdf.py normalize: the decoded parts
// joined, the date text and the digest.
func normalizeDocument(data []byte) (string, string, string, error) {
	text := string(data)
	if !strings.HasPrefix(text, testHeader) {
		return "", "", "", errors.New("header")
	}
	parts := []string{testHeader}
	position := len(testHeader)
	var offsets []int
	stamp := ""
	for {
		match := testObject.FindStringSubmatch(text[position:])
		if match == nil {
			break
		}
		if n, _ := strconv.Atoi(match[1]); n != len(offsets)+1 {
			return "", "", "", errors.New("object out of order")
		}
		offsets = append(offsets, position)
		start := position + len(match[0])
		marker := strings.Index(text[start:], ">>\nstream\n")
		end := strings.Index(text[start:], "endobj\n")
		if end < 0 {
			return "", "", "", errors.New("object without endobj")
		}
		var body string
		if marker >= 0 && marker < end {
			dictionary := text[start : start+marker+3]
			length := testLength.FindStringSubmatch(dictionary)
			filters := testFilter.FindStringSubmatch(dictionary)
			if length == nil || filters == nil {
				return "", "", "", errors.New("stream without /Length or /Filter")
			}
			n, _ := strconv.Atoi(length[1])
			from := start + marker + 10
			if from+n+17 > len(text) || text[from+n:from+n+17] != "endstream\nendobj\n" {
				return "", "", "", errors.New("stream /Length does not end at endstream")
			}
			raw := text[from : from+n]
			names := strings.Fields(filters[1])
			if names[0] != "/ASCII85Decode" || !strings.HasSuffix(raw, "~>") {
				return "", "", "", errors.New("stream filters")
			}
			decoded, err := io.ReadAll(ascii85.NewDecoder(strings.NewReader(strings.TrimSuffix(raw, "~>"))))
			if err != nil {
				return "", "", "", err
			}
			for _, name := range names[1:] {
				switch name {
				case "/FlateDecode":
					reader, err := zlib.NewReader(bytes.NewReader(decoded))
					if err != nil {
						return "", "", "", err
					}
					if decoded, err = io.ReadAll(reader); err != nil {
						return "", "", "", err
					}
				case "/DCTDecode":
				default:
					return "", "", "", errors.New("stream filter " + name)
				}
			}
			body = testLength.ReplaceAllString(dictionary, "/Length -$2") + "stream\n" + string(decoded) + "endstream\n"
			position = from + n + 17
		} else {
			body = text[start : start+end]
			position = start + end + 7
			if dates := testDate.FindAllStringSubmatch(body, -1); dates != nil {
				if len(dates) != 2 || dates[0][1] != dates[1][1] || stamp != "" {
					return "", "", "", errors.New("dates")
				}
				stamp = dates[0][1]
				body = strings.ReplaceAll(body, stamp, "-")
			}
		}
		parts = append(parts, match[0]+body)
	}
	if len(offsets) == 0 || stamp == "" {
		return "", "", "", errors.New("no objects or no dates")
	}
	table := fmt.Sprintf("xref\n0 %d\n0000000000 65535 f \n", len(offsets)+1)
	for _, offset := range offsets {
		table += fmt.Sprintf("%010d 00000 n \n", offset)
	}
	if !strings.HasPrefix(text[position:], table) {
		return "", "", "", errors.New("cross-reference table does not list the objects at their offsets")
	}
	trailer := testTrailer.FindStringSubmatch(text[position+len(table):])
	if trailer == nil {
		return "", "", "", errors.New("trailer")
	}
	if trailer[1] != trailer[2] || trailer[5] != strconv.Itoa(len(offsets)+1) || trailer[6] != strconv.Itoa(position) {
		return "", "", "", errors.New("trailer /ID, /Size or startxref")
	}
	parts = append(parts, "/Info "+trailer[3]+" /Root "+trailer[4])
	joined := strings.Join(parts, testSeparate)
	names := map[string]string{}
	joined = testImage.ReplaceAllStringFunc(joined, func(name string) string {
		if _, ok := names[name]; !ok {
			names[name] = "/FormXob.#" + strconv.Itoa(len(names))
		}
		return names[name]
	})
	return joined, stamp, trailer[1], nil
}

// pyRepr is repr() of a decoded typed value (inside a list or dict).
func pyRepr(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	cell := decodeCell(t, raw)
	if cell.None {
		return "None"
	}
	var text string
	if json.Unmarshal(raw, &text) == nil {
		return "'" + text + "'"
	}
	return cell.Text
}

func decodeCell(t *testing.T, raw json.RawMessage) Cell {
	t.Helper()
	if string(raw) == "null" {
		return Cell{None: true}
	}
	var text string
	if json.Unmarshal(raw, &text) == nil {
		return Cell{Text: text}
	}
	var tagged map[string]json.RawMessage
	if err := json.Unmarshal(raw, &tagged); err != nil || len(tagged) != 1 {
		t.Fatalf("bad value %s", raw)
	}
	for kind, value := range tagged {
		switch kind {
		case "int", "float", "decimal":
			_ = json.Unmarshal(value, &text)
			return Cell{Text: text}
		case "bool":
			if string(value) == "true" {
				return Cell{Text: "True"}
			}
			return Cell{Text: "False"}
		case "list":
			var items []json.RawMessage
			_ = json.Unmarshal(value, &items)
			parts := make([]string, len(items))
			empty := true
			for i, item := range items {
				parts[i] = pyRepr(t, item)
				if inner := decodeCell(t, item); !inner.Sequence || !inner.Empty {
					empty = false
				}
			}
			return Cell{Text: "[" + strings.Join(parts, ", ") + "]", Sequence: true, Empty: empty}
		case "dict":
			var pairs [][2]json.RawMessage
			_ = json.Unmarshal(value, &pairs)
			parts := make([]string, len(pairs))
			for i, pair := range pairs {
				parts[i] = pyRepr(t, pair[0]) + ": " + pyRepr(t, pair[1])
			}
			return Cell{Text: "{" + strings.Join(parts, ", ") + "}"}
		}
	}
	t.Fatalf("bad value %s", raw)
	return Cell{}
}

type dataTableSpec struct {
	Engine     string              `json:"engine"`
	Columns    []string            `json:"columns"`
	Rows       [][]json.RawMessage `json:"rows"`
	Title      string              `json:"title"`
	Align      []string            `json:"align"`
	Header     *string             `json:"header"`
	IsA3       bool                `json:"is_a3"`
	Landscape  *bool               `json:"landscape"`
	Additional [][2]string         `json:"additional"`
}

func buildGolden(t *testing.T, raw json.RawMessage, now time.Time) ([]byte, error) {
	t.Helper()
	var spec dataTableSpec
	if err := json.Unmarshal(raw, &spec); err != nil {
		t.Fatal(err)
	}
	switch spec.Engine {
	case "data_table":
		input := DataTable{Columns: spec.Columns, Title: spec.Title, Align: spec.Align, Header: spec.Header, IsA3: spec.IsA3,
			Portrait: spec.Landscape != nil && !*spec.Landscape}
		for _, row := range spec.Rows {
			cells := make([]Cell, len(row))
			for i, value := range row {
				cells[i] = decodeCell(t, value)
			}
			input.Rows = append(input.Rows, cells)
		}
		for _, pair := range spec.Additional {
			input.Additional = append(input.Additional, pair[1])
		}
		return input.Build(now)
	}
	return buildGoldenOther(t, spec.Engine, raw, now)
}

func TestGoldenDocuments(t *testing.T) {
	raw, err := os.ReadFile("testdata/golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Cases []goldenCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatal(err)
	}
	if len(file.Cases) == 0 {
		t.Fatal("no golden cases")
	}
	now := time.Date(2026, 11, 10, 9, 8, 7, 123456000, time.UTC)
	for _, item := range file.Cases {
		t.Run(item.Name, func(t *testing.T) {
			got, err := buildGolden(t, item.Spec, now)
			if item.Raises != "" {
				if !errors.Is(err, ErrLayout) {
					t.Fatalf("reportlab raises %s; got %v", item.Raises, err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			want, err := base64.StdEncoding.DecodeString(item.Document)
			if err != nil || len(want) == 0 {
				t.Fatalf("golden document: %v", err)
			}
			normal, stamp, _, err := normalizeDocument(got)
			if err != nil {
				t.Fatalf("document: %v", err)
			}
			if stamp != "20261110090807+00'00'" {
				t.Fatalf("date %s", stamp)
			}
			if normal != string(want) {
				index := 0
				for index < len(normal) && index < len(want) && normal[index] == want[index] {
					index++
				}
				low := max(0, index-200)
				t.Fatalf("differs at byte %d:\nwant %q\n got %q", index, want[low:min(len(want), index+120)], normal[low:min(len(normal), index+120)])
			}
		})
	}
}

// The trailer digest is md5 of 'a reportlab document', ascii(time.time()) and
// the default Info strings: reportlab's invariant document (time 946684800.0)
// carries 93f779ecd1f2924a75b2cd56e4383cfa.
func TestDigestAndDates(t *testing.T) {
	got, err := DataTable{Columns: []string{"A"}, Rows: [][]Cell{{{Text: "a"}}}, Title: "T"}.Build(time.Unix(946684800, 0))
	if err != nil {
		t.Fatal(err)
	}
	_, stamp, digest, err := normalizeDocument(got)
	if err != nil {
		t.Fatal(err)
	}
	if digest != "93f779ecd1f2924a75b2cd56e4383cfa" || stamp != "20000101000000+00'00'" {
		t.Fatalf("digest %s, date %s", digest, stamp)
	}
	if text := pythonTimeRepr(time.Unix(1794322564, 90204000)); text != "1794322564.090204" {
		t.Fatalf("time repr %s", text)
	}
}

func TestNormalizeRejectsBrokenDocuments(t *testing.T) {
	good, err := DataTable{Columns: []string{"A"}, Rows: [][]Cell{{{Text: "a"}}}, Title: "T"}.Build(time.Unix(946684800, 0))
	if err != nil {
		t.Fatal(err)
	}
	for name, broken := range map[string][]byte{
		"offset":   bytes.Replace(good, []byte("0000000073 00000 n"), []byte("0000000074 00000 n"), 1),
		"digest":   bytes.Replace(good, []byte("<93f779ecd1f2924a75b2cd56e4383cfa>]"), []byte("<93f779ecd1f2924a75b2cd56e4383cfb>]"), 1),
		"moddate":  bytes.Replace(good, []byte("/ModDate (D:2000"), []byte("/ModDate (D:2001"), 1),
		"length":   bytes.Replace(good, []byte("/Length "), []byte("/Length 1"), 1),
		"truncate": good[:len(good)-1],
	} {
		if bytes.Equal(broken, good) {
			t.Fatalf("%s: the document was not changed", name)
		}
		if _, _, _, err := normalizeDocument(broken); err == nil {
			t.Fatalf("%s: accepted", name)
		}
	}
}
