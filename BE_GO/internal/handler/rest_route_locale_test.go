package handler

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestDjangoReadRouteLocaleSupportsEveryInheritedLanguage(t *testing.T) {
	languages := []string{
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
	if len(languages) != 99 {
		t.Fatalf("test matrix has %d languages", len(languages))
	}
	for _, language := range languages {
		r := httptest.NewRequest(http.MethodGet, "/", nil)
		r.Header.Set("Accept-Language", language)
		if got := handlerReadRouteLocale.Language(r); got != language {
			t.Errorf("%s selected %q", language, got)
		}
	}
}

func TestDjangoReadRouteLocaleSelectionCompatibility(t *testing.T) {
	tests := []struct {
		name, header, cookie, cookieHeader, want string
		duplicateHeader                          []string
	}{
		{name: "default", want: "it"},
		{name: "exact variant", header: "ES-ar", want: "es-ar"},
		{name: "unsupported region", header: "fr-CA", want: "fr"},
		{name: "regional catalog", header: "en-CA", want: "en"},
		{name: "traditional alias", header: "zh-TW", want: "zh-hant"},
		{name: "simplified alias", header: "zh-SG", want: "zh-hans"},
		{name: "coinvariant regional", header: "en-XX", want: "en"},
		{name: "quality stable order", header: "de;q=0.5,ja;q=0.5,ru;q=0.1", want: "de"},
		{name: "zero quality", header: "de;q=0,ru;q=0", want: "de"},
		{name: "unknown before supported", header: "xx,nl", want: "nl"},
		{name: "wildcard stops", header: "*;q=1,en;q=0.5", want: "it"},
		{name: "case-insensitive q", header: "ja;Q=0.9,de", want: "de"},
		{name: "leading space invalid", header: " de", want: "it"},
		{name: "trailing space invalid", header: "de ", want: "it"},
		{name: "trailing comma", header: "de,", want: "de"},
		{name: "invalid quality whole header", header: "de;q=1.001,en", want: "it"},
		{name: "cookie over header", header: "de", cookie: "ja", want: "ja"},
		{name: "duplicate cookie last", header: "de", cookie: "ja; django_language=es-AR", want: "es-ar"},
		{name: "invalid cookie header fallback", header: "pt-BR", cookie: "not-a-language", want: "pt-br"},
		{name: "quoted octal cookie", header: "en", cookie: `"\152\141"`, want: "ja"},
		{name: "duplicate header values", duplicateHeader: []string{"ja;q=0.2", "es-ar;q=0.8"}, want: "es-ar"},
		{name: "long header cuts at comma", header: "de," + strings.Repeat("x", 501), want: "de"},
		{name: "long one-range defaults", header: strings.Repeat("x", 501), want: "it"},
		{name: "latin1 cutoff counts code points", header: readRouteLatin1Wire("de" + strings.Repeat("\u00a0", 490) + ";q=0.9,ja"), want: "de"},
		{name: "latin1 nbsp quality header", header: readRouteLatin1Wire("de\u00a0;\u00a0q=0.8,ja"), want: "ja"},
		{name: "latin1 nel separator header", header: readRouteLatin1Wire("de\u0085,\u0085ja"), want: "de"},
		{name: "latin1 nbsp cookie trim", header: "de", cookieHeader: readRouteLatin1Wire("django_language\u00a0=\u00a0ja\u00a0"), want: "ja"},
		{name: "quoted octal cookie control", header: "de", cookie: `"\152\141"`, want: "ja"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			r := httptest.NewRequest(http.MethodGet, "/", nil)
			if test.header != "" {
				r.Header.Set("Accept-Language", test.header)
			}
			for _, value := range test.duplicateHeader {
				r.Header.Add("Accept-Language", value)
			}
			if test.cookie != "" {
				r.Header.Set("Cookie", "django_language="+test.cookie)
			}
			if test.cookieHeader != "" {
				r.Header.Set("Cookie", test.cookieHeader)
			}
			if got := handlerReadRouteLocale.Language(r); got != test.want {
				t.Fatalf("got %q want %q", got, test.want)
			}
		})
	}
}

// readRouteLatin1Wire models the bytes supplied by the ASGI server for a
// Django string containing Latin-1 code points. It deliberately does not use
// the UTF-8 representation that the same Unicode string has in a Go literal.
func readRouteLatin1Wire(value string) string {
	wire := make([]byte, 0, len(value))
	for _, codePoint := range value {
		if codePoint > 0xff {
			panic("test header contains a non-Latin-1 code point")
		}
		wire = append(wire, byte(codePoint))
	}
	return string(wire)
}

func TestDjangoReadRouteMessagesUsePinnedCatalogsAndPlaceholders(t *testing.T) {
	tests := []struct {
		name, language, method, media string
		credentials, permission       string
		accept, notFound, methodText  string
		mediaText                     string
	}{
		{
			name: "italic language flag fallback", language: "de", method: "PATCH", media: "application/xml",
			credentials: "Anmeldedaten fehlen.",
			permission:  "Sie sind nicht berechtigt, diese Aktion durchzuführen.",
			accept:      "Kann die Accept Kopfzeile der Anfrage nicht erfüllen.",
			notFound:    "Nicht gefunden.", methodText: `Methode "PATCH" nicht erlaubt.`,
			mediaText: `Nicht unterstützter Medientyp "application/xml" in der Anfrage.`,
		},
		{
			name: "french nonbreaking placeholders", language: "fr", method: "PATCH", media: "text/plain",
			credentials: "Informations d'authentification non fournies.",
			permission:  "Vous n'avez pas la permission d'effectuer cette action.",
			accept:      "L'en-tête « Accept » n'a pas pu être satisfaite.",
			notFound:    "Non trouvé.", methodText: "Méthode «\u00a0PATCH\u00a0» non autorisée.",
			mediaText: "Type de média «\u00a0text/plain\u00a0» non supporté.",
		},
		{
			name: "russian expanded catalogs", language: "ru", method: "PATCH", media: "application/xml",
			credentials: "Учетные данные не были предоставлены.",
			permission:  "У вас недостаточно прав для выполнения данного действия.",
			accept:      `Невозможно удовлетворить "Accept" заголовок запроса.`,
			notFound:    "Страница не найдена.", methodText: `Метод "PATCH" не разрешен.`,
			mediaText: `Неподдерживаемый тип данных "application/xml" в запросе.`,
		},
		{
			name: "partial Vietnamese fallback", language: "vi", method: "DELETE", media: "image/png",
			credentials: "Non sono state immesse le credenziali di autenticazione.",
			permission:  "Bạn không được cấp quyền để thực hiện hành động này.",
			accept:      "Impossibile soddisfare l'header \"Accept\" presente nella richiesta.",
			notFound:    "Không tìm thấy.", methodText: "Phương thức “DELETE” không được chấp nhận.",
			mediaText: `Tipo di media "image/png"non supportato.`,
		},
		{
			name: "partial traditional Chinese fallback", language: "zh-hant", method: "OPTIONS", media: "application/xml",
			credentials: "Non sono state immesse le credenziali di autenticazione.",
			permission:  "你沒有執行此操作的權限。",
			accept:      "Impossibile soddisfare l'header \"Accept\" presente nella richiesta.",
			notFound:    "找不到資源。", methodText: `不被允許的方法 "OPTIONS"。`,
			mediaText: `Tipo di media "application/xml"non supportato.`,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			r := httptest.NewRequest(test.method, "/", nil)
			r.Header.Set("Accept-Language", test.language)
			r.Header.Set("Content-Type", test.media)
			for _, check := range []struct {
				kind readRouteMessage
				want string
			}{
				{readRouteCredentials, test.credentials},
				{readRoutePermission, test.permission},
				{readRouteAccept, test.accept},
				{readRouteNotFound, test.notFound},
				{readRouteMethod, test.methodText},
				{readRouteMedia, test.mediaText},
			} {
				if got := handlerReadRouteLocale.Text(r, check.kind); got != check.want {
					t.Errorf("%s: got %q want %q", check.kind, got, check.want)
				}
			}
		})
	}
}
