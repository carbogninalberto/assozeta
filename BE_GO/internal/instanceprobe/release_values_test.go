package instanceprobe

import (
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"os"
	"strconv"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// releaseValues is testdata/release_values.json: CPython's own bytes for a
// release feed and for hand-written cached catalogs.
//
//	import json, pickle
//	from instance.release_catalog import normalize_release, release_summary
//	feed = json.dumps([...])                      # the same raw releases Go parses
//	catalog = sorted({r['id']: r for r in map(normalize_release, json.loads(feed)) if r}.values(),
//	                 key=lambda r: version_tuple(r['tag']), reverse=True)
//	pickle.dumps(catalog, 4)                      # django-redis' protocol
//	json.dumps(release_summary(catalog, 'v0.0.0'), ensure_ascii=False, allow_nan=False,
//	           separators=(',', ':'))
//	pickle.dumps(cached, 5)                       # a protocol-5 cache value
type releaseValues struct {
	Feed          string `json:"feed"`
	FeedPickle4   string `json:"feed_pickle4_hex"`
	FeedRender    string `json:"feed_render"`
	CachedPickle5 string `json:"cached_pickle5_hex"`
	CachedRender  string `json:"cached_render"`
	BadPickle5    string `json:"bad_pickle5_hex"`
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

func feedReleases(t *testing.T, text string) []Release {
	t.Helper()
	value, err := LoadJSON([]byte(text))
	if err != nil {
		t.Fatal(err)
	}
	var set ReleaseSet
	if err = set.Add(value.([]any)); err != nil {
		t.Fatal(err)
	}
	return set.Releases()
}

func releaseByTag(t *testing.T, releases []Release, tag string) Release {
	t.Helper()
	for _, release := range releases {
		if release.Tag == tag {
			return release
		}
	}
	t.Fatalf("no release %s", tag)
	return Release{}
}

// normalize_release copies a feed's id, name, published_at and notes as they
// are: Django serves and renders each value.
func TestNormalizeKeepsPythonValues(t *testing.T) {
	values := loadReleaseValues(t)
	releases := feedReleases(t, values.Feed)
	if len(releases) != 8 {
		t.Fatalf("normalized %d releases, want 8", len(releases))
	}
	text := releaseByTag(t, releases, "v1.2.0")
	if !text.IDValueSet || text.IDValue != "release-string" || text.Name != "String id" {
		t.Fatalf("string id release = %+v", text)
	}
	bigRelease := releaseByTag(t, releases, "v1.3.0")
	number, ok := bigRelease.NameValue.(*big.Int)
	if !bigRelease.IDValueSet || bigRelease.IDValue.(*big.Int).Cmp(new(big.Int).Lsh(big.NewInt(1), 70)) != 0 ||
		!ok || number.Int64() != 42 {
		t.Fatalf("big int id release = %+v", bigRelease)
	}
	float := releaseByTag(t, releases, "v1.4.0")
	if !float.IDValueSet || float.IDValue != 1.5 || !float.NameIsTag {
		t.Fatalf("float id release = %+v", float)
	}
	nested := releaseByTag(t, releases, "v1.5.0")
	name, ok := nested.NameValue.([]any)
	if !ok || len(name) != 3 || name[2].(Dict)[0].Name != "a" || name[2].(Dict)[1].Name != "b" {
		t.Fatalf("list name = %+v", nested.NameValue)
	}
	notes, ok := nested.NotesValue.([]any)
	if !ok || len(notes) != 2 || notes[1].(Dict)[0].Value != 1.0 {
		t.Fatalf("list notes = %+v", nested.NotesValue)
	}
	boolRelease := releaseByTag(t, releases, "v1.6.0")
	if boolRelease.NameValue != true || boolRelease.PublishedValue != 1.5 || boolRelease.Notes != "" {
		t.Fatalf("bool/float release = %+v", boolRelease)
	}
	none := releaseByTag(t, releases, "v1.7.0")
	if !none.IDValueSet || none.IDValue != nil {
		t.Fatalf("None id release = %+v", none)
	}
	trueID := releaseByTag(t, releases, "v1.8.0")
	if !trueID.IDValueSet || trueID.IDValue != true {
		t.Fatalf("True id release = %+v", trueID)
	}
	floatID := releaseByTag(t, releases, "v1.9.0")
	if !floatID.IDValueSet || floatID.IDValue != 2.0 || floatID.Notes != "" {
		t.Fatalf("float 2.0 id release = %+v", floatID)
	}

	// The rendered summary is DRF's JSONRenderer body.
	rendered, err := Render(Summary(releases, "v0.0.0", big.NewInt(1)))
	if err != nil {
		t.Fatal(err)
	}
	if string(rendered) != values.FeedRender {
		t.Fatalf("render differs from DRF:\n got %s\nwant %s", rendered, values.FeedRender)
	}
}

// A JSON object value keeps its keys in Go's sorted order (json.loads keeps
// the source order, which LoadJSON's map does not); a list value keeps its
// order. Both render as DRF writes them.
func TestNormalizeKeepsObjectAndListValues(t *testing.T) {
	raw := map[string]any{"id": Int("1"), "tag_name": "v1.0.0", "published_at": []any{Int("1"), "x"},
		"name": map[string]any{"b": Int("2"), "a": Int("1")}}
	release, err := normalize(raw)
	if err != nil || release == nil {
		t.Fatalf("normalize = %v, %v", release, err)
	}
	published, ok := release.PublishedValue.([]any)
	if !ok || len(published) != 2 || published[0].(*big.Int).Int64() != 1 {
		t.Fatalf("list date = %+v", release.PublishedValue)
	}
	name, ok := release.NameValue.(Dict)
	if !ok || len(name) != 2 || name[0].Name != "a" || name[1].Name != "b" {
		t.Fatalf("object name = %+v", release.NameValue)
	}
	rendered, err := Render(release.Map())
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(rendered), `"name":{"a":1,"b":2},"published_at":[1,"x"]`) {
		t.Fatalf("render = %s", rendered)
	}
}

// releases[release['id']] = release raises TypeError for an unhashable id:
// Django's 500 page (application state), not a cache miss or a refusal.
func TestNormalizeUnhashableIDIsAppState(t *testing.T) {
	for name, id := range map[string]any{"list": []any{Int("1")}, "object": map[string]any{"a": Int("1")}} {
		t.Run(name, func(t *testing.T) {
			raw := map[string]any{"tag_name": "v1.0.0", "published_at": "2026-01-01T00:00:00Z", "id": id}
			release, err := normalize(raw)
			if release != nil || !errors.Is(err, ErrReleaseRaises) {
				t.Fatalf("normalize = %v, %v; want ErrReleaseRaises", release, err)
			}
			if !errors.Is(err, refusal.ErrAppState) {
				t.Fatalf("error %v is not application state", err)
			}
		})
	}
}

// The catalog dict follows Python equality: 1 == 1.0 == True is one key, a
// str and None are their own keys, and every NaN id is one key (json.loads
// returns the one json.decoder.NaN object for every NaN literal).
func TestReleaseSetPythonKeys(t *testing.T) {
	release := func(id any, tag string) map[string]any {
		return map[string]any{"id": id, "tag_name": tag, "published_at": "2026-01-01T00:00:00Z", "name": tag}
	}
	var set ReleaseSet
	if err := set.Add([]any{
		release(Int("1"), "v1.0.0"),
		release(true, "v1.0.1"),
		release(1.0, "v1.0.2"),
		release("1", "v1.0.3"),
		release(nil, "v1.0.4"),
		release(math.NaN(), "v1.0.5"),
		release(math.NaN(), "v1.0.6"),
	}); err != nil {
		t.Fatal(err)
	}
	releases := set.Releases()
	if len(releases) != 4 {
		t.Fatalf("%d releases, want 4 (1/True/1.0 collapse; the two NaN fold into one key)", len(releases))
	}
	byTag := map[string]Release{}
	for _, release := range releases {
		byTag[release.Tag] = release
	}
	// The collapsed key keeps the last value (1.0) and the first position.
	if _, found := byTag["v1.0.0"]; found {
		t.Fatalf("1, True and 1.0 are three keys: %+v", releases)
	}
	if _, found := byTag["v1.0.1"]; found {
		t.Fatalf("1, True and 1.0 are three keys: %+v", releases)
	}
	if byTag["v1.0.2"].IDValue != 1.0 {
		t.Fatalf("the collapsed key kept %+v, want the last value 1.0", byTag["v1.0.2"].IDValue)
	}
	if byTag["v1.0.3"].IDValue != "1" || byTag["v1.0.4"].IDValue != nil || !byTag["v1.0.4"].IDValueSet {
		t.Fatalf("string and None ids = %+v, %+v", byTag["v1.0.3"], byTag["v1.0.4"])
	}
	nan, found := byTag["v1.0.6"]
	if !found {
		t.Fatalf("the NaN key is missing: %+v", releases)
	}
	if value, ok := nan.IDValue.(float64); !ok || !math.IsNaN(value) {
		t.Fatalf("the NaN key kept %+v, want the last value NaN", nan.IDValue)
	}
	if _, found := byTag["v1.0.5"]; found {
		t.Fatalf("the NaN key kept the first value: %+v", releases)
	}
}

// loneHigh and loneLow are U+D800 and U+DFFF in the WTF-8 form LoadJSON
// keeps for a lone surrogate.
const (
	loneHigh = "\xed\xa0\x80"
	loneLow  = "\xed\xbf\xbf"
)

func summaryOf(releases []Release) Dict {
	return Summary(releases, "v0.0.0", big.NewInt(1))
}

// A NaN or infinite float (allow_nan=False) or a lone surrogate
// (ensure_ascii=False) in the rendered entries is the exception Django lets
// escape: its 500 page.
func TestRenderRefusesWhatDRFCannotEncode(t *testing.T) {
	base := Release{ID: 1, Tag: "v1.0.0", Version: "1.0.0", Name: "ok", PublishedAt: "2026-01-01T00:00:00Z",
		URL: "https://example.test/v1.0.0", ArtifactsReady: true}
	for name, mutate := range map[string]func(*Release){
		"NaN name":         func(r *Release) { r.NameValue = math.NaN() },
		"+Inf date":        func(r *Release) { r.PublishedValue = math.Inf(1) },
		"-Inf notes":       func(r *Release) { r.NotesValue = math.Inf(-1) },
		"lone surrogate":   func(r *Release) { r.NameValue = "bad " + loneHigh },
		"nested NaN":       func(r *Release) { r.NameValue = []any{big.NewInt(1), math.NaN()} },
		"nested surrogate": func(r *Release) { r.NotesValue = []any{"ok", loneLow} },
	} {
		t.Run(name, func(t *testing.T) {
			release := base
			mutate(&release)
			_, err := Render(summaryOf([]Release{release}))
			if !errors.Is(err, ErrReleaseRender) || !errors.Is(err, refusal.ErrAppState) {
				t.Fatalf("render error = %v, want ErrReleaseRender app state", err)
			}
		})
	}
}

// An entry outside the rendered page (and not pending) does not matter: DRF
// never serializes it.
func TestRenderIgnoresEntriesOutsideThePage(t *testing.T) {
	release := func(minor int, name any) Release {
		tag := "v1.0." + strconv.Itoa(minor)
		return Release{ID: int64(minor), Tag: tag, Version: "1.0." + strconv.Itoa(minor), Name: "ok",
			NameValue: name, PublishedAt: "2026-01-01T00:00:00Z", URL: "https://example.test/" + tag, ArtifactsReady: true}
	}
	releases := make([]Release, 0, 11)
	for minor := 10; minor >= 0; minor-- {
		releases = append(releases, release(minor, nil))
	}
	releases[10].NameValue = "bad " + loneHigh // v1.0.0: the eleventh, page 2
	page1 := Summary(releases, "v1.0.10", big.NewInt(1))
	if _, err := Render(page1); err != nil {
		t.Fatalf("page 1 render = %v, want the bad entry ignored", err)
	}
	page2 := Summary(releases, "v1.0.10", big.NewInt(2))
	if _, err := Render(page2); !errors.Is(err, ErrReleaseRender) {
		t.Fatalf("page 2 render = %v, want ErrReleaseRender", err)
	}
}

// Values render as json.dumps(ensure_ascii=False, allow_nan=False,
// separators=(',', ':')) does: ints of any size as digits, floats as repr,
// dict keys in insertion order, no HTML escaping and U+2028/U+2029 escaped.
func TestRenderPythonValues(t *testing.T) {
	value := Dict{
		{Name: "b", Value: 1.0},
		{Name: "a", Value: []any{new(big.Int).Lsh(big.NewInt(1), 70), 1e16, 1e-5, math.Copysign(0, -1), 2.5}},
		{Name: "html", Value: "<&>\u2028"},
		{Name: "none", Value: nil},
		{Name: "true", Value: true},
	}
	got, err := Render(value)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"b":1.0,"a":[1180591620717411303424,1e+16,1e-05,-0.0,2.5],"html":"<&>\u2028","none":null,"true":true}`
	if string(got) != want {
		t.Fatalf("Render = %s, want %s", got, want)
	}
}
