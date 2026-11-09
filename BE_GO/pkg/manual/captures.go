package manual

import (
	"bytes"
	"encoding/binary"
	"errors"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// CaptureCommandError is a CommandError raised by compare_manuale_captures.
// Every other returned error corresponds to an uncaught Python exception.
type CaptureCommandError struct{ Message string }

func (e CaptureCommandError) Error() string { return e.Message }

// ErrCaptureImageUnsupported refuses images whose RGBA conversion is not
// reproduced: formats other than PNG, 16-bit samples, grayscale below 8 bits
// and palette indices beyond the PLTE entries.
var ErrCaptureImageUnsupported = errors.New("capture image format is not supported by the Go comparison")

var errCaptureUncaught = errors.New("capture comparison failed")

const capturesMaximumChangedFraction = .001

// Django's INPUTS: equal values make a drifted screenshot a failure.
var capturesInputs = []string{"application_revision", "source_hashes", "tooling_hashes", "fixture_version",
	"reference_date", "viewport", "locale", "timezone", "theme", "device_scale_factor", "backend",
	"fixture_profile", "capture_format"}

// CompareCaptures is compare_manuale_captures: it compares every report in
// <run>/capture-baseline with the same-named current report in <run>, writes
// the variance report to output and returns the number of checkpoints.
func CompareCaptures(run, output string) (int, error) {
	baseline := filepath.Join(run, "capture-baseline")
	names, err := capturesReports(baseline)
	if err != nil {
		return 0, err
	}
	results := []any{}
	failed := false
	for _, name := range names {
		old, err := capturesReadReport(filepath.Join(baseline, name))
		if err != nil {
			return 0, err
		}
		current, err := capturesReadReport(filepath.Join(run, name))
		if err != nil {
			return 0, err
		}
		oldStatus, err := capturesItem(old, "status")
		if err != nil {
			return 0, err
		}
		if !capturesEqual(oldStatus, "passed", false) {
			return 0, CaptureCommandError{"Variance comparison requires passed real captures"}
		}
		currentStatus, err := capturesItem(current, "status")
		if err != nil {
			return 0, err
		}
		if !capturesEqual(currentStatus, "passed", false) {
			return 0, CaptureCommandError{"Variance comparison requires passed real captures"}
		}
		oldReport, currentReport := old.(*capturesObject), current.(*capturesObject)
		comparable := true
		for _, key := range capturesInputs {
			left, _ := oldReport.get(key)
			right, _ := currentReport.get(key)
			if !capturesEqual(left, right, false) {
				comparable = false
				break
			}
		}
		before, err := capturesArtifacts(oldReport)
		if err != nil {
			return 0, err
		}
		after, err := capturesArtifacts(currentReport)
		if err != nil {
			return 0, err
		}
		relatives := []string{}
		for key := range before {
			relatives = append(relatives, key)
		}
		for key := range after {
			if _, ok := before[key]; !ok {
				relatives = append(relatives, key)
			}
		}
		sort.Strings(relatives)
		for _, relative := range relatives {
			scenario, ok := currentReport.get("id")
			if !ok {
				return 0, errCaptureUncaught
			}
			entry := newCapturesObject()
			entry.set("scenario", scenario)
			entry.set("path", relative)
			entry.set("identical_inputs", comparable)
			previousRecord, inBefore := before[relative]
			latestRecord, inAfter := after[relative]
			if !inBefore || !inAfter {
				entry.set("status", "checkpoint-changed")
				failed = failed || comparable
				results = append(results, entry)
				continue
			}
			previous, err := capturesOwnedPath(filepath.Join(run, "capture-baseline"), relative)
			if err != nil {
				return 0, err
			}
			latest, err := capturesOwnedPath(run, relative)
			if err != nil {
				return 0, err
			}
			previousDigest, previousOK := previousRecord.get("sha256")
			latestDigest, latestOK := latestRecord.get("sha256")
			contents, err := os.ReadFile(previous)
			if err != nil {
				return 0, err
			}
			if !previousOK {
				return 0, errCaptureUncaught
			}
			if !capturesEqual(hash(contents), previousDigest, false) {
				return 0, CaptureCommandError{"Capture contents differ from their recorded hashes"}
			}
			later, err := os.ReadFile(latest)
			if err != nil {
				return 0, err
			}
			if !latestOK {
				return 0, errCaptureUncaught
			}
			if !capturesEqual(hash(later), latestDigest, false) {
				return 0, CaptureCommandError{"Capture contents differ from their recorded hashes"}
			}
			first, err := capturesRGBA(contents)
			if err != nil {
				return 0, err
			}
			second, err := capturesRGBA(later)
			if err != nil {
				return 0, err
			}
			if first.Rect.Dx() != second.Rect.Dx() || first.Rect.Dy() != second.Rect.Dy() {
				entry.set("status", "dimensions-changed")
				failed = failed || comparable
			} else {
				changed, difference := 0, 0
				for i := 0; i < len(first.Pix); i += 4 {
					pixel := false
					for c := 0; c < 4; c++ {
						a, b := int(first.Pix[i+c]), int(second.Pix[i+c])
						if a != b {
							pixel = true
							if a > b {
								difference += a - b
							} else {
								difference += b - a
							}
						}
					}
					if pixel {
						changed++
					}
				}
				// NumPy sums these integer counts exactly in float64, then divides.
				pixels := len(first.Pix) / 4
				fraction := float64(changed) / float64(pixels)
				delta := float64(difference) / float64(len(first.Pix)) / 255
				status := "stable"
				if fraction > capturesMaximumChangedFraction {
					status = "changed"
				}
				entry.set("status", status)
				entry.set("changed_pixel_fraction", capturesRound(fraction, 8))
				entry.set("mean_channel_difference", capturesRound(delta, 8))
				entry.set("identical_bytes", previousDigest == latestDigest)
				failed = failed || comparable && fraction > capturesMaximumChangedFraction
			}
			results = append(results, entry)
		}
	}
	report := newCapturesObject()
	if failed {
		report.set("status", "failed")
	} else {
		report.set("status", "passed")
	}
	report.set("maximum_changed_pixel_fraction", capturesMaximumChangedFraction)
	report.set("screenshots", results)
	if err := os.WriteFile(output, []byte(capturesDumps(report)+"\n"), 0666); err != nil {
		return 0, err
	}
	if failed {
		return 0, CaptureCommandError{"Screenshots drifted under identical inputs; previous verified manual/index retained."}
	}
	return len(results), nil
}

// capturesReports is sorted(Path(baseline).glob('*.json')): hidden entries,
// directories and dangling links included, case-sensitive, missing directory empty.
func capturesReports(baseline string) ([]string, error) {
	entries, err := os.ReadDir(baseline)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) || errors.Is(err, errors.ErrUnsupported) {
			return nil, nil
		}
		var pathError *os.PathError
		if errors.As(err, &pathError) {
			// pathlib ignores directories it cannot list.
			return nil, nil
		}
		return nil, err
	}
	names := []string{}
	for _, entry := range entries {
		if strings.HasSuffix(entry.Name(), ".json") {
			names = append(names, entry.Name())
		}
	}
	sort.Strings(names)
	return names, nil
}

func capturesReadReport(path string) (any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return capturesDecode(raw)
}

// capturesItem is report[key] on a decoded JSON document.
func capturesItem(value any, key string) (any, error) {
	object, ok := value.(*capturesObject)
	if !ok {
		return nil, errCaptureUncaught
	}
	item, ok := object.get(key)
	if !ok {
		return nil, errCaptureUncaught
	}
	return item, nil
}

// capturesArtifacts mirrors the nested artifacts(report) helper: capture
// paths under captures/ (pathlib-normalized), then master paths as written.
func capturesArtifacts(report *capturesObject) (map[string]*capturesObject, error) {
	raw, ok := report.get("screenshots")
	if !ok {
		return nil, errCaptureUncaught
	}
	captures, ok := raw.([]any)
	if !ok {
		// Iterating a string or an object yields strings, which cannot be indexed by 'path'.
		if object, isObject := raw.(*capturesObject); isObject && len(object.keys) == 0 {
			return map[string]*capturesObject{}, nil
		}
		if text, isText := raw.(string); isText && text == "" {
			return map[string]*capturesObject{}, nil
		}
		return nil, errCaptureUncaught
	}
	result := map[string]*capturesObject{}
	for _, item := range captures {
		capture, ok := item.(*capturesObject)
		if !ok {
			return nil, errCaptureUncaught
		}
		path, ok := capture.get("path")
		if !ok {
			return nil, errCaptureUncaught
		}
		text, ok := path.(string)
		if !ok {
			return nil, errCaptureUncaught
		}
		result[capturesPurePath("captures", text)] = capture
	}
	for _, item := range captures {
		capture := item.(*capturesObject)
		master, _ := capture.get("master")
		if !capturesTruthy(master) {
			continue
		}
		record, ok := master.(*capturesObject)
		if !ok {
			return nil, errCaptureUncaught
		}
		path, ok := record.get("path")
		if !ok {
			return nil, errCaptureUncaught
		}
		// A non-string key cannot be sorted with the string capture paths.
		text, ok := path.(string)
		if !ok {
			return nil, errCaptureUncaught
		}
		result[text] = record
	}
	return result, nil
}

// capturesPurePath is str(PurePosixPath(base) / relative).
func capturesPurePath(base, relative string) string {
	path := base + "/" + relative
	root := ""
	if strings.HasPrefix(relative, "/") {
		path = relative
		root = "/"
		if strings.HasPrefix(relative, "//") && !strings.HasPrefix(relative, "///") {
			root = "//"
		}
	}
	parts := []string{}
	for _, part := range strings.Split(path, "/") {
		if part != "" && part != "." {
			parts = append(parts, part)
		}
	}
	if root == "" && len(parts) == 0 {
		return "."
	}
	return root + strings.Join(parts, "/")
}

// capturesOwnedPath is owned_path: both paths resolved non-strictly like
// Path.resolve(), then the candidate must lie strictly inside the root.
func capturesOwnedPath(root, relative string) (string, error) {
	resolvedRoot, err := capturesRealpath(root)
	if err != nil {
		return "", err
	}
	candidate := resolvedRoot + "/" + relative
	if strings.HasPrefix(relative, "/") {
		candidate = relative
	}
	path, err := capturesRealpath(candidate)
	if err != nil {
		return "", err
	}
	inside := resolvedRoot == "/" && path != "/" || strings.HasPrefix(path, resolvedRoot+"/")
	if !inside {
		return "", errCaptureUncaught
	}
	return path, nil
}

// capturesRealpath is posixpath.realpath(path, strict=False).
func capturesRealpath(path string) (string, error) {
	if !strings.HasPrefix(path, "/") {
		cwd, err := os.Getwd()
		if err != nil {
			return "", err
		}
		path = cwd + "/" + path
	}
	resolved, _ := capturesJoinRealpath("/", path, map[string]*string{})
	if resolved == "" {
		resolved = "/"
	}
	return filepath.Clean(resolved), nil
}

func capturesJoin(base, name string) string {
	if strings.HasSuffix(base, "/") {
		return base + name
	}
	return base + "/" + name
}

func capturesJoinRealpath(path, rest string, seen map[string]*string) (string, bool) {
	if strings.HasPrefix(rest, "/") {
		rest = rest[1:]
		path = "/"
	}
	for rest != "" {
		var name string
		name, rest, _ = strings.Cut(rest, "/")
		if name == "" || name == "." {
			continue
		}
		if name == ".." {
			if path != "" {
				parent, last := filepath.Split(path)
				if parent != "/" {
					parent = strings.TrimRight(parent, "/")
					if parent == "" {
						parent = "/"
					}
				}
				path = parent
				if last == ".." {
					path = capturesJoin(capturesJoin(path, ".."), "..")
				}
			} else {
				path = ".."
			}
			continue
		}
		next := capturesJoin(path, name)
		info, err := os.Lstat(next)
		if err != nil || info.Mode()&os.ModeSymlink == 0 {
			path = next
			continue
		}
		if known, ok := seen[next]; ok {
			if known != nil {
				path = *known
				continue
			}
			return capturesJoin(next, rest), false
		}
		seen[next] = nil
		target, err := os.Readlink(next)
		if err != nil {
			path = next
			continue
		}
		resolved, ok := capturesJoinRealpath(path, target, seen)
		if !ok {
			return capturesJoin(resolved, rest), false
		}
		path = resolved
		value := resolved
		seen[next] = &value
	}
	return path, true
}

// PIL raises DecompressionBombError above twice MAX_IMAGE_PIXELS.
const capturesMaximumPixels = 2 * 89478485

// capturesRGBA is Image.open(path).convert('RGBA') for the PNG variants whose
// conversion Go reproduces exactly: 8-bit samples of every colour type and
// 1/2/4/8-bit palettes whose indices are all within PLTE.
func capturesRGBA(contents []byte) (*image.NRGBA, error) {
	if len(contents) < 33 || !bytes.Equal(contents[:8], []byte("\x89PNG\r\n\x1a\n")) || string(contents[12:16]) != "IHDR" {
		return nil, ErrCaptureImageUnsupported
	}
	depth, colour := contents[24], contents[25]
	if !(depth == 8 || colour == 3 && (depth == 1 || depth == 2 || depth == 4)) {
		return nil, ErrCaptureImageUnsupported
	}
	config, err := png.DecodeConfig(bytes.NewReader(contents))
	if err != nil {
		return nil, err
	}
	if int64(config.Width)*int64(config.Height) > capturesMaximumPixels {
		return nil, errCaptureUncaught
	}
	decoded, err := png.Decode(bytes.NewReader(contents))
	if err != nil {
		return nil, err
	}
	bounds := decoded.Bounds()
	result := image.NewNRGBA(image.Rect(0, 0, bounds.Dx(), bounds.Dy()))
	if paletted, ok := decoded.(*image.Paletted); ok {
		entries := capturesPaletteEntries(contents)
		for _, index := range paletted.Pix {
			if int(index) >= entries {
				return nil, ErrCaptureImageUnsupported
			}
		}
	}
	for y := 0; y < bounds.Dy(); y++ {
		for x := 0; x < bounds.Dx(); x++ {
			c := color.NRGBAModel.Convert(decoded.At(bounds.Min.X+x, bounds.Min.Y+y)).(color.NRGBA)
			offset := result.PixOffset(x, y)
			result.Pix[offset], result.Pix[offset+1], result.Pix[offset+2], result.Pix[offset+3] = c.R, c.G, c.B, c.A
		}
	}
	return result, nil
}

func capturesPaletteEntries(contents []byte) int {
	for at := 8; at+8 <= len(contents); {
		length := int(binary.BigEndian.Uint32(contents[at:]))
		kind := string(contents[at+4 : at+8])
		if kind == "PLTE" {
			return length / 3
		}
		if length < 0 || at+12+length > len(contents) {
			break
		}
		at += 12 + length
	}
	return 0
}
