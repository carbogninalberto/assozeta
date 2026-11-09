package service

import (
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// testdata/bakney_sso_golden.json holds BE/instance/sso/protocol.py results
// (normalize_origin, origin, authority, signature, encrypt, digest) computed
// by Django for production and development settings, with placeholder values.
type bakneyGolden struct {
	Cases []struct {
		Debug         bool   `json:"debug"`
		Mode          string `json:"mode"`
		Value         string `json:"value"`
		Normalize     string `json:"normalize"`
		Origin        string `json:"origin"`
		Authority     string `json:"authority"`
		AuthorityOnly string `json:"authority_only"`
	} `json:"cases"`
	Secret     string            `json:"secret"`
	Binding    map[string]any    `json:"binding"`
	Signatures map[string]string `json:"signatures"`
	Fernet     struct{ Key, Token, Plain string }
	Digest     string `json:"digest"`
}

func goldenResult(value string, err error) string {
	var ssoErr *BakneySSOError
	switch {
	case err == nil:
		return value
	case errors.As(err, &ssoErr):
		return "SSOError:" + ssoErr.Code
	default:
		return "ValueError"
	}
}

func TestBakneyProtocolMatchesDjango(t *testing.T) {
	raw, err := os.ReadFile("testdata/bakney_sso_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden bakneyGolden
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	for _, item := range golden.Cases {
		settings := BakneySSOSettings{Debug: item.Debug, DeploymentMode: item.Mode, AppURL: item.Value, APIBase: item.Value}
		if item.AuthorityOnly != "" {
			if got := goldenResult(bakneyAuthority(settings)); got != item.AuthorityOnly {
				t.Errorf("authority(%q) = %q, Django %q", item.Value, got, item.AuthorityOnly)
			}
			continue
		}
		if got := goldenResult(normalizeBakneyOrigin(item.Value, settings)); got != item.Normalize {
			t.Errorf("normalize_origin(%q) debug=%v mode=%s = %q, Django %q", item.Value, item.Debug, item.Mode, got, item.Normalize)
		}
		if got := goldenResult(bakneyOrigin(settings)); got != item.Origin {
			t.Errorf("origin(%q) debug=%v mode=%s = %q, Django %q", item.Value, item.Debug, item.Mode, got, item.Origin)
		}
		if got := goldenResult(bakneyAuthority(settings)); got != item.Authority {
			t.Errorf("authority(%q) debug=%v mode=%s = %q, Django %q", item.Value, item.Debug, item.Mode, got, item.Authority)
		}
	}
	text := func(key string) string { return golden.Binding[key].(string) }
	binding := bakneyBinding{PairingID: text("pairing_id"), AssociationID: text("association_id"), InstanceID: text("instance_id"),
		Origin: text("origin"), CallbackURI: text("callback_uri"), Generation: text("generation"), Nonce: text("nonce"),
		ExpiresAt: strconv.FormatInt(int64(golden.Binding["expires_at"].(float64)), 10)}
	for phase, expected := range golden.Signatures {
		if got, err := bakneySignature(golden.Secret, phase, binding); err != nil || got != expected {
			t.Errorf("signature(%s) = %q %v, Django %q", phase, got, err, expected)
		}
	}
	settings := BakneySSOSettings{SecretKeys: []string{"other-key", golden.Fernet.Key}}
	if plain, err := bakneyDecrypt(golden.Fernet.Token, settings); err != nil || plain != golden.Fernet.Plain {
		t.Errorf("decrypt(Django token) = %q %v", plain, err)
	}
	token, err := bakneyEncrypt("round trip", BakneySSOSettings{SecretKeys: []string{golden.Fernet.Key}}, time.Unix(1790000000, 0))
	if err != nil {
		t.Fatal(err)
	}
	if plain, err := bakneyDecrypt(token, BakneySSOSettings{SecretKeys: []string{golden.Fernet.Key}}); err != nil || plain != "round trip" {
		t.Errorf("round trip = %q %v", plain, err)
	}
	if _, err := bakneyDecrypt(token, BakneySSOSettings{SecretKeys: []string{"other-key"}}); goldenResult("", err) != "SSOError:configuration_required" {
		t.Errorf("decrypt with another key = %v", err)
	}
	if got, _ := bakneyDigest("abc:commit:" + binding.Nonce); got != golden.Digest {
		t.Errorf("digest = %q, Django %q", got, golden.Digest)
	}
}

func TestBakneyBrowserCookieAndDates(t *testing.T) {
	sso := &BakneySSO{settings: BakneySSOSettings{Debug: true, DeploymentMode: "development"}}
	for origin, expected := range map[string]string{"http://localhost": "assozeta-sso-dev", "https://sso.example.test": "__Host-assozeta-sso"} {
		p := samplePairing(origin)
		if name, _ := sso.browserCookie(p); name != expected {
			t.Errorf("browser_cookie(%s) = %s", origin, name)
		}
	}
	sso.settings = BakneySSOSettings{DeploymentMode: "development"}
	if name, secure := sso.browserCookie(samplePairing("http://localhost")); name != "__Host-assozeta-sso" || !secure {
		t.Errorf("production cookie = %s %v", name, secure)
	}
	at := time.Date(2026, 11, 9, 8, 0, 0, 120000000, time.UTC)
	if got := drfDateTime(at); got != "2026-11-09T08:00:00.120000Z" {
		t.Errorf("drfDateTime = %s", got)
	}
	if got := drfDateTime(at.Truncate(time.Second)); got != "2026-11-09T08:00:00Z" {
		t.Errorf("drfDateTime = %s", got)
	}
	cookie := djangoCookie("BKN_AUTH", "a.b.c", 14400, true, "Strict", time.Unix(1790000000, 500))
	if cookie != "BKN_AUTH=a.b.c; expires=Mon, 21 Sep 2026 18:13:20 GMT; HttpOnly; Max-Age=14400; Path=/; SameSite=Strict; Secure" {
		t.Errorf("cookie = %s", cookie)
	}
	for value, expected := range map[any]string{"A1000000000040008000000000700004": "a1000000-0000-4000-8000-000000700004",
		"{a1000000-0000-4000-8000-000000700004}": "a1000000-0000-4000-8000-000000700004", json.Number("12"): "",
		json.Number("12345678901234567890123456789012"): "12345678-9012-3456-7890-123456789012", true: "", nil: ""} {
		if got, _ := pythonUUIDText(value); got != expected {
			t.Errorf("uuid.UUID(str(%v)) = %q, want %q", value, got, expected)
		}
	}
}

func samplePairing(origin string) *domain.BakneyPairing { return &domain.BakneyPairing{Origin: origin} }
