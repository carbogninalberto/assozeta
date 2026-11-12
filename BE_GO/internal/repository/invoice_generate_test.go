package repository

import "testing"

// The keys are Python's int(hashlib.md5(str(id).encode()).hexdigest()[:15], 16):
// Go and Django serialize numbering on the same pg_advisory_xact_lock.
func TestInvoiceLockKeyMatchesDjango(t *testing.T) {
	for id, want := range map[string]int64{
		"a1000000-0000-4000-8000-000000701001": 100256128553114930,
		"00000000-0000-0000-0000-000000000000": 718496234089346419,
	} {
		if got := InvoiceLockKey(id); got != want {
			t.Fatalf("%s: %d, want %d", id, got, want)
		}
	}
}
