package documentkey

import (
	"context"
	"crypto/rand"
	"strings"
	"unicode/utf8"
)

// AvailableName is S3Storage.get_available_name(name) with file_overwrite
// off: name itself while exists(name) is false, otherwise
// os.path.join(dir_name, file_root + "_" + get_random_string(7) + file_ext)
// until exists is false, where file_ext is
// "".join(PurePath(file_name).suffixes). exists receives the candidate name
// (the caller maps it to the object key).
func AvailableName(ctx context.Context, name string, exists func(context.Context, string) (bool, error), random func() string) (string, error) {
	if !utf8.ValidString(name) || strings.ContainsRune(name, 0) {
		return "", ErrStoredName
	}
	// storages.utils.clean_name, as S3Key applies it.
	cleaned := strings.ReplaceAll(normpath(name), `\`, "/")
	if strings.HasSuffix(name, "/") && !strings.HasSuffix(cleaned, "/") {
		cleaned += "/"
	}
	// clean_name: normpath's "." stands for the empty string.
	if cleaned == "." {
		cleaned = ""
	}
	dirName, fileName := pythonSplit(cleaned)
	for _, part := range strings.Split(dirName, "/") {
		if part == ".." {
			return "", &SuspiciousFileError{Message: "Detected path traversal attempt in '" + dirName + "'"}
		}
	}
	// django.core.files.utils.validate_file_name.
	if fileName == "" || fileName == "." || fileName == ".." {
		return "", &SuspiciousFileError{Message: "Could not derive file name from '" + fileName + "'"}
	}
	fileExt, fileRoot := pythonSuffixes(fileName)
	if random == nil {
		random = RandomName7
	}
	candidate := cleaned
	taken, err := exists(ctx, candidate)
	if err != nil {
		return "", err
	}
	for taken {
		candidate = pythonJoin(dirName, fileRoot+"_"+random()+fileExt)
		if taken, err = exists(ctx, candidate); err != nil {
			return "", err
		}
	}
	return candidate, nil
}

// pythonSplit is os.path.split: the last '/' separates the two parts, and the
// directory keeps trailing slashes only when it consists of nothing else.
func pythonSplit(name string) (dirName, fileName string) {
	i := strings.LastIndex(name, "/")
	if i < 0 {
		return "", name
	}
	dirName = name[:i+1]
	if strings.Trim(dirName, "/") != "" {
		dirName = strings.TrimRight(dirName, "/")
	}
	return dirName, name[i+1:]
}

// pythonSuffixes is file_name's PurePath(file_name).suffixes joined and the
// file name without them: a name ending in '.' has no suffixes, and every
// part after the first (leading dots stripped) is a suffix with its dot.
func pythonSuffixes(fileName string) (fileExt, fileRoot string) {
	if !strings.HasSuffix(fileName, ".") {
		trimmed := strings.TrimLeft(fileName, ".")
		if i := strings.Index(trimmed, "."); i >= 0 {
			fileExt = trimmed[i:]
		}
	}
	return fileExt, strings.TrimSuffix(fileName, fileExt)
}

// pythonJoin is os.path.join(dir_name, name) for a relative name.
func pythonJoin(dirName, name string) string {
	switch {
	case dirName == "":
		return name
	case strings.HasSuffix(dirName, "/"):
		return dirName + name
	}
	return dirName + "/" + name
}

// randomNameAlphabet is django.utils.crypto.RANDOM_STRING_CHARS.
const randomNameAlphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

// RandomName7 is get_random_string(7): seven characters drawn with crypto/rand
// from a-z, A-Z and 0-9.
func RandomName7() string {
	var out [7]byte
	var b [1]byte
	for i := range out {
		for {
			if _, err := rand.Read(b[:]); err != nil {
				// crypto/rand.Read does not fail on supported platforms.
				panic(err)
			}
			// Rejection sampling, as secrets.choice: 248 = 4*62 values.
			if b[0] < 248 {
				out[i] = randomNameAlphabet[int(b[0])%len(randomNameAlphabet)]
				break
			}
		}
	}
	return string(out[:])
}
