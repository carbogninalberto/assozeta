package repository

import (
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math/big"
	"os"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
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

// releaseValues is testdata/release_values.json: CPython's own bytes for a
// release feed whose values normalize_release copies as they are, and for two
// hand-written protocol-5 cache values.
//
//	feed = json.dumps([...])  # ids: str, 2**70, 1.5, 7, 8, None, True, 2.0
//	catalog = sorted({r['id']: r for r in map(normalize_release, json.loads(feed)) if r}.values(),
//	                 key=lambda r: version_tuple(r['tag']), reverse=True)
//	pickle.dumps(catalog, 4)                       # django-redis' protocol
//	pickle.dumps([{...release dicts...}], 5)        # cached and bad catalogs
//	json.dumps(release_summary(cached, 'v0.0.0'), ensure_ascii=False, allow_nan=False,
//	           separators=(',', ':'))
type releaseValues struct {
	Feed         string `json:"feed"`
	FeedPickle4  string `json:"feed_pickle4_hex"`
	CachedPickle string `json:"cached_pickle5_hex"`
	CachedRender string `json:"cached_render"`
	BadPickle    string `json:"bad_pickle5_hex"`
}

func loadReleaseValues(t *testing.T) releaseValues {
	t.Helper()
	raw, err := os.ReadFile("testdata/release_values.json")
	if err != nil {
		t.Fatal(err)
	}
	var values releaseValues
	if err = json.Unmarshal(raw, &values); err != nil {
		t.Fatal(err)
	}
	return values
}

func decodeHex(t *testing.T, text string) []byte {
	t.Helper()
	raw, err := hex.DecodeString(text)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

// The cache writer picks CPython's opcodes and objects for the values
// normalize_release copies (str/None/bool ids, ints of any size, floats, lists
// and objects), so Django's cache.get reads the catalog back unchanged.
func TestPickleReleasesValuesGolden(t *testing.T) {
	values := loadReleaseValues(t)
	value, err := instanceprobe.LoadJSON([]byte(values.Feed))
	if err != nil {
		t.Fatal(err)
	}
	var set instanceprobe.ReleaseSet
	if err = set.Add(value.([]any)); err != nil {
		t.Fatal(err)
	}
	releases := set.Releases()
	if len(releases) != 8 {
		t.Fatalf("normalized %d releases, want 8", len(releases))
	}
	if got := hex.EncodeToString(PickleReleases(releases)); got != values.FeedPickle4 {
		t.Fatalf("pickle differs from CPython:\n got %s\nwant %s", got, values.FeedPickle4)
	}
}

// The cache reader decodes the same values: an int of any size, floats (a zero
// float date), the objects normalize_release copied (keys in insertion order)
// and one release without any of them.
func TestDecodeCatalogValues(t *testing.T) {
	values := loadReleaseValues(t)
	releases, found, err := decodeCatalog(decodeHex(t, values.CachedPickle))
	if err != nil || !found || len(releases) != 3 {
		t.Fatalf("decodeCatalog = %v, %v, %v", releases, found, err)
	}
	byTag := map[string]instanceprobe.Release{}
	for _, release := range releases {
		byTag[release.Tag] = release
	}
	object := byTag["v2.0.0"]
	name, ok := object.NameValue.(instanceprobe.Dict)
	if !ok || len(name) != 2 || name[0].Name != "b" || name[1].Name != "a" {
		t.Fatalf("cached object value = %+v", object.NameValue)
	}
	if notes, ok := object.NotesValue.([]any); !ok || len(notes) != 0 {
		t.Fatalf("cached empty list = %+v", object.NotesValue)
	}
	if zero := byTag["v2.1.0"]; zero.PublishedValue != 0.0 || zero.Notes != "" || zero.UnavailableReason == nil {
		t.Fatalf("cached zero float date = %+v", zero)
	}
	if bigRelease := byTag["v2.2.0"]; !bigRelease.IDValueSet ||
		bigRelease.IDValue.(*big.Int).Cmp(new(big.Int).Lsh(big.NewInt(1), 70)) != 0 {
		t.Fatalf("cached big int id = %+v", bigRelease)
	}
	rendered, err := instanceprobe.Render(instanceprobe.Summary(releases, "v0.0.0", big.NewInt(1)))
	if err != nil {
		t.Fatal(err)
	}
	if string(rendered) != values.CachedRender {
		t.Fatalf("render differs from DRF:\n got %s\nwant %s", rendered, values.CachedRender)
	}
}

// A cached catalog Django serves but DRF cannot render (a lone surrogate in a
// str, an infinite float id) is read, then the render is the 500 page.
func TestDecodeCatalogValuesRendersAs500(t *testing.T) {
	values := loadReleaseValues(t)
	releases, found, err := decodeCatalog(decodeHex(t, values.BadPickle))
	if err != nil || !found || len(releases) != 3 {
		t.Fatalf("decodeCatalog = %v, %v, %v", releases, found, err)
	}
	if _, err = instanceprobe.Render(instanceprobe.Summary(releases, "v0.0.0", big.NewInt(1))); !errors.Is(err, instanceprobe.ErrReleaseRender) {
		t.Fatalf("render error = %v, want ErrReleaseRender", err)
	}
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

// A release entry normalize_release cannot have written (a None name, extra
// keys) or a value outside the decoded subset is a cache miss (the caller
// fetches the feed again), not a refusal.
func TestDecodeCatalogUnusableEntryIsMiss(t *testing.T) {
	for name, encoded := range map[string]string{
		"None name":      "gAWViQAAAAAAAABdlH2UKIwCaWSUSwGMA3RhZ5SMBnYxLjAuMJSMB3ZlcnNpb26UjAUxLjAuMJSMBG5hbWWUTowMcHVibGlzaGVkX2F0lIwBepSMBW5vdGVzlIwAlIwDdXJslIwBdZSMD2FydGlmYWN0c19yZWFkeZSIjBJ1bmF2YWlsYWJsZV9yZWFzb26UTnVhLg==",
		"extra key":      "gAWVlgAAAAAAAABdlH2UKIwCaWSUSwGMA3RhZ5SMBnYxLjAuMJSMB3ZlcnNpb26UjAUxLjAuMJSMBG5hbWWUjAFulIwMcHVibGlzaGVkX2F0lIwBepSMBW5vdGVzlIwAlIwDdXJslIwBdZSMD2FydGlmYWN0c19yZWFkeZSIjBJ1bmF2YWlsYWJsZV9yZWFzb26UTowFZXh0cmGUSwF1YS4=",
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
