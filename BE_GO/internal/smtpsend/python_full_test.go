package smtpsend

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
)

// TestRenderAnyMatchesPython replays every send_email sample of the golden
// file, line breaks included: the same text, or ErrHeader where Python raised.
func TestRenderAnyMatchesPython(t *testing.T) {
	golden := loadGolden(t)
	rendered, raised := 0, 0
	for i, item := range golden.MIME {
		input := item.Input
		if (input.HTML == nil || *input.HTML == "") && input.Text == nil {
			continue
		}
		message := Message{SenderUser: input.User, To: input.To, Subject: input.Subject, ReplyTo: input.Reply}
		if input.Name != nil {
			message.SenderName = *input.Name
		}
		if input.HTML != nil {
			message.HTML = *input.HTML
		}
		if input.Text != nil {
			message.Text = *input.Text
		}
		boundary := NewBoundary()
		if item.Message != nil {
			boundary = boundaryPattern.FindStringSubmatch(*item.Message)[1]
		}
		text, err := RenderAny(message, boundary)
		switch {
		case item.Message == nil:
			if !errors.Is(err, ErrHeader) {
				t.Fatalf("%d: Python raised, Go %v %q", i, err, text)
			}
			raised++
		case err != nil:
			t.Fatalf("%d: %v", i, err)
		case text != *item.Message:
			t.Fatalf("%d: render differs\ngo:     %q\npython: %q", i, text, *item.Message)
		default:
			rendered++
		}
	}
	if rendered < 200 {
		t.Fatalf("coverage: rendered %d raised %d", rendered, raised)
	}
}

func TestQuoteAddrMatchesPython(t *testing.T) {
	golden := loadGolden(t)
	for _, item := range golden.Addresses {
		if got := QuoteAddr(item.Address); got != item.Quoted {
			t.Fatalf("%q: go %q python %q", item.Address, got, item.Quoted)
		}
	}
}

type fullGolden struct {
	Headers []struct {
		Name   string  `json:"name"`
		Value  string  `json:"value"`
		Folded *string `json:"folded"`
	} `json:"headers"`
	Addresses []struct {
		Address string `json:"address"`
		Quoted  string `json:"quoted"`
	} `json:"addresses"`
	Encode []struct {
		Text  string `json:"text"`
		Error string `json:"error"`
		Repr  string `json:"repr"`
	} `json:"encode"`
}

// TestPythonFullGolden replays testdata/python_full.json
// (TESTS/adapters/go/smtpsend_full_golden.py): header folds with line breaks,
// smtplib.quoteaddr of addresses with quoted parts, comments, routes and
// groups, and the texts of str.encode('ascii') failures and repr().
func TestPythonFullGolden(t *testing.T) {
	path := "testdata/python_full.json"
	if extra := os.Getenv("SMTPSEND_FULL_GOLDEN"); extra != "" {
		path = extra
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var golden fullGolden
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Headers) < 500 || len(golden.Addresses) < 500 || len(golden.Encode) < 100 {
		t.Fatal("incomplete golden file")
	}
	raised := 0
	for _, item := range golden.Headers {
		got, err := foldHeader(item.Name, item.Value)
		if item.Folded == nil {
			if !errors.Is(err, ErrHeader) {
				t.Fatalf("%q: Python raised, Go %v %q", item.Value, err, got)
			}
			raised++
			continue
		}
		if err != nil || item.Name+": "+got+"\n" != *item.Folded {
			t.Fatalf("%q: go %q (%v) python %q", item.Value, got, err, *item.Folded)
		}
	}
	if raised < 20 || raised > len(golden.Headers)-100 {
		t.Fatalf("coverage: %d raised of %d", raised, len(golden.Headers))
	}
	for _, item := range golden.Addresses {
		if got := QuoteAddr(item.Address); got != item.Quoted {
			t.Fatalf("%q: go %q python %q", item.Address, got, item.Quoted)
		}
	}
	for _, item := range golden.Encode {
		got := ""
		if err := asciiEncodeFailure(item.Text); err != nil {
			got = err.(*Error).Text
		}
		if got != item.Error {
			t.Fatalf("%q: go %q python %q", item.Text, got, item.Error)
		}
		if repr, ok := PyStrRepr(item.Text); ok && repr != item.Repr {
			t.Fatalf("repr %q: go %q python %q", item.Text, repr, item.Repr)
		}
	}
}
