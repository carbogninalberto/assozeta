package repository

import (
	"encoding/hex"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Fixed synthetic Python protocol-4 record, independent of the Go encoder.
const historicalAdminSession = "8004957c000000000000007d94288c086163746f725f6964948c2430303030303030302d303030302d343030302d383030302d303030303030303030303031948c097461726765745f6964948c2430303030303030302d303030302d343030302d383030302d303030303030303030303032948c0e6173736f63696174696f6e5f6964944e752e"

func TestImpersonationFixedHistoricalRecord(t *testing.T) {
	data, _ := hex.DecodeString(historicalAdminSession)
	record, err := decodeImpersonation(data)
	if err != nil || record.ActorID != "00000000-0000-4000-8000-000000000001" || record.TargetID != "00000000-0000-4000-8000-000000000002" || record.AssociationID != "" {
		t.Fatal("historical record changed")
	}
}

func TestImpersonationRecordCodec(t *testing.T) {
	records := []domain.ImpersonationSession{
		{ActorID: "00000000-0000-4000-8000-000000000001", TargetID: "00000000-0000-4000-8000-000000000002"},
		{ActorID: "00000000-0000-4000-8000-000000000001", TargetID: "00000000-0000-4000-8000-000000000002", AssociationID: "00000000-0000-4000-8000-000000000003"},
	}
	for _, record := range records {
		encoded, err := encodeImpersonation(record)
		if err != nil {
			t.Fatal(err)
		}
		actual, err := decodeImpersonation(encoded)
		if err != nil {
			t.Fatal(err)
		}
		if actual != record {
			t.Fatal("session record changed")
		}
		for end := 0; end < len(encoded); end++ {
			if _, err := decodeImpersonation(encoded[:end]); !errors.Is(err, domain.ErrInvalidImpersonation) {
				t.Fatalf("accepted truncated record at %d", end)
			}
		}
	}
}
func TestImpersonationCodecRejectsExecutableAndWrongShapePickles(t *testing.T) {
	for _, encoded := range []string{
		"8004636f730a73797374656d0a2e", // GLOBAL (never execute or resolve).
		"80045d942e",                   // A list, not the session dictionary.
		"80047d942e",                   // Missing actor and target.
		"80044e2e",                     // None, not a session.
		"80047d284b014b02752e",         // Non-string dictionary members.
	} {
		data, _ := hex.DecodeString(encoded)
		if _, err := decodeImpersonation(data); !errors.Is(err, domain.ErrInvalidImpersonation) {
			t.Fatal("accepted invalid session data")
		}
	}
}
func FuzzImpersonationSessionDecoder(f *testing.F) {
	valid, _ := hex.DecodeString(historicalAdminSession)
	f.Add(valid)
	f.Add([]byte{0x80, 4, '}', 0x94, 'N', '.'})
	f.Fuzz(func(t *testing.T, data []byte) { decodeImpersonation(data) })
}
