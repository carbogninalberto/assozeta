package smtpsend

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// The golden file is CPython's compat32 folding: testdata/python_header_fold.json
// comes from a script that folds each value with email.mime's as_string() (the
// send_email path, maxlinelen 0) or Header.encode(maxlinelen) directly, and
// records the folded line, or folded: null with the exception class when
// Python raises. A value with lone surrogates arrives as code points because a
// JSON string escape cannot carry them; a value too large to keep is a list of
// repeated parts and its folded line is recorded as a digest.

type headerFoldFile struct {
	Python  string           `json:"python"`
	Message []headerFoldCase `json:"message"`
	Header  []headerFoldCase `json:"header"`
	Chunks  []struct {
		Maxlinelen int               `json:"maxlinelen"`
		Chunks     []headerFoldChunk `json:"chunks"`
		Raised     *string           `json:"raised"`
		DigestFields
	} `json:"chunks"`
	Body []struct {
		Kind string `json:"kind"`
		headerFoldChunk
		Raised   *string `json:"raised"`
		Boundary string  `json:"boundary"`
		Message  *string `json:"message"`
	} `json:"body"`
}

// DigestFields are the fields of a folded line kept for a value too large to
// store.
type DigestFields struct {
	Folded *string `json:"folded"`
	// SHA256, Chars, Head and Tail describe Folded when it is not stored.
	SHA256 string `json:"folded_sha256"`
	Chars  int    `json:"folded_chars"`
	Head   string `json:"folded_head"`
	Tail   string `json:"folded_tail"`
}

type headerFoldChunk struct {
	Charset string  `json:"charset"`
	Value   *string `json:"value"`
	Runes   []int32 `json:"runes"`
	Parts   []struct {
		Unit  string `json:"unit"`
		Count int    `json:"count"`
	} `json:"parts"`
}

type headerFoldCase struct {
	Name       string `json:"name"`
	Maxlinelen int    `json:"maxlinelen"`
	headerFoldChunk
	Raised *string `json:"raised"`
	DigestFields
}

// text is the Go form of the Python str the case folds.
func (c headerFoldChunk) text(t *testing.T) string {
	t.Helper()
	switch {
	case c.Value != nil:
		return *c.Value
	case c.Runes != nil:
		runes := make([]rune, len(c.Runes))
		for i, r := range c.Runes {
			runes[i] = rune(r)
		}
		return pystr.String(runes)
	}
	var out strings.Builder
	for _, part := range c.Parts {
		out.WriteString(strings.Repeat(part.Unit, part.Count))
	}
	return out.String()
}

func (c headerFoldChunk) charset(t *testing.T) headerCharset {
	t.Helper()
	switch c.Charset {
	case "utf-8":
		return charsetUTF8
	case "unknown-8bit":
		return charsetUnknown8bit
	case "us-ascii", "":
		return charsetUSASCII
	}
	t.Fatalf("unknown charset %q", c.Charset)
	return charsetUSASCII
}

func loadHeaderFold(t *testing.T) headerFoldFile {
	t.Helper()
	raw, err := os.ReadFile("testdata/python_header_fold.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden headerFoldFile
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if golden.Python == "" || len(golden.Message) < 30 || len(golden.Header) < 80 || len(golden.Chunks) < 10 {
		t.Fatal("incomplete golden file")
	}
	return golden
}

// checkFold compares a folded value with the golden line (which keeps the
// "Name: " prefix for the message cases) and reports a mismatch.
func checkFold(t *testing.T, label string, item DigestFields, folded string) {
	t.Helper()
	if item.Folded != nil {
		if folded != *item.Folded {
			t.Fatalf("%s: go %q python %q", label, folded, *item.Folded)
		}
		return
	}
	sum := sha256.Sum256([]byte(folded))
	if got := hex.EncodeToString(sum[:]); got != item.SHA256 {
		t.Fatalf("%s: digest differs (go %d chars, python %d)\ngo:     %q...\npython: %q...",
			label, len(folded), item.Chars, folded[:min(60, len(folded))], item.Head)
	}
	if len(folded) != item.Chars || !strings.HasPrefix(folded, item.Head) || !strings.HasSuffix(folded, item.Tail) {
		t.Fatalf("%s: head, tail or length differs (go %d chars, python %d)", label, len(folded), item.Chars)
	}
}

func checkRaised(t *testing.T, label string, item DigestFields, raised *string, err error) bool {
	t.Helper()
	if raised == nil {
		if err != nil {
			t.Fatalf("%s: %v", label, err)
		}
		if item.Folded == nil && item.SHA256 == "" {
			t.Fatalf("%s: golden has no folded value", label)
		}
		return true
	}
	if !errors.Is(err, ErrRaises) {
		t.Fatalf("%s: Python raised %s, Go %v", label, *raised, err)
	}
	return false
}

// TestMessageHeaderFoldMatchesPython replays the send_email header values
// (subject, From display name, To, Reply-To, lone surrogates included) against
// msg.as_string()'s line.
func TestMessageHeaderFoldMatchesPython(t *testing.T) {
	golden := loadHeaderFold(t)
	raised, folded := 0, 0
	for _, item := range golden.Message {
		value := item.text(t)
		text, err := foldHeader(item.Name, value)
		label := item.Name + " " + shortValue(value)
		if !checkRaised(t, label, item.DigestFields, item.Raised, err) {
			raised++
			continue
		}
		checkFold(t, label, item.DigestFields, item.Name+": "+text+"\n")
		folded++
	}
	if raised < 5 || folded < 25 {
		t.Fatalf("coverage: folded %d raised %d", folded, raised)
	}
}

// TestHeaderEncodeMatchesPython replays Header.encode(maxlinelen) for the
// Django folding widths (76 and 78) and for maxlinelen 0, where the folding
// only shows on values a million characters long.
func TestHeaderEncodeMatchesPython(t *testing.T) {
	golden := loadHeaderFold(t)
	raised, folded := 0, 0
	for _, item := range golden.Header {
		value := item.text(t)
		text, err := foldHeaderLine(item.Name, value, item.Maxlinelen)
		label := item.Name + " " + shortValue(value)
		if !checkRaised(t, label, item.DigestFields, item.Raised, err) {
			raised++
			continue
		}
		checkFold(t, label, item.DigestFields, text)
		folded++
	}
	if raised < 2 || folded < 80 {
		t.Fatalf("coverage: folded %d raised %d", folded, raised)
	}
}

// TestHeaderEncodeChunksMatchesPython replays Header.append with explicit
// charsets, so headerEncode's chunk transitions are covered too.
func TestHeaderEncodeChunksMatchesPython(t *testing.T) {
	golden := loadHeaderFold(t)
	for _, item := range golden.Chunks {
		chunks := make([]headerChunk, 0, len(item.Chunks))
		for _, chunk := range item.Chunks {
			chunks = append(chunks, headerChunk{text: chunk.text(t), charset: chunk.charset(t)})
		}
		text, err := headerEncode(chunks, item.Maxlinelen, "Subject", " ")
		label := "chunks"
		if !checkRaised(t, label, item.DigestFields, item.Raised, err) {
			continue
		}
		checkFold(t, label, item.DigestFields, text)
	}
}

// TestRenderBodyMatchesPython replays the whole message for text and html
// bodies, with the boundary pinned: msg.as_string() as MIMEText builds the
// part, including the surrogateescape bytes and the UnicodeEncodeError of a
// surrogate outside U+DC80..U+DCFF.
func TestRenderBodyMatchesPython(t *testing.T) {
	golden := loadHeaderFold(t)
	if len(golden.Body) < 8 {
		t.Fatal("incomplete golden file")
	}
	for _, item := range golden.Body {
		body := item.text(t)
		message := Message{SenderName: "Associazione", SenderUser: "sender@example.test", To: []string{"a@example.test"},
			Subject: "Bakney Sport"}
		if item.Kind == "html" {
			message.HTML = body
		} else {
			message.Text = body
		}
		rendered, err := Render(message, item.Boundary)
		if item.Message == nil {
			if !errors.Is(err, ErrRaises) {
				t.Fatalf("%s %q: Python raised %s, Go %v", item.Kind, shortValue(body), *item.Raised, err)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s %q: %v", item.Kind, shortValue(body), err)
		}
		if rendered != *item.Message {
			t.Fatalf("%s %q: render differs\ngo:     %q\npython: %q", item.Kind, shortValue(body), rendered, *item.Message)
		}
	}
}

// shortValue clips a value for a test message: the big cases are a million
// characters.
func shortValue(value string) string {
	if runes := []rune(value); len(runes) > 60 {
		return string(runes[:60]) + "..."
	}
	return value
}
