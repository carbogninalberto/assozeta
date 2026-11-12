package dto

import (
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestMessageMutationsSerializerContract(t *testing.T) {
	for _, tc := range []struct {
		name    string
		body    string
		valid   bool
		message string
		subject *string
	}{
		{"preserve whitespace", `{"type":"EMAIL","message":" \n <b>x</b> \t","subject":"  &  "}`, true, " \n <b>x</b> \t", messageMutationString("  &  ")},
		{"whitespace body valid", `{"type":"INSIDE_APP","message":" \t\n"}`, true, " \t\n", nil},
		{"omitted subject", `{"type":"EMAIL","message":"x"}`, true, "x", nil},
		{"null subject", `{"type":"EMAIL","message":"x","subject":null}`, true, "x", nil},
		{"blank subject", `{"type":"EMAIL","message":"x","subject":""}`, true, "x", messageMutationString("")},
		{"numeric coercion", `{"type":"EMAIL","message":12,"subject":1.0}`, true, "12", messageMutationString("1.0")},
		{"unknown fields ignored", `{"type":"EMAIL","message":"x","sport_association":false,"message_id":"bad","created_at":null}`, true, "x", nil},
		{"message missing", `{"type":"EMAIL"}`, false, "", nil},
		{"message empty", `{"type":"EMAIL","message":""}`, false, "", nil},
		{"message null", `{"type":"EMAIL","message":null}`, false, "", nil},
		{"message bool", `{"type":"EMAIL","message":true}`, false, "", nil},
		{"message object", `{"type":"EMAIL","message":{}}`, false, "", nil},
		{"subject list", `{"type":"EMAIL","message":"x","subject":[]}`, false, "", nil},
		{"type missing", `{"message":"x"}`, false, "", nil},
		{"type whitespace", `{"type":" EMAIL ","message":"x"}`, false, "", nil},
		{"retired type", `{"type":"SMS","message":"x"}`, false, "", nil},
		{"type null", `{"type":null,"message":"x"}`, false, "", nil},
		{"null char", `{"type":"EMAIL","message":"\u0000"}`, false, "", nil},
		{"surrogate", `{"type":"EMAIL","message":"\ud800"}`, false, "", nil},
		{"nonobject", `[]`, false, "", nil},
		{"null body", `null`, false, "", nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body, err := ParseProfileTablesJSON([]byte(tc.body))
			if err != nil {
				t.Fatal(err)
			}
			got, err := ParseMessageMutationWrite(body)
			if (err == nil) != tc.valid {
				t.Fatalf("valid=%v error=%v", tc.valid, err)
			}
			if !tc.valid {
				return
			}
			if got.Message != tc.message || (got.Subject == nil) != (tc.subject == nil) {
				t.Fatalf("got %#v", got)
			}
			if tc.subject != nil && *got.Subject != *tc.subject {
				t.Fatalf("subject=%q", *got.Subject)
			}
		})
	}
	for _, n := range []int{255, 256} {
		body := domain.RequestObject{{Name: "type", Value: "EMAIL"}, {Name: "message", Value: "x"}, {Name: "subject", Value: strings.Repeat("😀", n)}}
		_, err := ParseMessageMutationWrite(body)
		if (err == nil) != (n == 255) {
			t.Fatalf("unicode subject length %d: %v", n, err)
		}
	}
	// An unbounded TextField must not inherit the subject's 255-character limit.
	_, err := ParseMessageMutationWrite(domain.RequestObject{{Name: "type", Value: "EMAIL"}, {Name: "message", Value: strings.Repeat("x", 10000)}})
	if err != nil {
		t.Fatal(err)
	}
}
func messageMutationString(s string) *string { return &s }
