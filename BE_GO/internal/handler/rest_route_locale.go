package handler

import (
	"net/http"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// readRouteLocale is intentionally limited to the two characterized read
// handlers. Other REST routes retain their existing transport locale behavior.
type readRouteLocale interface {
	Language(*http.Request) string
	Text(*http.Request, readRouteMessage) string
}

type readRouteMessage string

const (
	readRouteCredentials readRouteMessage = "credentials"
	readRoutePermission  readRouteMessage = "permission"
	readRouteAccept      readRouteMessage = "accept"
	readRouteNotFound    readRouteMessage = "not_found"
	readRouteMethod      readRouteMessage = "method"
	readRouteMedia       readRouteMessage = "media"
)

type djangoReadRouteLocale struct{}

var handlerReadRouteLocale readRouteLocale = djangoReadRouteLocale{}

const (
	readRouteDefaultLanguage = "it"
	readRouteMaxHeaderChars  = 500
)

// This character class is CPython's Unicode \s for the pinned reference:
// ASCII whitespace and control separators, NEL, and the Unicode Z categories.
// Django parses the Latin-1-decoded ASGI string with this expression.
const readRoutePythonWhitespace = `[\x09-\x0D\x1C-\x1F\x{85}\p{Z}]`

// Mirrors Django 5.2 accept_language_re. The full header is parsed
// transactionally: one malformed range rejects all ranges, matching Django.
var readRouteLanguageRange = regexp.MustCompile(
	`^([a-z]{1,8}(?:-[a-z0-9]{1,8})*|\*)(?:` + readRoutePythonWhitespace + `*;` +
		readRoutePythonWhitespace + `*q=(0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?))?(?:` +
		readRoutePythonWhitespace + `*,` + readRoutePythonWhitespace + `*|$)`,
)

var readRouteLanguages = []string{
	"af", "ar", "ar-dz", "ast", "az", "bg", "be", "bn", "br", "bs",
	"ca", "ckb", "cs", "cy", "da", "de", "dsb", "el", "en", "en-au",
	"en-gb", "eo", "es", "es-ar", "es-co", "es-mx", "es-ni", "es-ve",
	"et", "eu", "fa", "fi", "fr", "fy", "ga", "gd", "gl", "he", "hi",
	"hr", "hsb", "hu", "hy", "ia", "id", "ig", "io", "is", "it", "ja",
	"ka", "kab", "kk", "km", "kn", "ko", "ky", "lb", "lt", "lv", "mk",
	"ml", "mn", "mr", "ms", "my", "nb", "ne", "nl", "nn", "os", "pa",
	"pl", "pt", "pt-br", "ro", "ru", "sk", "sl", "sq", "sr", "sr-latn",
	"sv", "sw", "ta", "te", "tg", "th", "tk", "tr", "tt", "udm", "ug",
	"uk", "ur", "uz", "vi", "zh-hans", "zh-hant",
}

var readRouteLanguageSet = func() map[string]struct{} {
	set := make(map[string]struct{}, len(readRouteLanguages))
	for _, language := range readRouteLanguages {
		set[language] = struct{}{}
	}
	return set
}()

func (djangoReadRouteLocale) Language(r *http.Request) string {
	if language, ok := readRouteSupportedVariant(readRouteLanguageCookie(r)); ok {
		return language
	}
	for _, preference := range readRouteLanguagePreferences(r.Header.Values("Accept-Language")) {
		if preference.language == "*" {
			break
		}
		if language, ok := readRouteSupportedVariant(preference.language); ok {
			return language
		}
	}
	return readRouteDefaultLanguage
}

func (djangoReadRouteLocale) Text(r *http.Request, kind readRouteMessage) string {
	messages := readRouteMessagesForLanguage(djangoReadRouteLocale{}.Language(r))
	var text string
	switch kind {
	case readRouteCredentials:
		text = messages.credentials
	case readRoutePermission:
		text = messages.permission
	case readRouteAccept:
		text = messages.accept
	case readRouteNotFound:
		text = messages.notFound
	case readRouteMethod:
		text = messages.method
	case readRouteMedia:
		text = messages.media
	}
	return strings.NewReplacer(
		"{method}", r.Method,
		"{media_type}", r.Header.Get("Content-Type"),
	).Replace(text)
}

type readRouteLanguagePreference struct {
	language string
	quality  float64
}

func readRouteLanguagePreferences(values []string) []readRouteLanguagePreference {
	header := readRouteDjangoHeaderString(strings.Join(values, ","))
	if utf8.RuneCountInString(header) > readRouteMaxHeaderChars {
		prefix := readRouteRunePrefix(header, readRouteMaxHeaderChars)
		end := strings.LastIndex(prefix, ",")
		if end <= 0 {
			return nil
		}
		header = prefix[:end]
	}
	header = strings.ToLower(header)
	preferences := make([]readRouteLanguagePreference, 0, strings.Count(header, ",")+1)
	for header != "" {
		match := readRouteLanguageRange.FindStringSubmatch(header)
		if match == nil {
			return nil
		}
		quality := 1.0
		if match[2] != "" {
			quality, _ = strconv.ParseFloat(match[2], 64)
		}
		preferences = append(preferences, readRouteLanguagePreference{match[1], quality})
		header = header[len(match[0]):]
	}
	sort.SliceStable(preferences, func(i, j int) bool {
		return preferences[i].quality > preferences[j].quality
	})
	return preferences
}

func readRouteSupportedVariant(code string) (string, bool) {
	code = strings.ToLower(code)
	if code == "" {
		return "", false
	}
	if utf8.RuneCountInString(code) > readRouteMaxHeaderChars {
		prefix := readRouteRunePrefix(code, readRouteMaxHeaderChars)
		end := strings.LastIndex(prefix, "-")
		if end <= 0 {
			return "", false
		}
		code = prefix[:end]
	}

	possible := []string{code}
	switch code {
	case "zh-cn", "zh-my", "zh-sg":
		possible = append(possible, "zh-hans")
	case "zh-hk", "zh-mo", "zh-tw":
		possible = append(possible, "zh-hant")
	}
	for i := len(code); ; {
		i = strings.LastIndex(code[:i], "-")
		if i < 0 {
			break
		}
		possible = append(possible, code[:i])
	}
	for _, candidate := range possible {
		if _, ok := readRouteLanguageSet[candidate]; ok {
			return candidate, true
		}
	}
	generic := possible[len(possible)-1]
	for _, language := range readRouteLanguages {
		if strings.HasPrefix(language, generic+"-") {
			return language, true
		}
	}
	return "", false
}

// Django parse_cookie assigns duplicate names in order, including an invalid
// final value; net/http.Request.Cookie instead returns the first occurrence.
func readRouteLanguageCookie(r *http.Request) string {
	value := ""
	cookie := readRouteDjangoHeaderString(strings.Join(r.Header.Values("Cookie"), ","))
	for _, part := range strings.Split(cookie, ";") {
		key, candidate, found := strings.Cut(part, "=")
		if !found || readRouteTrimPythonSpace(key) != "django_language" {
			continue
		}
		value = readRouteTrimPythonSpace(candidate)
		if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
			quoted := value[1 : len(value)-1]
			decoded := make([]byte, 0, len(quoted))
			for i := 0; i < len(quoted); i++ {
				if quoted[i] == '\\' && i+1 < len(quoted) {
					if i+3 < len(quoted) && quoted[i+1] >= '0' && quoted[i+1] <= '3' &&
						quoted[i+2] >= '0' && quoted[i+2] <= '7' && quoted[i+3] >= '0' && quoted[i+3] <= '7' {
						decoded = append(decoded, (quoted[i+1]-'0')*64+(quoted[i+2]-'0')*8+quoted[i+3]-'0')
						i += 3
						continue
					}
					i++
				}
				decoded = append(decoded, quoted[i])
			}
			value = string(decoded)
		}
	}
	return value
}

// Django's ASGI handler decodes each raw header byte as one Latin-1 code
// point. Go strings retain transport bytes, so convert only for this parser.
func readRouteDjangoHeaderString(raw string) string {
	for i := 0; i < len(raw); i++ {
		if raw[i] < utf8.RuneSelf {
			continue
		}
		var decoded strings.Builder
		decoded.Grow(len(raw) + 1)
		for j := 0; j < len(raw); j++ {
			decoded.WriteRune(rune(raw[j]))
		}
		return decoded.String()
	}
	return raw
}

func readRouteRunePrefix(value string, count int) string {
	position := 0
	for byteIndex := range value {
		if position == count {
			return value[:byteIndex]
		}
		position++
	}
	return value
}

// Python's str.strip() additionally treats U+001C through U+001F as
// whitespace; Go's unicode.IsSpace covers the remaining Python whitespace.
func readRouteTrimPythonSpace(value string) string {
	return strings.TrimFunc(value, func(r rune) bool {
		return ('\u001c' <= r && r <= '\u001f') || unicode.IsSpace(r)
	})
}
