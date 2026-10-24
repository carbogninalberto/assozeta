package handler

import (
	"net/http"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// Route-local compatibility for the authored English, Italian and French
// contract. Other translation catalogs remain outside this characterized slice.
var collaboratorLanguageRange = regexp.MustCompile(`^([a-z]{1,8}(?:-[a-z0-9]{1,8})*|\*)(?:\s*;\s*q=(0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?))?(?:\s*,\s*|$)`)

func collaboratorLanguage(r *http.Request) string {
	variant := func(code string) string {
		code = strings.ToLower(code)
		if code == "en-gb" || strings.HasPrefix(code, "en-gb-") {
			return "en-gb"
		}
		if code == "en-au" || strings.HasPrefix(code, "en-au-") {
			return "en-au"
		}
		for _, language := range []string{"en", "it", "fr"} {
			if code == language || strings.HasPrefix(code, language+"-") {
				return language
			}
		}
		return ""
	}
	if code := variant(collaboratorLanguageCookie(r)); code != "" {
		return code
	}

	header := strings.ToLower(strings.Join(r.Header.Values("Accept-Language"), ","))
	if len(header) > 500 {
		if end := strings.LastIndex(header[:500], ","); end > 0 {
			header = header[:end]
		} else {
			return "it"
		}
	}
	type preference struct {
		language string
		quality  float64
	}
	preferences := []preference{}
	for header != "" {
		match := collaboratorLanguageRange.FindStringSubmatch(header)
		if match == nil {
			return "it"
		}
		quality := 1.0
		if match[2] != "" {
			quality, _ = strconv.ParseFloat(match[2], 64)
		}
		preferences = append(preferences, preference{match[1], quality})
		header = header[len(match[0]):]
	}
	sort.SliceStable(preferences, func(i, j int) bool { return preferences[i].quality > preferences[j].quality })
	for _, choice := range preferences {
		if choice.language == "*" {
			break
		}
		if code := variant(choice.language); code != "" {
			return code
		}
	}
	return "it"
}

var collaboratorMessages = map[string]map[string]string{
	"en": {
		"credentials": "Authentication credentials were not provided.",
		"permission":  "You do not have permission to perform this action.",
		"accept":      "Could not satisfy the request Accept header.",
		"notfound":    "Not found.",
		"method":      "Method \"%s\" not allowed.",
		"media":       "Unsupported media type \"%s\" in request.",
	},
	"it": {
		"credentials": "Non sono state immesse le credenziali di autenticazione.",
		"permission":  "Non hai l'autorizzazione per eseguire questa azione.",
		"accept":      "Impossibile soddisfare l'header \"Accept\" presente nella richiesta.",
		"notfound":    "Non trovato.",
		"method":      "Metodo \"%s\" non consentito",
		"media":       "Tipo di media \"%s\"non supportato.",
	},
	"fr": {
		"credentials": "Informations d'authentification non fournies.",
		"permission":  "Vous n'avez pas la permission d'effectuer cette action.",
		"accept":      "L'en-tête « Accept » n'a pas pu être satisfaite.",
		"notfound":    "Non trouvé.",
		"method":      "Méthode « %s » non autorisée.",
		"media":       "Type de média « %s » non supporté.",
	},
}

func collaboratorText(r *http.Request, kind string) string {
	language := strings.Split(collaboratorLanguage(r), "-")[0]
	text := collaboratorMessages[language][kind]
	if kind == "method" {
		text = strings.Replace(text, "%s", r.Method, 1)
	}
	if kind == "media" {
		text = strings.Replace(text, "%s", r.Header.Get("Content-Type"), 1)
	}
	return text
}

// Django parse_cookie assigns duplicate names in order, including an invalid
// final value; net/http.Request.Cookie instead returns the first occurrence.
func collaboratorLanguageCookie(r *http.Request) string {
	value := ""
	for _, part := range strings.Split(strings.Join(r.Header.Values("Cookie"), ","), ";") {
		key, candidate, found := strings.Cut(part, "=")
		if !found || strings.TrimSpace(key) != "django_language" {
			continue
		}
		value = strings.TrimSpace(candidate)
		if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
			quoted := value[1 : len(value)-1]
			decoded := make([]byte, 0, len(quoted))
			for i := 0; i < len(quoted); i++ {
				if quoted[i] == '\\' && i+1 < len(quoted) {
					if i+3 < len(quoted) && quoted[i+1] >= '0' && quoted[i+1] <= '3' && quoted[i+2] >= '0' && quoted[i+2] <= '7' && quoted[i+3] >= '0' && quoted[i+3] <= '7' {
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
