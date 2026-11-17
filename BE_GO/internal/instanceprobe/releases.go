package instanceprobe

import (
	"context"
	"errors"
	"fmt"
	"math"
	"math/big"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// release_catalog constants.
const (
	Repository  = "carbogninalberto/assozeta"
	ReleasesAPI = "https://api.github.com/repos/" + Repository
	ReleasesURL = "https://github.com/" + Repository + "/releases"
	// UnavailableReason is normalize_release's literal for an incomplete
	// distribution.
	UnavailableReason = "The complete update distribution has not been published yet."
)

// VersionTuple is release_catalog.version_tuple for a str:
// STABLE_VERSION.fullmatch, where \d is any Unicode decimal digit and int()
// reads it; ok is false for None.
func VersionTuple(value string) ([3]*big.Int, bool) {
	var result [3]*big.Int
	rest := strings.TrimPrefix(value, "v")
	parts := strings.Split(rest, ".")
	if len(parts) != 3 {
		return result, false
	}
	for i, part := range parts {
		runes := []rune(part)
		if len(runes) == 0 || (runes[0] == '0' && len(runes) > 1) || runes[0] < '0' || runes[0] > '9' {
			return result, false
		}
		number := new(big.Int)
		for _, r := range runes {
			digit, ok := decimalDigit(r)
			if !ok {
				return result, false
			}
			number.Mul(number, big.NewInt(10)).Add(number, big.NewInt(int64(digit)))
		}
		result[i] = number
	}
	return result, true
}

// decimalDigit is the value of a Unicode Nd character: Nd characters come
// in complete runs of ten consecutive code points starting at zero.
func decimalDigit(r rune) (int, bool) {
	if !unicode.IsDigit(r) {
		return 0, false
	}
	start := r
	for unicode.IsDigit(start - 1) {
		start--
	}
	return int(r-start) % 10, true
}

func compareVersions(a, b [3]*big.Int) int {
	for i := range a {
		if c := a[i].Cmp(b[i]); c != 0 {
			return c
		}
	}
	return 0
}

// DictField is one key and value of a Dict.
type DictField struct {
	Name  string
	Value any
}

// Dict is a Python dict with its keys in insertion order: the objects
// normalize_release copies, the summary it returns and the objects the release
// cache reader decodes keep the order DRF renders.
type Dict []DictField

// Lookup is dict.get(name).
func (d Dict) Lookup(name string) (any, bool) {
	var value any
	present := false
	for _, field := range d {
		if field.Name == name {
			value, present = field.Value, true
		}
	}
	return value, present
}

// MarshalJSON renders the dict as DRF's JSONRenderer does (insertion order).
func (d Dict) MarshalJSON() ([]byte, error) { return Render(d) }

// Release is one normalize_release dict. A field it copies from the feed holds
// its Python value when it is not the field's plain type: the typed fields (ID
// int64, Name/PublishedAt/Notes str) carry the plain type and the *Value field
// carries anything else (a str id, an int of any size, a float, a bool, None, a
// list or an object). IDValueSet separates a Python None id (IDValue nil) from
// the typed one. tag/version/url/artifacts_ready/unavailable_reason are always
// what normalize_release writes. NameIsTag records that name is the tag object
// itself (raw name falsy), which pickle memoizes as one string.
type Release struct {
	ID      int64
	IDValue any
	// IDValueSet reports that IDValue is the Python id (which may be None,
	// IDValue nil); otherwise ID holds it.
	IDValueSet bool

	Tag, Version string

	Name      string
	NameValue any
	NameIsTag bool

	PublishedAt    string
	PublishedValue any

	Notes      string
	NotesValue any

	URL               string
	ArtifactsReady    bool
	UnavailableReason *string
}

// Map is the release as Django renders it (the dict normalize_release built,
// in its key order).
func (r Release) Map() Dict {
	var reason any
	if r.UnavailableReason != nil {
		reason = *r.UnavailableReason
	}
	id := any(r.ID)
	if r.IDValueSet {
		id = r.IDValue
	}
	name := any(r.Name)
	if r.NameValue != nil {
		name = r.NameValue
	}
	published := any(r.PublishedAt)
	if r.PublishedValue != nil {
		published = r.PublishedValue
	}
	notes := any(r.Notes)
	if r.NotesValue != nil {
		notes = r.NotesValue
	}
	return Dict{{Name: "id", Value: id}, {Name: "tag", Value: r.Tag}, {Name: "version", Value: r.Version},
		{Name: "name", Value: name}, {Name: "published_at", Value: published}, {Name: "notes", Value: notes},
		{Name: "url", Value: r.URL}, {Name: "artifacts_ready", Value: r.ArtifactsReady},
		{Name: "unavailable_reason", Value: reason}}
}

// ReleaseError is release_catalog.ReleaseError (the view's 503 message).
type ReleaseError struct{ Message string }

func (e *ReleaseError) Error() string { return e.Message }

// ReleaseNotPublished is fetch_release's answer for a release that is not a
// published stable one.
const ReleaseNotPublished = "The selected release is not a published stable release."

// ReleaseNotReadable is the native answer (accepted divergence X2) for a feed
// this port does not read: Django's text for an invalid catalog when the feed
// was read (ErrReleaseUnread), its unavailable text for a response Go does
// not decode (ErrUnreadResponse).
func ReleaseNotReadable(err error) *ReleaseError {
	if errors.Is(err, ErrReleaseUnread) {
		return &ReleaseError{Message: "Invalid release catalog response."}
	}
	return &ReleaseError{Message: "Release service is unavailable. Please try again later."}
}

// ErrReleaseUnread is a feed value outside LoadJSON's documented set (nil,
// bool, Int, float64, string, []any, map[string]any), which the port cannot
// carry. json.loads never produces one, so no feed value is left here: every
// JSON value normalize_release copies (a str or int of any size, a float, a
// bool, None, a list or an object) is kept and rendered. The release views
// still answer a defensive occurrence with the invalid-catalog text (accepted
// divergence X2).
var ErrReleaseUnread = errors.New("instanceprobe: release feed value outside the JSON value set")

// ErrReleaseRaises is a feed item on which normalize_release raises
// (AttributeError, KeyError or TypeError). Neither the release list nor the
// update request catches it, so Django answers its 500 page; Go answers the
// same page (application state, never handed off).
var ErrReleaseRaises = refusal.AppState("instanceprobe: release feed item Django raises on")

// ErrReleaseRender is a summary value DRF's JSONRenderer cannot write: a NaN
// or infinite float (allow_nan=False) or a str with a lone surrogate
// (ensure_ascii=False, then .encode('utf-8')). The renderer raises, so Django
// answers its 500 page; Go answers the same page (application state).
var ErrReleaseRender = refusal.AppState("instanceprobe: release summary value DRF cannot render")

// quoteTag is urllib.parse.quote(tag, safe="") for a stable tag (only
// letters, digits and dots, which quote keeps).
func quoteTag(tag string) string { return url.PathEscape(tag) }

// normalize is normalize_release(raw); nil, nil for a skipped release.
func normalize(item any) (*Release, error) {
	raw, ok := item.(map[string]any)
	if !ok {
		return nil, ErrReleaseRaises // raw.get: AttributeError
	}
	tagValue, present := raw["tag_name"]
	if !present {
		tagValue = ""
	}
	if Truthy(raw["draft"]) || Truthy(raw["prerelease"]) || !Truthy(raw["published_at"]) {
		return nil, nil
	}
	// version_tuple(str(tag)): only a str can match.
	tag, isString := tagValue.(string)
	if !isString {
		return nil, nil
	}
	if _, ok := VersionTuple(tag); !ok {
		return nil, nil
	}
	assets := map[string]map[string]any{}
	if list, present := raw["assets"]; present {
		items, err := iterate(list)
		if err != nil {
			return nil, err
		}
		for _, entry := range items {
			asset, ok := entry.(map[string]any)
			if !ok {
				return nil, ErrReleaseRaises // item.get('name'): AttributeError
			}
			name, isString := asset["name"].(string)
			if !isString {
				if _, unhashable := asset["name"].([]any); unhashable {
					return nil, ErrReleaseRaises // unhashable dict key: TypeError
				}
				if _, unhashable := asset["name"].(map[string]any); unhashable {
					return nil, ErrReleaseRaises // unhashable dict key: TypeError
				}
				continue
			}
			assets[name] = asset
		}
	}
	ready := true
	for _, name := range []string{"assozeta-selfhost-" + tag + ".tar.gz", "assozeta-update.json"} {
		asset, present := assets[name]
		if !present || asset["state"] != "uploaded" {
			ready = false
			break
		}
		size, present := asset["size"]
		if !present {
			ready = false
			break
		}
		positive, err := greaterThanZero(size)
		if err != nil {
			return nil, err
		}
		if !positive {
			ready = false
			break
		}
	}
	idValue, present := raw["id"]
	if !present {
		return nil, ErrReleaseRaises // raw['id']: KeyError
	}
	version, _ := VersionTuple(tag)
	release := &Release{Tag: tag, Version: fmt.Sprintf("%s.%s.%s", version[0], version[1], version[2]),
		URL: ReleasesURL + "/tag/" + quoteTag(tag), ArtifactsReady: ready}
	switch id := idValue.(type) {
	case Int:
		number, ok := new(big.Int).SetString(string(id), 10)
		if !ok {
			return nil, ErrReleaseUnread // not a decimal integer: outside LoadJSON's values
		}
		if number.IsInt64() {
			release.ID = number.Int64()
		} else {
			release.IDValue, release.IDValueSet = number, true
		}
	case float64, string, bool:
		// Hashable ids: releases[release['id']] accepts them unchanged.
		release.IDValue, release.IDValueSet = idValue, true
	case nil:
		release.IDValueSet = true // Python None: a hashable id (the dict key None)
	case []any, map[string]any:
		return nil, ErrReleaseRaises // releases[release['id']] = release: TypeError
	default:
		return nil, ErrReleaseUnread
	}
	if name := raw["name"]; Truthy(name) {
		if text, ok := name.(string); ok {
			release.Name = text
		} else {
			release.NameValue = pythonValue(name)
		}
	} else {
		release.Name, release.NameIsTag = tag, true
	}
	if published, ok := raw["published_at"].(string); ok {
		release.PublishedAt = published
	} else {
		release.PublishedValue = pythonValue(raw["published_at"])
	}
	if body := raw["body"]; Truthy(body) {
		if text, ok := body.(string); ok {
			release.Notes = text
		} else {
			release.NotesValue = pythonValue(body)
		}
	}
	if !ready {
		reason := UnavailableReason
		release.UnavailableReason = &reason
	}
	return release, nil
}

// pythonValue converts a LoadJSON value to the representation a Release field
// keeps (and the summary renders): any int as *big.Int, float64, bool, string,
// nil (None), []any and Dict. A JSON object's keys are emitted in Go's sorted
// order: json.loads keeps the source order, which LoadJSON's map does not (the
// cache reader, whose pickles do keep it, builds the Dict in that order).
func pythonValue(value any) any {
	switch v := value.(type) {
	case Int:
		number, ok := new(big.Int).SetString(string(v), 10)
		if !ok {
			return nil
		}
		return number
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = pythonValue(item)
		}
		return out
	case map[string]any:
		keys := make([]string, 0, len(v))
		for key := range v {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		out := make(Dict, 0, len(v))
		for _, key := range keys {
			out = append(out, DictField{Name: key, Value: pythonValue(v[key])})
		}
		return out
	}
	return value
}

// iterate is a for loop over raw.get('assets', []): a list, or the keys of
// a dict or characters of a string (whose .get then fails unless empty).
func iterate(value any) ([]any, error) {
	switch v := value.(type) {
	case []any:
		return v, nil
	case map[string]any:
		if len(v) == 0 {
			return nil, nil
		}
	case string:
		if v == "" {
			return nil, nil
		}
	}
	return nil, ErrReleaseRaises // None/int iterate: TypeError; dict/str keys: AttributeError
}

// greaterThanZero is `value > 0` for a JSON value (bools are ints; other
// types raise TypeError).
func greaterThanZero(value any) (bool, error) {
	switch v := value.(type) {
	case bool:
		return v, nil
	case Int:
		return !strings.HasPrefix(string(v), "-") && strings.Trim(string(v), "0") != "", nil
	case float64:
		return v > 0, nil
	}
	return false, ErrReleaseRaises // value > 0: TypeError
}

// ReleaseSet is fetch_releases' dict of normalized releases by id (first
// position, last value). The keys follow Python equality: ints of any size,
// floats and bools compare by value (1 == 1.0 == True), strs only with strs,
// None only with None and a NaN float with nothing.
type ReleaseSet struct {
	keys  []string
	byKey map[string]Release
}

// Add normalizes one page's items.
func (r *ReleaseSet) Add(items []any) error {
	if r.byKey == nil {
		r.byKey = map[string]Release{}
	}
	for _, item := range items {
		release, err := normalize(item)
		if err != nil {
			return err
		}
		if release == nil {
			continue
		}
		key := releaseKey(*release)
		if _, seen := r.byKey[key]; !seen {
			r.keys = append(r.keys, key)
		}
		r.byKey[key] = *release
	}
	return nil
}

// Releases is the sorted catalog.
func (r *ReleaseSet) Releases() []Release {
	releases := make([]Release, 0, len(r.keys))
	for _, key := range r.keys {
		releases = append(releases, r.byKey[key])
	}
	SortReleases(releases)
	return releases
}

// nanSequence keys each NaN id apart: a Python dict never matches a NaN float
// against another one (json.loads makes a new float object for each).
var nanSequence uint64

// releaseKey is the Python dict key of the normalized id.
func releaseKey(r Release) string {
	if !r.IDValueSet {
		return "num:" + strconv.FormatInt(r.ID, 10)
	}
	switch v := r.IDValue.(type) {
	case nil:
		return "none"
	case string:
		return "str:" + v
	case bool:
		if v {
			return "num:1"
		}
		return "num:0"
	case *big.Int:
		return "num:" + v.String()
	case float64:
		switch {
		case math.IsNaN(v):
			return "nan:" + strconv.FormatUint(atomic.AddUint64(&nanSequence, 1), 10)
		case math.IsInf(v, 1):
			return "num:+inf"
		case math.IsInf(v, -1):
			return "num:-inf"
		}
		return "num:" + new(big.Rat).SetFloat64(v).RatString()
	}
	return "other"
}

// FetchReleases is release_catalog.fetch_releases(): every page of 100,
// deduplicated by id, sorted by version, newest first (stable).
func (c *Client) FetchReleases(ctx context.Context) ([]Release, error) {
	var set ReleaseSet
	for page := 1; ; page++ {
		value, err := c.readRelease(ctx, fmt.Sprintf("%s/releases?per_page=100&page=%d", ReleasesAPI, page))
		if err != nil {
			return nil, err
		}
		items, ok := value.([]any)
		if !ok {
			return nil, &ReleaseError{Message: "Invalid release catalog response."}
		}
		if err = set.Add(items); err != nil {
			return nil, err
		}
		if len(items) < 100 {
			break
		}
	}
	return set.Releases(), nil
}

// SortReleases is sorted(..., key=version_tuple(tag), reverse=True).
func SortReleases(releases []Release) {
	sort.SliceStable(releases, func(i, j int) bool {
		a, _ := VersionTuple(releases[i].Tag)
		b, _ := VersionTuple(releases[j].Tag)
		return compareVersions(a, b) > 0
	})
}

func (c *Client) readRelease(ctx context.Context, target string) (any, error) {
	value, err := c.ReadJSON(ctx, target, map[string]string{"Accept": "application/vnd.github+json",
		"User-Agent": "Assozeta-Self-Instance", "X-GitHub-Api-Version": "2022-11-28"})
	var status *HTTPError
	switch {
	case errors.As(err, &status) && (status.Code == 403 || status.Code == 429):
		return nil, &ReleaseError{Message: "Release service rate limit reached. Please try again later."}
	case errors.As(err, &status):
		return nil, &ReleaseError{Message: fmt.Sprintf("Release service returned HTTP %d.", status.Code)}
	case errors.Is(err, ErrUnavailable):
		return nil, &ReleaseError{Message: "Release service is unavailable. Please try again later."}
	}
	return value, err
}

// Summary is release_summary(releases, running, page) with page_size 10. The
// value is the Python dict in its insertion order, holding each rendered
// release's Python values; Render writes it as DRF's JSONRenderer does.
func Summary(releases []Release, running string, page *big.Int) Dict {
	current, known := VersionTuple(running)
	var latest any
	relation := "unknown"
	if len(releases) > 0 {
		latest = releases[0].Map()
	}
	if known {
		relation = "unavailable"
		if len(releases) > 0 {
			target, _ := VersionTuple(releases[0].Tag)
			switch compareVersions(current, target) {
			case -1:
				relation = "behind"
			case 1:
				relation = "ahead"
			default:
				relation = "current"
			}
		}
	}
	pending := []any{}
	for _, release := range releases {
		if version, _ := VersionTuple(release.Tag); known && compareVersions(version, current) > 0 {
			pending = append(pending, release.Map())
		}
	}
	size := big.NewInt(10)
	end := new(big.Int).Mul(page, size)
	start := new(big.Int).Sub(end, size)
	total := big.NewInt(int64(len(releases)))
	history := []any{}
	if start.Cmp(total) < 0 {
		limit := end
		if limit.Cmp(total) > 0 {
			limit = total
		}
		for i := start.Int64(); i < limit.Int64(); i++ {
			history = append(history, releases[i].Map())
		}
	}
	var next any
	if total.Cmp(end) > 0 {
		next = new(big.Int).Add(page, big.NewInt(1)).Int64()
	}
	return Dict{{Name: "repository", Value: Repository}, {Name: "latest", Value: latest},
		{Name: "relation", Value: relation}, {Name: "pending", Value: pending},
		{Name: "history", Value: history}, {Name: "page", Value: page},
		{Name: "next_page", Value: next}, {Name: "total", Value: len(releases)}}
}

// Render is DRF's JSONRenderer over a summary value:
// json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')),
// then the encoder's U+2028/U+2029 escaping. A NaN or infinite float
// (allow_nan=False) or a str with a lone surrogate (ensure_ascii=False, then
// .encode('utf-8')) is the exception Django lets escape: its 500 page.
func Render(value any) ([]byte, error) {
	out, err := appendReleaseJSON(nil, value)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrReleaseRender, err)
	}
	return out, nil
}

func appendReleaseJSON(out []byte, value any) ([]byte, error) {
	switch v := value.(type) {
	case nil:
		return append(out, "null"...), nil
	case bool:
		if v {
			return append(out, "true"...), nil
		}
		return append(out, "false"...), nil
	case string:
		return appendReleaseJSONString(out, v)
	case *big.Int:
		return append(out, v.String()...), nil
	case int64:
		return strconv.AppendInt(out, v, 10), nil
	case int:
		return strconv.AppendInt(out, int64(v), 10), nil
	case float64:
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return nil, errors.New("Out of range float values are not JSON compliant")
		}
		return append(out, pythonFloatRepr(v)...), nil
	case Dict:
		out = append(out, '{')
		for i, field := range v {
			if i > 0 {
				out = append(out, ',')
			}
			var err error
			if out, err = appendReleaseJSONString(out, field.Name); err != nil {
				return nil, err
			}
			out = append(out, ':')
			if out, err = appendReleaseJSON(out, field.Value); err != nil {
				return nil, err
			}
		}
		return append(out, '}'), nil
	case []any:
		out = append(out, '[')
		for i, item := range v {
			if i > 0 {
				out = append(out, ',')
			}
			var err error
			if out, err = appendReleaseJSON(out, item); err != nil {
				return nil, err
			}
		}
		return append(out, ']'), nil
	}
	return nil, errors.New("unsupported JSON value")
}

// appendReleaseJSONString is json's py_encode_basestring with
// ensure_ascii=False; U+2028 and U+2029 are escaped as DRF does.
func appendReleaseJSONString(out []byte, text string) ([]byte, error) {
	if HasSurrogate(text) {
		return nil, errors.New("lone surrogate (UnicodeEncodeError)")
	}
	const hex = "0123456789abcdef"
	out = append(out, '"')
	for _, r := range text {
		switch r {
		case '"':
			out = append(out, '\\', '"')
		case '\\':
			out = append(out, '\\', '\\')
		case '\n':
			out = append(out, '\\', 'n')
		case '\r':
			out = append(out, '\\', 'r')
		case '\t':
			out = append(out, '\\', 't')
		case '\b':
			out = append(out, '\\', 'b')
		case '\f':
			out = append(out, '\\', 'f')
		case '\u2028', '\u2029':
			out = append(out, '\\', 'u', '2', '0', '2', hex[r&0xf])
		default:
			if r < 0x20 {
				out = append(out, '\\', 'u', '0', '0', hex[r>>4&0xf], hex[r&0xf])
			} else {
				out = utf8.AppendRune(out, r)
			}
		}
	}
	return append(out, '"'), nil
}

// pythonFloatRepr is float.__repr__: the shortest round-trip digits, positional
// for decimal exponents -4..15 and d[.ddd]e+XX with two exponent digits
// otherwise (Python's json encoder writes repr(float)).
func pythonFloatRepr(value float64) string {
	if value == 0 {
		if math.Signbit(value) {
			return "-0.0"
		}
		return "0.0"
	}
	text := strconv.FormatFloat(value, 'e', -1, 64)
	sign := ""
	if text[0] == '-' {
		sign, text = "-", text[1:]
	}
	mantissa, exponentText, _ := strings.Cut(text, "e")
	digits := strings.Replace(mantissa, ".", "", 1)
	exponent, _ := strconv.Atoi(exponentText)
	if exponent < -4 || exponent > 15 {
		result := digits[:1]
		if len(digits) > 1 {
			result += "." + digits[1:]
		}
		mark := "+"
		if exponent < 0 {
			mark, exponent = "-", -exponent
		}
		return sign + result + "e" + mark + leftPad(strconv.Itoa(exponent), 2)
	}
	point := exponent + 1
	switch {
	case point <= 0:
		return sign + "0." + strings.Repeat("0", -point) + digits
	case point >= len(digits):
		return sign + digits + strings.Repeat("0", point-len(digits)) + ".0"
	default:
		return sign + digits[:point] + "." + digits[point:]
	}
}

func leftPad(value string, width int) string {
	if len(value) >= width {
		return value
	}
	return strings.Repeat("0", width-len(value)) + value
}
