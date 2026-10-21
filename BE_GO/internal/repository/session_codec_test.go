package repository

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Fixed CPython protocol-4 vectors, independent of the Go decoder.
const sessionPrimitiveVector = "gASVPwAAAAAAAAB9lCiMDV9hdXRoX3VzZXJfaWSUjAdmaXh0dXJllIwFc3RhdGWUXZQojAF4lIhOSv////9HQAQAAAAAAABldS4="
const sessionSharedDAG = "gASVWQAAAAAAAABdlChdlChdlChdlChdlChdlChdlChdlChdlChdlChdlChdlChdlChdlChdlE5haA5laA1laAxlaAtlaAplaAllaAhlaAdlaAZlaAVlaARlaANlaAJlaAFlLg=="

func TestSessionCodecPrimitiveData(t *testing.T) {
	raw, _ := base64.StdEncoding.DecodeString(sessionPrimitiveVector)
	want := map[string]any{"_auth_user_id": "fixture", "state": []any{"x", true, nil, json.Number("-1"), 2.5}}
	got, err := decodeDjangoSession(context.Background(), raw)
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatal("primitive Python session data differs")
	}
	for i := range raw {
		if _, err := decodeDjangoSession(context.Background(), raw[:i]); !errors.Is(err, domain.ErrInvalidCachedSession) {
			t.Fatalf("truncation %d accepted", i)
		}
	}
}

func TestSessionCodecRejectsExpansionAndInvalidReferences(t *testing.T) {
	dag, _ := base64.StdEncoding.DecodeString(sessionSharedDAG)
	cycle, _ := base64.StdEncoding.DecodeString("gASVBgAAAAAAAABdlGgAYS4=")
	for name, raw := range map[string][]byte{
		"shared expansion": dag, "recursive list": cycle,
		"unassigned memo entry": {0x80, 4, ']', 'N', 'q', 2, 'a', 'h', 0, 'a', '.'},
		"object constructor":    {0x80, 4, 'c'},
		"oversized payload":     make([]byte, maxDjangoSessionBytes+1),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := decodeDjangoSession(context.Background(), raw); !errors.Is(err, domain.ErrInvalidCachedSession) {
				t.Fatal("unsafe or invalid session accepted")
			}
		})
	}
}

func TestSessionCodecSparseMemoUsesEntryCount(t *testing.T) {
	// Python MEMOIZE stores at len(memo), not highest index + 1.
	raw := []byte{0x80, 4, ']', 'N', 'q', 2, 'a', 'K', 7, 0x94, 'a', 'h', 1, 'a', '.'}
	got, err := decodeDjangoSession(context.Background(), raw)
	if err != nil || !reflect.DeepEqual(got, []any{nil, json.Number("7"), json.Number("7")}) {
		t.Fatal("sparse memo semantics differ")
	}
}

func FuzzSessionCodec(f *testing.F) {
	for _, encoded := range []string{sessionPrimitiveVector, sessionSharedDAG} {
		raw, _ := base64.StdEncoding.DecodeString(encoded)
		f.Add(raw)
	}
	f.Add([]byte{0x80, 5, 'N', '.'})
	f.Fuzz(func(t *testing.T, raw []byte) {
		_, _ = decodeDjangoSession(context.Background(), raw)
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if _, err := decodeDjangoSession(ctx, raw); !errors.Is(err, context.Canceled) {
			t.Fatal("cancelled decode ran")
		}
	})
}
