package smtpsend

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"
)

// TestDjangoTextGolden replays testdata/django_text.json
// (TESTS/adapters/go/instance_outbound_golden.py): Django's EmailMessage bytes
// and compat32 header folds.
func TestDjangoTextGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/django_text.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Messages []struct {
			Name, SenderName, FromEmail, To, MessageID, Subject, Bytes string
		} `json:"messages"`
		Folds []struct{ Header, Value, Folded string } `json:"folds"`
	}
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	var rawMessages []map[string]string
	var wrapper struct {
		Messages []map[string]string `json:"messages"`
	}
	_ = json.Unmarshal(raw, &wrapper)
	rawMessages = wrapper.Messages
	date := time.Date(2026, 11, 9, 8, 43, 54, 0, time.UTC)
	for _, m := range rawMessages {
		got, err := RenderDjangoText(DjangoText{EncodedSubject: m["subject"],
			Body:       "Questo messaggio è stato richiesto dal proprietario dell’istanza per verificare la configurazione email.",
			SenderName: m["sender_name"], FromEmail: m["from_email"], To: m["to"], Date: date, MessageID: m["message_id"]})
		if err != nil {
			t.Fatalf("%s: %v", m["name"], err)
		}
		// The golden bytes are latin-1 decoded.
		var want strings.Builder
		for _, r := range m["bytes"] {
			want.WriteByte(byte(r))
		}
		if string(got) != want.String() {
			t.Errorf("%s:\n got %q\nwant %q", m["name"], got, want.String())
		}
	}
	if len(rawMessages) < 20 || len(golden.Folds) < 10 {
		t.Fatalf("unexpected golden file")
	}
	for _, f := range golden.Folds {
		if got := FoldASCII(len(f.Header)+2, f.Value, 78, "\r\n"); got != f.Folded {
			t.Errorf("fold %s %q:\n got %q\nwant %q", f.Header, f.Value, got, f.Folded)
		}
	}
}
