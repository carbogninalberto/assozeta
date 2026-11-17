package service

import (
	"reflect"
	"testing"
)

// Legacy backups can hold an unassigned anagrafica sharing a member's tax code;
// the setup import moves every Associate into the new association, so it clears
// the legacy copy's code like the data restore instead of failing on
// unique_tax_code_sport_assoc_upper_active.
func TestConsumerImportTaxConflicts(t *testing.T) {
	const association = "c8000000-0000-4000-8000-00000000000b"
	const low, high = "00000000-0000-4000-8000-000000000001", "ffffffff-ffff-4fff-8fff-ffffffffffff"
	cases := []struct {
		name string
		rows string
		want map[string]string
		err  string
	}{{
		name: "unassigned legacy copies of members lose their code",
		rows: `[{"associate_id":"m1","sport_association_id":"` + association + `","tax_code":"BNCGNN85M01H501Z","deleted":false},
			{"associate_id":"l1","sport_association_id":null,"tax_code":"BNCGNN85M01H501Z","deleted":false},
			{"associate_id":"l2","sport_association_id":null,"tax_code":"vrdmrc70c15f205k"},
			{"associate_id":"m2","sport_association_id":"` + association + `","tax_code":"VRDMRC70C15F205K"}]`,
		want: map[string]string{"l1": "m1", "l2": "m2"},
	}, {
		name: "legacy copies without a member keep the code on the lowest uuid",
		rows: `[{"associate_id":"` + high + `","tax_code":"RSSMRA80A01H501U"},
			{"associate_id":"` + low + `","tax_code":"RSSMRA80A01H501U"}]`,
		want: map[string]string{high: low},
	}, {
		name: "deleted rows neither claim nor lose a code",
		rows: `[{"associate_id":"dm","sport_association_id":"` + association + `","tax_code":"RSSMRA80A01H501U","deleted":true},
			{"associate_id":"l1","tax_code":"RSSMRA80A01H501U"},
			{"associate_id":"dl","tax_code":"VRDLGU80A01H501X","deleted":true},
			{"associate_id":"m1","sport_association_id":"` + association + `","tax_code":"VRDLGU80A01H501X"}]`,
		want: map[string]string{},
	}, {
		name: "duplicate active members fail with a readable error",
		rows: `[{"associate_id":"m1","sport_association_id":"` + association + `","tax_code":"RSSMRA80A01H501U"},
			{"associate_id":"m2","sport_association_id":"` + association + `","tax_code":"rssmra80a01h501u"}]`,
		err: "Codice fiscale duplicato tra le anagrafiche attive: m2.",
	}}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			decoded, err := consumerImportJSON([]byte(tc.rows))
			if err != nil {
				t.Fatal(err)
			}
			got, err := consumerImportTaxConflicts(decoded.([]any), association)
			if tc.err != "" {
				if err == nil || err.Error() != tc.err {
					t.Fatalf("error = %v, want %q", err, tc.err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("conflicts = %v, want %v", got, tc.want)
			}
		})
	}
}
