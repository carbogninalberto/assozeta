package repository

import (
	"reflect"
	"testing"
)

// The changes fallback tries the old value then the new one, skips empty and
// 'None' values, and a value the UUID field rejects ends the block (the Django
// handler catches the lookup error before its actor fallback).
func TestModelAuditIndexFallbackCandidates(t *testing.T) {
	old := "00000000-0000-4000-8000-0000000b0001"
	fresh := "00000000-0000-4000-8000-0000000b0002"
	for _, test := range []struct {
		name       string
		changes    map[string][2]string
		want       []string
		resolvable bool
	}{
		{name: "no changes", changes: map[string][2]string{}, resolvable: true},
		{name: "other field", changes: map[string][2]string{"archived": {"False", "True"}}, resolvable: true},
		{name: "delete entry tries the old value", changes: map[string][2]string{"sport_association": {"None", fresh}}, want: []string{fresh}, resolvable: true},
		{name: "both values in order", changes: map[string][2]string{"sport_association": {old, fresh}}, want: []string{old, fresh}, resolvable: true},
		{name: "empty and none are skipped", changes: map[string][2]string{"sport_association": {"", "NONE"}}, resolvable: true},
		{name: "empty then value", changes: map[string][2]string{"sport_association": {"", fresh}}, want: []string{fresh}, resolvable: true},
		{name: "invalid value ends the block", changes: map[string][2]string{"sport_association": {"not-a-uuid", fresh}}, resolvable: true},
		{name: "invalid second value ends the block", changes: map[string][2]string{"sport_association": {"None", "not-a-uuid"}}, resolvable: true},
		{name: "a value before an invalid one is still tried", changes: map[string][2]string{"sport_association": {old, "not-a-uuid"}}, want: []string{old}, resolvable: true},
	} {
		candidates, resolvable := auditIndexChangesCandidates(test.changes)
		if !reflect.DeepEqual(candidates, test.want) || resolvable != test.resolvable {
			t.Errorf("%s: candidates=%v resolvable=%v, want %v %v", test.name, candidates, resolvable, test.want, test.resolvable)
		}
	}
}
