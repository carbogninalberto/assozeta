package repository

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// Cached values only the app writes and the committed collector graph are
// application-state errors (long-tail agent C, PR 2): the 500 page, never a
// handoff.
func TestLongTailRepositoryAppStateErrors(t *testing.T) {
	for name, err := range map[string]error{
		"ErrCollaboratorDeleteUnsupported": ErrCollaboratorDeleteUnsupported,
		"ErrRestoreDeletionUnsupported":    ErrRestoreDeletionUnsupported,
		"ErrDataRestoreProgressShape":      ErrDataRestoreProgressShape,
		"ErrInstanceBackupsCacheShape":     ErrInstanceBackupsCacheShape,
	} {
		if _, ok := refusal.AppStateReason(err); !ok {
			t.Errorf("%s is not an application-state error", name)
		}
		if errors.Is(err, refusal.ErrNotPorted) {
			t.Errorf("%s is still a refusal", name)
		}
	}
	if _, err := unpickleDataRestoreProgress([]byte{0x80, 0x05, 0x63}); !errors.Is(err, ErrDataRestoreProgressShape) {
		t.Fatalf("an unsupported opcode: %v", err)
	}
}
