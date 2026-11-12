package s3credentials

import (
	"io"
	"os"
	"os/user"
	"path/filepath"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

func sharedFile(variable, name string) (string, error) {
	value, present := os.LookupEnv(variable)
	if !present {
		value = "~/.aws/" + name
	}
	path := expandSharedPath(value)
	if info, err := os.Stat(path); err == nil && !info.Mode().IsRegular() {
		// Boto3 does not open directories/devices/FIFOs. An explicit empty SDK
		// filename also prevents fallback to the user's default home profile.
		return "", nil
	}
	return path, nil
}

var pathVariable = regexp.MustCompile(`\$(?:[\pL\pN_]+|\{[^}]*\})`)

// Botocore expands variables (retaining unknown names) and then user prefixes.
func expandSharedPath(value string) string {
	value = pathVariable.ReplaceAllStringFunc(value, func(token string) string {
		name := token[1:]
		if strings.HasPrefix(name, "{") {
			name = name[1 : len(name)-1]
		}
		if resolved, present := os.LookupEnv(name); present {
			return resolved
		}
		return token
	})
	if !strings.HasPrefix(value, "~") {
		return value
	}
	name, rest, slash := strings.Cut(value[1:], "/")
	var home string
	if name == "" {
		var present bool
		home, present = os.LookupEnv("HOME")
		if !present {
			if current, err := user.Current(); err == nil {
				home = current.HomeDir
			} else {
				return value
			}
		}
	} else {
		if account, err := user.Lookup(name); err == nil {
			home = account.HomeDir
		} else {
			return value
		}
	}
	if slash {
		trimmed := strings.TrimRight(home, "/")
		if trimmed == "" {
			return string(filepath.Separator)
		}
		return trimmed + "/" + rest
	}
	if home == "" {
		return string(filepath.Separator)
	}
	trimmed := strings.TrimRight(home, "/")
	if trimmed == "" {
		return string(filepath.Separator)
	}
	return trimmed
}

// The SDK INI reader silently ignores malformed lines and overwrites duplicate
// declarations. Boto3's RawConfigParser rejects them before selecting identity.
// Validate the shared-file structure before handing provider resolution to SDK.
// Files are trusted deployment inputs, capped at 1 MiB each for startup bounds.
func readSharedFile(path string) (map[string]map[string]string, error) {
	info, err := os.Stat(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, ErrConfiguration
	}
	if !info.Mode().IsRegular() {
		return nil, nil
	}
	if info.Size() > 1<<20 {
		return nil, ErrConfiguration
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, ErrConfiguration
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, (1<<20)+1))
	if err != nil || len(data) > 1<<20 || !utf8.Valid(data) {
		return nil, ErrConfiguration
	}
	text := strings.ReplaceAll(strings.ReplaceAll(string(data), "\r\n", "\n"), "\r", "\n")
	sections := map[string]map[string]string{}
	current := ""
	hasSection, hasOption := false, false
	indentation := 0
	option := ""
	for _, line := range strings.Split(text, "\n") {
		stripped := strings.TrimSpace(line)
		if stripped == "" || strings.HasPrefix(stripped, "#") || strings.HasPrefix(stripped, ";") {
			continue
		}
		indent := 0
		for _, r := range line {
			if !unicode.IsSpace(r) {
				break
			}
			indent++
		}
		if hasSection && hasOption && indent > indentation {
			sections[current][option] += "\n" + stripped
			continue
		}
		indentation = indent
		if strings.HasPrefix(stripped, "[") {
			end := strings.LastIndexByte(stripped, ']')
			if end > 1 {
				current = stripped[1:end]
				if _, exists := sections[current]; exists && current != "DEFAULT" {
					return nil, ErrConfiguration
				}
				if sections[current] == nil {
					sections[current] = map[string]string{}
				}
				hasSection, hasOption = true, false
				continue
			}
		}
		if !hasSection {
			return nil, ErrConfiguration
		}
		delimiter := strings.IndexAny(stripped, "=:")
		if delimiter < 0 {
			return nil, ErrConfiguration
		}
		key := strings.ToLower(strings.TrimSpace(stripped[:delimiter]))
		if _, exists := sections[current][key]; key == "" || exists {
			return nil, ErrConfiguration
		}
		sections[current][key] = strings.TrimSpace(stripped[delimiter+1:])
		option = key
		hasOption = true
	}
	return sections, nil
}
