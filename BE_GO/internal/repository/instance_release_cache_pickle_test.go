package repository

import (
	"encoding/base64"
	"errors"
	"testing"
)

// The pickles below are CPython's:
//
//	import pickle
//	pickle.dumps([{'id': 123456789, 'tag': 'v1.2.3', 'version': '1.2.3',
//	    'name': 'v1.2.3', 'published_at': '2026-01-02T03:04:05Z',
//	    'notes': 'Città\nsecond line', 'url': 'https://example.test/releases/v1.2.3',
//	    'artifacts_ready': True, 'unavailable_reason': None},
//	    {'id': 9, 'tag': 'v1.2.4', 'version': '1.2.4', 'name': 'release 1.2.4',
//	    'published_at': '2026-01-02T03:04:05Z', 'notes': '',
//	    'url': 'https://example.test/releases/v1.2.3', 'artifacts_ready': False,
//	    'unavailable_reason': 'artifacts missing'}], protocol=p)
var catalogPickles = map[string]string{
	"protocol 2": "gAJdcQAofXEBKFgCAAAAaWRxAkoVzVsHWAMAAAB0YWdxA1gGAAAAdjEuMi4zcQRYBwAAAHZlcnNpb25xBVgFAAAAMS4yLjNxBlgEAAAAbmFtZXEHaARYDAAAAHB1Ymxpc2hlZF9hdHEIWBQAAAAyMDI2LTAxLTAyVDAzOjA0OjA1WnEJWAUAAABub3Rlc3EKWBIAAABDaXR0w6AKc2Vjb25kIGxpbmVxC1gDAAAAdXJscQxYJAAAAGh0dHBzOi8vZXhhbXBsZS50ZXN0L3JlbGVhc2VzL3YxLjIuM3ENWA8AAABhcnRpZmFjdHNfcmVhZHlxDohYEgAAAHVuYXZhaWxhYmxlX3JlYXNvbnEPTnV9cRAoaAJLCWgDWAYAAAB2MS4yLjRxEWgFWAUAAAAxLjIuNHESaAdYDQAAAHJlbGVhc2UgMS4yLjRxE2gIaAloClgAAAAAcRRoDGgNaA6JaA9YEQAAAGFydGlmYWN0cyBtaXNzaW5ncRV1ZS4=",
	"protocol 3": "gANdcQAofXEBKFgCAAAAaWRxAkoVzVsHWAMAAAB0YWdxA1gGAAAAdjEuMi4zcQRYBwAAAHZlcnNpb25xBVgFAAAAMS4yLjNxBlgEAAAAbmFtZXEHaARYDAAAAHB1Ymxpc2hlZF9hdHEIWBQAAAAyMDI2LTAxLTAyVDAzOjA0OjA1WnEJWAUAAABub3Rlc3EKWBIAAABDaXR0w6AKc2Vjb25kIGxpbmVxC1gDAAAAdXJscQxYJAAAAGh0dHBzOi8vZXhhbXBsZS50ZXN0L3JlbGVhc2VzL3YxLjIuM3ENWA8AAABhcnRpZmFjdHNfcmVhZHlxDohYEgAAAHVuYXZhaWxhYmxlX3JlYXNvbnEPTnV9cRAoaAJLCWgDWAYAAAB2MS4yLjRxEWgFWAUAAAAxLjIuNHESaAdYDQAAAHJlbGVhc2UgMS4yLjRxE2gIaAloClgAAAAAcRRoDGgNaA6JaA9YEQAAAGFydGlmYWN0cyBtaXNzaW5ncRV1ZS4=",
	"protocol 4": "gASVKwEAAAAAAABdlCh9lCiMAmlklEoVzVsHjAN0YWeUjAZ2MS4yLjOUjAd2ZXJzaW9ulIwFMS4yLjOUjARuYW1llGgEjAxwdWJsaXNoZWRfYXSUjBQyMDI2LTAxLTAyVDAzOjA0OjA1WpSMBW5vdGVzlIwSQ2l0dMOgCnNlY29uZCBsaW5llIwDdXJslIwkaHR0cHM6Ly9leGFtcGxlLnRlc3QvcmVsZWFzZXMvdjEuMi4zlIwPYXJ0aWZhY3RzX3JlYWR5lIiMEnVuYXZhaWxhYmxlX3JlYXNvbpROdX2UKGgCSwloA4wGdjEuMi40lGgFjAUxLjIuNJRoB4wNcmVsZWFzZSAxLjIuNJRoCGgJaAqMAJRoDGgNaA6JaA+MEWFydGlmYWN0cyBtaXNzaW5nlHVlLg==",
	"protocol 5": "gAWVKwEAAAAAAABdlCh9lCiMAmlklEoVzVsHjAN0YWeUjAZ2MS4yLjOUjAd2ZXJzaW9ulIwFMS4yLjOUjARuYW1llGgEjAxwdWJsaXNoZWRfYXSUjBQyMDI2LTAxLTAyVDAzOjA0OjA1WpSMBW5vdGVzlIwSQ2l0dMOgCnNlY29uZCBsaW5llIwDdXJslIwkaHR0cHM6Ly9leGFtcGxlLnRlc3QvcmVsZWFzZXMvdjEuMi4zlIwPYXJ0aWZhY3RzX3JlYWR5lIiMEnVuYXZhaWxhYmxlX3JlYXNvbpROdX2UKGgCSwloA4wGdjEuMi40lGgFjAUxLjIuNJRoB4wNcmVsZWFzZSAxLjIuNJRoCGgJaAqMAJRoDGgNaA6JaA+MEWFydGlmYWN0cyBtaXNzaW5nlHVlLg==",
}

func decode64(t *testing.T, text string) []byte {
	t.Helper()
	raw, err := base64.StdEncoding.DecodeString(text)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestDecodeCatalogProtocols(t *testing.T) {
	for name, encoded := range catalogPickles {
		t.Run(name, func(t *testing.T) {
			releases, found, err := decodeCatalog(decode64(t, encoded))
			if err != nil || !found {
				t.Fatalf("decodeCatalog = %v, %v, %v", releases, found, err)
			}
			if len(releases) != 2 {
				t.Fatalf("decoded %d releases, want 2", len(releases))
			}
			first := releases[0]
			if first.ID != 123456789 || first.Tag != "v1.2.3" || first.Version != "1.2.3" || !first.NameIsTag ||
				first.PublishedAt != "2026-01-02T03:04:05Z" || first.Notes != "Città\nsecond line" ||
				first.URL != "https://example.test/releases/v1.2.3" || !first.ArtifactsReady || first.UnavailableReason != nil {
				t.Fatalf("first release = %+v", first)
			}
			second := releases[1]
			if second.ID != 9 || second.Tag != "v1.2.4" || second.Name != "release 1.2.4" || second.NameIsTag ||
				second.Notes != "" || second.ArtifactsReady || second.UnavailableReason == nil ||
				*second.UnavailableReason != "artifacts missing" {
				t.Fatalf("second release = %+v", second)
			}
		})
	}
}

// An entry whose fields have other types is a cache miss (the caller fetches
// the feed again), not a refusal.
func TestDecodeCatalogUnusableEntryIsMiss(t *testing.T) {
	floatID := "gAWV2QAAAAAAAABdlH2UKIwCaWSURz/4AAAAAAAAjAN0YWeUjAZ2MS4yLjOUjAd2ZXJzaW9ulIwFMS4yLjOUjARuYW1llGgEjAxwdWJsaXNoZWRfYXSUjBQyMDI2LTAxLTAyVDAzOjA0OjA1WpSMBW5vdGVzlIwSQ2l0dMOgCnNlY29uZCBsaW5llIwDdXJslIwkaHR0cHM6Ly9leGFtcGxlLnRlc3QvcmVsZWFzZXMvdjEuMi4zlIwPYXJ0aWZhY3RzX3JlYWR5lIiMEnVuYXZhaWxhYmxlX3JlYXNvbpROdWEu"
	for name, encoded := range map[string]string{
		"float id":       floatID,
		"not a list":     "gAWVCwAAAAAAAAB9lIwCaWSUSwFzLg==",
		"pickle outside": "gAWVCwAAAAAAAACPlChLAUsCSwOQLg==",
		"bytes outside":  "gAWVBwAAAAAAAABDA2FiY5Qu",
		"not a pickle":   "bm90IGEgcGlja2xl",
	} {
		t.Run(name, func(t *testing.T) {
			releases, found, err := decodeCatalog(decode64(t, encoded))
			if name == "not a list" {
				if found || !errors.Is(err, errReleaseCacheNotList) {
					t.Fatalf("decodeCatalog = %v, %v, %v; want the not-a-list app state", releases, found, err)
				}
				return
			}
			if found || err != nil || releases != nil {
				t.Fatalf("decodeCatalog = %v, %v, %v; want a miss", releases, found, err)
			}
		})
	}
}

// unpickleCatalog still refuses opcodes outside the data subset.
func TestUnpickleCatalogRejectsOutsideSubset(t *testing.T) {
	for name, encoded := range map[string]string{
		"set":   "gAWVCwAAAAAAAACPlChLAUsCSwOQLg==",
		"bytes": "gAWVBwAAAAAAAABDA2FiY5Qu",
	} {
		if _, err := unpickleCatalog(decode64(t, encoded)); err == nil {
			t.Fatalf("%s: decoded outside the subset", name)
		}
	}
}
