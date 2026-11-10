package instanceprobe

import (
	"context"
	"errors"
	"fmt"
	"math/big"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"unicode"

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

// Release is one normalize_release dict. NameIsTag records that name is
// the tag object itself (raw name falsy), which pickle memoizes as one
// string.
type Release struct {
	ID                int64
	Tag, Version      string
	Name              string
	NameIsTag         bool
	PublishedAt       string
	Notes             string
	URL               string
	ArtifactsReady    bool
	UnavailableReason *string
}

// Map is the release as Django renders it.
func (r Release) Map() map[string]any {
	var reason any
	if r.UnavailableReason != nil {
		reason = *r.UnavailableReason
	}
	return map[string]any{"id": r.ID, "tag": r.Tag, "version": r.Version, "name": r.Name, "published_at": r.PublishedAt,
		"notes": r.Notes, "url": r.URL, "artifacts_ready": r.ArtifactsReady, "unavailable_reason": reason}
}

// ReleaseError is release_catalog.ReleaseError (the view's 503 message).
type ReleaseError struct{ Message string }

func (e *ReleaseError) Error() string { return e.Message }

// ErrReleaseNotPorted is a feed Django would answer with a server error
// (an exception the view does not catch) or values this port does not
// carry (non-integer ids, non-string names/dates/notes, lone surrogates).
var ErrReleaseNotPorted = refusal.New("instanceprobe: release feed not ported")

// quoteTag is urllib.parse.quote(tag, safe="") for a stable tag (only
// letters, digits and dots, which quote keeps).
func quoteTag(tag string) string { return url.PathEscape(tag) }

// normalize is normalize_release(raw); nil, nil for a skipped release.
func normalize(item any) (*Release, error) {
	raw, ok := item.(map[string]any)
	if !ok {
		return nil, ErrReleaseNotPorted // raw.get: AttributeError
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
				return nil, ErrReleaseNotPorted
			}
			name, isString := asset["name"].(string)
			if !isString {
				if _, unhashable := asset["name"].([]any); unhashable {
					return nil, ErrReleaseNotPorted
				}
				if _, unhashable := asset["name"].(map[string]any); unhashable {
					return nil, ErrReleaseNotPorted
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
		return nil, ErrReleaseNotPorted // raw['id']: KeyError
	}
	id, ok := idValue.(Int)
	if !ok {
		return nil, ErrReleaseNotPorted
	}
	number, err := strconv.ParseInt(string(id), 10, 64)
	if err != nil {
		return nil, ErrReleaseNotPorted
	}
	version, _ := VersionTuple(tag)
	release := &Release{ID: number, Tag: tag, Version: fmt.Sprintf("%s.%s.%s", version[0], version[1], version[2]),
		URL: ReleasesURL + "/tag/" + quoteTag(tag), ArtifactsReady: ready}
	if name := raw["name"]; Truthy(name) {
		text, ok := name.(string)
		if !ok {
			return nil, ErrReleaseNotPorted
		}
		release.Name = text
	} else {
		release.Name, release.NameIsTag = tag, true
	}
	published, ok := raw["published_at"].(string)
	if !ok {
		return nil, ErrReleaseNotPorted
	}
	release.PublishedAt = published
	if body := raw["body"]; Truthy(body) {
		text, ok := body.(string)
		if !ok {
			return nil, ErrReleaseNotPorted
		}
		release.Notes = text
	}
	if !ready {
		reason := UnavailableReason
		release.UnavailableReason = &reason
	}
	for _, text := range []string{release.Tag, release.Name, release.PublishedAt, release.Notes} {
		if HasSurrogate(text) {
			return nil, ErrReleaseNotPorted
		}
	}
	return release, nil
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
	return nil, ErrReleaseNotPorted
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
	return false, ErrReleaseNotPorted
}

// ReleaseSet is fetch_releases' dict of normalized releases by id (first
// position, last value).
type ReleaseSet struct {
	order []int64
	byID  map[int64]Release
}

// Add normalizes one page's items.
func (r *ReleaseSet) Add(items []any) error {
	if r.byID == nil {
		r.byID = map[int64]Release{}
	}
	for _, item := range items {
		release, err := normalize(item)
		if err != nil {
			return err
		}
		if release == nil {
			continue
		}
		if _, seen := r.byID[release.ID]; !seen {
			r.order = append(r.order, release.ID)
		}
		r.byID[release.ID] = *release
	}
	return nil
}

// Releases is the sorted catalog.
func (r *ReleaseSet) Releases() []Release {
	releases := make([]Release, 0, len(r.order))
	for _, id := range r.order {
		releases = append(releases, r.byID[id])
	}
	SortReleases(releases)
	return releases
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

// Summary is release_summary(releases, running, page) with page_size 10.
func Summary(releases []Release, running string, page *big.Int) map[string]any {
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
	return map[string]any{"repository": Repository, "latest": latest, "relation": relation, "pending": pending,
		"history": history, "page": jsonInt{page}, "next_page": next, "total": len(releases)}
}

// jsonInt renders a Python int of any size as a JSON number.
type jsonInt struct{ *big.Int }

func (n jsonInt) MarshalJSON() ([]byte, error) { return []byte(n.String()), nil }
