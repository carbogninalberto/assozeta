package instanceprobe

import (
	"context"
	"fmt"
)

// FetchRelease is release_catalog.fetch_release(release_id) for a validated
// positive id: read_json of the release, then normalize_release; a skipped
// release is a ReleaseError.
func (c *Client) FetchRelease(ctx context.Context, id int64) (*Release, error) {
	value, err := c.readRelease(ctx, fmt.Sprintf("%s/releases/%d", ReleasesAPI, id))
	if err != nil {
		return nil, err
	}
	release, err := normalize(value)
	if err != nil {
		return nil, err
	}
	if release == nil {
		return nil, &ReleaseError{Message: "The selected release is not a published stable release."}
	}
	return release, nil
}

// VersionAtMost reports version_tuple(tag) <= version_tuple(running); ok is
// false when running is not a stable version (version_tuple is None).
func VersionAtMost(tag, running string) (atMost, ok bool) {
	current, ok := VersionTuple(running)
	if !ok {
		return false, false
	}
	candidate, valid := VersionTuple(tag)
	if !valid {
		return false, false
	}
	return compareVersions(candidate, current) <= 0, true
}
