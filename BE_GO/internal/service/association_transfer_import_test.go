package service

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
)

// Expected values in this file were produced by CPython 3.13.16 running
// ImportValidator.validate_file/validate_manifest/validate_data_files/
// validate_owner_identity on the same archives.

const importTestAssociation = "a1000000-0000-4000-8000-000000976950"
const importTestOwner = "a1000000-0000-4000-8000-000000976951"

// The hashes Django's default PASSWORD_HASHERS encode for the password
// "secret" and the salt "abcdefghijklmnopqrstuv" (bcrypt uses its own random
// salt); CPython's is_restorable_password_hash returns True for each.
const (
	importTestPBKDF2SHA256 = "pbkdf2_sha256$1000000$abcdefghijklmnopqrstuv$tW94pSigUM2AlIkWSQO4yVeVgz1Hy8/tw6HFhR010Gk="
	importTestPBKDF2SHA1   = "pbkdf2_sha1$1000000$abcdefghijklmnopqrstuv$sFm5hQjQKip5kpM2m+9tpSNirH8="
	importTestArgon2       = "argon2$argon2id$v=19$m=102400,t=2,p=8$YWJjZGVmZ2hpamtsbW5vcHFyc3R1dg$A9DAPQnz27exXaIEX/gYEkGY9RGfCEFQQ/I38bD2xuM"
	importTestBCryptSHA256 = "bcrypt_sha256$$2b$12$aEUW1bUZP8SXpoiwC.Z5s.0XhWu9QyrdHKaP1s1qq7U0BeMjP2DZy"
	importTestScrypt       = "scrypt$16384$abcdefghijklmnopqrstuv$8$5$7+fVdWAoYF5tiOFUhWAk9HPmdFw5yyZqSz8ThhvgSnI4xqXq4vnGiy+ngEpPYnpZTfLd3D4CXVjanFjRQ/qFrg=="
)

// The digest and salt segments of the vectors above.
const (
	importTestPBKDF2Digest = "tW94pSigUM2AlIkWSQO4yVeVgz1Hy8/tw6HFhR010Gk="
	importTestArgon2Salt   = "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dg"
	importTestArgon2Digest = "A9DAPQnz27exXaIEX/gYEkGY9RGfCEFQQ/I38bD2xuM"
)

func TestImportRestorablePasswordHash(t *testing.T) {
	cases := []struct {
		encoded string
		want    bool
	}{
		{importTestPBKDF2SHA256, true},
		{importTestPBKDF2SHA1, true},
		{importTestArgon2, true},
		{importTestBCryptSHA256, true},
		{importTestScrypt, true},
		{"", false},
		{"!unusable", false},
		{"!anything", false},
		// pbkdf2: int(iterations) reads whitespace, underscores and a sign.
		{"pbkdf2_sha256$ 1000000 $salt$" + importTestPBKDF2Digest, true},
		{"pbkdf2_sha256$1_000_000$salt$" + importTestPBKDF2Digest, true},
		{"pbkdf2_sha256$+1000000$salt$" + importTestPBKDF2Digest, true},
		{"pbkdf2_sha256$0$salt$" + importTestPBKDF2Digest, false},
		{"pbkdf2_sha256$1000001$salt$" + importTestPBKDF2Digest, false},
		{"pbkdf2_sha256$1.0$salt$" + importTestPBKDF2Digest, false},
		{"pbkdf2_sha256$1000000$$" + importTestPBKDF2Digest, false},
		{"pbkdf2_sha256$1000000$salt$" + importTestPBKDF2Digest + "$x", false},
		// base64.b64decode(validate=True) is strict: no missing padding, no
		// newline and no shorter/longer digest.
		{"pbkdf2_sha256$1000000$salt$" + strings.TrimSuffix(importTestPBKDF2Digest, "="), false},
		{"pbkdf2_sha256$1000000$salt$" + importTestPBKDF2Digest[:1] + "\n" + importTestPBKDF2Digest[1:], false},
		// Algorithms off the configured list (and identify_hasher's
		// unsalted_sha1 / md5 names) are false.
		{"sha256$abc$def", false},
		{strings.Repeat("A", 32), false},
		{"md5$abc$def", false},
		{"bcrypt$2b$12$" + strings.Repeat("a", 53), false},
		// scrypt: work factor a power of two above one, within 2**14.
		{"scrypt$16384$salt$8$5$" + strings.Repeat("A", 86) + "==", true},
		{"scrypt$2$salt$8$5$" + strings.Repeat("A", 86) + "==", true},
		{"scrypt$0$salt$8$5$" + strings.Repeat("A", 86) + "==", false},
		{"scrypt$32768$salt$8$5$" + strings.Repeat("A", 86) + "==", false},
		{"scrypt$16383$salt$8$5$" + strings.Repeat("A", 86) + "==", false},
		{"scrypt$16384$salt$9$5$" + strings.Repeat("A", 86) + "==", false},
		{"scrypt$16384$salt$8$6$" + strings.Repeat("A", 86) + "==", false},
		{"scrypt$16384$$8$5$" + strings.Repeat("A", 86) + "==", false},
		{"scrypt$16384$salt$8$5$" + strings.Repeat("A", 86) + "=$", false},
		// argon2: the PHC parameters are parsed like argon2-cffi.
		{"argon2$argon2id$v=19$m=102401,t=2,p=8$" + importTestArgon2Salt + "$" + importTestArgon2Digest, false},
		{"argon2$argon2id$v=19$m=100,t=0,p=8$" + importTestArgon2Salt + "$" + importTestArgon2Digest, false},
		{"argon2$argon2id$v=19$m=100,t=1,p=9$" + importTestArgon2Salt + "$" + importTestArgon2Digest, false},
		{"argon2$argon2id$v=19$m=100,t=1,p=1$$" + importTestArgon2Digest, false},
		{"argon2$argon2id$v=19$m=100,t=1,p=1$YWJj$!!!$A9DA", false},
		{"argon2$argon2id$m=100,t=1,p=1$" + importTestArgon2Salt + "$" + importTestArgon2Digest, true},
		{"argon2$argon2id$v=19$m=100,t=1,p=1$YWJj$" + "A9DAP", false},
		{"argon2$argon2id$v=18$m=100,t=1,p=1$" + importTestArgon2Salt + "$" + importTestArgon2Digest, true},
		{"argon2$argon2i$m=100,t=1,p=1$" + importTestArgon2Salt + "$" + importTestArgon2Digest, true},
		{"argon2$xargon2id$v=19$m=100,t=1,p=1$" + importTestArgon2Salt + "$" + importTestArgon2Digest, false},
		{"argon2$argon2id$v=19$m=100,t=1,p=1,keyid=1$" + importTestArgon2Salt + "$" + importTestArgon2Digest, false},
		{"argon2$argon2id$v=19$m= 100 ,t=1,p=1$" + importTestArgon2Salt + "$" + importTestArgon2Digest, true},
		// bcrypt_sha256: algostr, work factor within [4, 12], 22+31 characters.
		{"bcrypt_sha256$$2a$12$aEUW1bUZP8SXpoiwC.Z5s.0XhWu9QyrdHKaP1s1qq7U0BeMjP2DZy", true},
		{"bcrypt_sha256$$2y$04$aEUW1bUZP8SXpoiwC.Z5s.0XhWu9QyrdHKaP1s1qq7U0BeMjP2DZy", true},
		{"bcrypt_sha256$$2b$3$aEUW1bUZP8SXpoiwC.Z5s.0XhWu9QyrdHKaP1s1qq7U0BeMjP2DZy", false},
		{"bcrypt_sha256$$2b$13$aEUW1bUZP8SXpoiwC.Z5s.0XhWu9QyrdHKaP1s1qq7U0BeMjP2DZy", false},
		{"bcrypt_sha256$$2x$12$aEUW1bUZP8SXpoiwC.Z5s.0XhWu9QyrdHKaP1s1qq7U0BeMjP2DZy", false},
		{"bcrypt_sha256$$2b$ 12 $aEUW1bUZP8SXpoiwC.Z5s.0XhWu9QyrdHKaP1s1qq7U0BeMjP2DZy", true},
		{"bcrypt_sha256$$2b$12$" + strings.Repeat("!", 22) + "0XhWu9QyrdHKaP1s1qq7U0BeMjP2DZy", false},
	}
	for _, c := range cases {
		if got := importRestorablePasswordHash(c.encoded); got != c.want {
			t.Errorf("is_restorable_password_hash(%q) = %v, want %v", c.encoded, got, c.want)
		}
	}
	// Only strings can hold a hash: any other JSON value needs recovery.
	for _, value := range []any{nil, json.Number("5"), true, []any{}, domain.RequestObject{}} {
		if importRestorablePasswordHash(value) {
			t.Errorf("is_restorable_password_hash(%#v) = true", value)
		}
	}
}

func TestImportUUIDFailureText(t *testing.T) {
	cases := []struct {
		value any
		want  string
	}{
		{"abc", "['“abc” is not a valid UUID.']"},
		{"", "['“” is not a valid UUID.']"},
		{json.Number("-1"), "['“-1” is not a valid UUID.']"},
		{float64(1.5), "['“1.5” is not a valid UUID.']"},
		{[]any{json.Number("1")}, "['“[1]” is not a valid UUID.']"},
		{domain.RequestObject{{Name: "a", Value: json.Number("1")}}, "[\"“{'a': 1}” is not a valid UUID.\"]"},
	}
	for _, c := range cases {
		value, want := c.value, c.want
		var failure *importValidationFailure
		err := importUUIDFailure(value)
		if !errors.As(err, &failure) || failure.text != want {
			t.Errorf("importUUIDFailure(%#v) = %v, want %q", value, err, want)
		}
	}
}

// importTestWriteStub supplies validate_uuid_conflicts' lookups.
type importTestWriteStub struct {
	conflicts  repository.ImportConflicts
	err        error
	calls      int
	candidates []string
}

func (s *importTestWriteStub) ImportConflicts(ctx context.Context, association, owner string, candidates []string) (repository.ImportConflicts, error) {
	s.calls++
	s.candidates = candidates
	return s.conflicts, s.err
}
func (s *importTestWriteStub) ExportFiles(ctx context.Context, association, document string) ([]repository.AssociationExportFile, error) {
	return nil, nil
}
func (s *importTestWriteStub) ActorAssociation(ctx context.Context, actor string) (*string, error) {
	return nil, nil
}
func (s *importTestWriteStub) DeleteArchive(ctx context.Context, id, association string, audit domain.LoginAuditContext, at time.Time) error {
	return nil
}
func (s *importTestWriteStub) DeleteDocument(ctx context.Context, id string, audit domain.LoginAuditContext, at time.Time) error {
	return nil
}
func (s *importTestWriteStub) SetExportSize(ctx context.Context, id string, size int64, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	return nil
}
func (s *importTestWriteStub) CacheValue(ctx context.Context, key string) (any, bool, error) {
	return nil, false, nil
}
func (s *importTestWriteStub) CacheAdd(ctx context.Context, key string, value associationexport.Dict, timeout time.Duration) (bool, error) {
	return false, nil
}
func (s *importTestWriteStub) CacheSet(ctx context.Context, key string, value associationexport.Dict, timeout time.Duration) error {
	return nil
}
func (s *importTestWriteStub) CacheDelete(ctx context.Context, keys ...string) error { return nil }

// importTestArchive writes a ZIP whose JSON members are given as text; a
// member may also be raw bytes.
func importTestArchive(t *testing.T, members map[string][]byte) []byte {
	t.Helper()
	names := make([]string, 0, len(members))
	for name := range members {
		names = append(names, name)
	}
	sort.Strings(names)
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	for _, name := range names {
		header := &zip.FileHeader{Name: name, Method: zip.Deflate}
		header.Modified = time.Date(2026, 1, 15, 10, 0, 0, 0, time.UTC)
		part, err := writer.CreateHeader(header)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := part.Write(members[name]); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

func importTestJSON(t *testing.T, value any) []byte {
	t.Helper()
	text, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return text
}

// importTestMembers is export_members of the parity fixture.
func importTestMembers(t *testing.T, overrides map[string]any) map[string][]byte {
	t.Helper()
	manifest := map[string]any{
		"version": "1.0.0", "export_format": "bakney_sport_export_v1", "export_date": "2026-01-15T10:00:00+00:00",
		"association":     map[string]any{"sport_association_id": importTestAssociation},
		"models_exported": []any{map[string]any{"name": "SportAssociation", "file": "data/01_sport_association.json"}},
	}
	record := map[string]any{"sport_association_id": importTestAssociation, "user_id": importTestOwner,
		"denomination": "x", "tax_code": "y"}
	users := []any{map[string]any{"user_id": importTestOwner, "username": "o", "email": "e", "password": "!"}}
	members := map[string][]byte{
		"manifest.json":                  importTestJSON(t, manifest),
		"data/01_sport_association.json": importTestJSON(t, []any{record}),
		"data/02_users.json":             importTestJSON(t, users),
	}
	for name, value := range overrides {
		switch typed := value.(type) {
		case []byte:
			members[name] = typed
		default:
			members[name] = importTestJSON(t, typed)
		}
	}
	return members
}

func importTestValidate(t *testing.T, members map[string][]byte) (*AssociationTransferReply, error) {
	t.Helper()
	archive := importTestArchive(t, members)
	transfer := (&AssociationTransfer{}).With(AssociationTransferOptions{Writes: &importTestWriteStub{}})
	return transfer.ImportValidate(context.Background(),
		AssociationImportUpload{File: &AssociationImportFile{Name: "export.zip", Content: archive}})
}

func importTestFailure(t *testing.T, reply *AssociationTransferReply, err error) string {
	t.Helper()
	if err != nil {
		t.Fatalf("unexpected error %v", err)
	}
	if reply == nil || reply.Status != 500 {
		t.Fatalf("unexpected reply %+v", reply)
	}
	body, ok := reply.Body.(map[string]string)
	if !ok {
		t.Fatalf("unexpected body %#v", reply.Body)
	}
	return body["error"]
}

func importTestBody(t *testing.T, members map[string][]byte) map[string]any {
	t.Helper()
	reply, err := importTestValidate(t, members)
	return importTestSuccess(t, reply, err)
}

func importTestSuccess(t *testing.T, reply *AssociationTransferReply, err error) map[string]any {
	t.Helper()
	if err != nil {
		t.Fatalf("unexpected error %v", err)
	}
	if reply == nil || reply.Status != 200 {
		t.Fatalf("unexpected reply %+v", reply)
	}
	body, ok := reply.Body.(map[string]any)
	if !ok {
		t.Fatalf("unexpected body %#v", reply.Body)
	}
	return body
}

func TestImportValidateManifestShapes(t *testing.T) {
	cases := []struct {
		name     string
		manifest any
		want     string
	}{
		{"list", []any{json.Number("1")}, "Errore durante la validazione: 'list' object has no attribute 'get'"},
		{"str", "abc", "Errore durante la validazione: 'str' object has no attribute 'get'"},
		{"int", json.Number("5"), "Errore durante la validazione: argument of type 'int' is not iterable"},
		{"null", nil, "Errore durante la validazione: argument of type 'NoneType' is not iterable"},
		{"bool", true, "Errore durante la validazione: argument of type 'bool' is not iterable"},
		{"float", 1.5, "Errore durante la validazione: argument of type 'float' is not iterable"},
	}
	for _, c := range cases {
		reply, err := importTestValidate(t, importTestMembers(t, map[string]any{"manifest.json": c.manifest}))
		if got := importTestFailure(t, reply, err); got != c.want {
			t.Errorf("%s manifest: %q, want %q", c.name, got, c.want)
		}
	}
}

func TestImportValidateExportFormat(t *testing.T) {
	cases := []struct {
		name   string
		format any
		want   string
	}{
		{"dict", map[string]any{"a": json.Number("1")}, "Unsupported export format: {'a': 1}"},
		{"number", json.Number("5"), "Unsupported export format: 5"},
		{"float", 5.5, "Unsupported export format: 5.5"},
		{"null", nil, "Unsupported export format: None"},
		{"list", []any{json.Number("1")}, "Unsupported export format: [1]"},
	}
	for _, c := range cases {
		manifest := map[string]any{"version": "1.0.0", "export_date": "2026-01-15T10:00:00+00:00",
			"association": map[string]any{}, "export_format": c.format}
		body := importTestBody(t, importTestMembers(t, map[string]any{"manifest.json": manifest}))
		if body["is_valid"] != false {
			t.Errorf("%s: unexpected body %#v", c.name, body)
		}
		errors_ := body["errors"].([]string)
		if len(errors_) != 1 || errors_[0] != c.want {
			t.Errorf("%s: errors %v, want %q", c.name, errors_, c.want)
		}
		if got := body["warnings"].([]string); len(got) != 0 {
			t.Errorf("%s: warnings %v", c.name, got)
		}
		// The info keeps the raw JSON value.
		info := body["info"].(map[string]any)
		raw, present := info["export_format"]
		want, _ := json.Marshal(c.format)
		got, err := json.Marshal(raw)
		if !present || err != nil || !bytes.Equal(got, want) {
			t.Errorf("%s: info export_format %s, want %s", c.name, got, want)
		}
	}
}

func TestImportValidateModelsExported(t *testing.T) {
	base := func(models any) map[string]any {
		return map[string]any{"version": "1.0.0", "export_format": "bakney_sport_export_v1",
			"export_date": "2026-01-15T10:00:00+00:00", "association": map[string]any{"sport_association_id": importTestAssociation},
			"models_exported": models}
	}
	for name, c := range map[string]struct {
		models any
		want   string
	}{
		"dict":      {map[string]any{"file": "x"}, "Errore durante la validazione: 'str' object has no attribute 'get'"},
		"string":    {"ab", "Errore durante la validazione: 'str' object has no attribute 'get'"},
		"number":    {json.Number("5"), "Errore durante la validazione: 'int' object is not iterable"},
		"null":      {nil, "Errore durante la validazione: 'NoneType' object is not iterable"},
		"list-item": {[]any{json.Number("1")}, "Errore durante la validazione: 'int' object has no attribute 'get'"},
		"list-file": {[]any{map[string]any{"file": json.Number("3")}},
			"Errore durante la validazione: argument of type 'int' is not iterable"},
	} {
		reply, err := importTestValidate(t, importTestMembers(t, map[string]any{"manifest.json": base(c.models)}))
		if got := importTestFailure(t, reply, err); got != c.want {
			t.Errorf("%s: %q, want %q", name, got, c.want)
		}
	}

	// items without a file: nothing expected, nothing missing.
	body := importTestBody(t, importTestMembers(t,
		map[string]any{"manifest.json": base([]any{map[string]any{"name": "X"}})}))
	info := body["info"].(map[string]any)
	if info["expected_files"] != 0 || info["missing_files"] != 0 || body["is_valid"] != true {
		t.Errorf("no file: %#v", body)
	}

	// a list file: '/' is membership in the list, so the raw list is expected
	// and str() of it is the warning.
	body = importTestBody(t, importTestMembers(t,
		map[string]any{"manifest.json": base([]any{map[string]any{"file": []any{"data/x.json"}}})}))
	info = body["info"].(map[string]any)
	if got := body["warnings"].([]string); len(got) != 1 || got[0] != "Missing data file: data/['data/x.json']" {
		t.Errorf("list file warnings %v", got)
	}
	if info["expected_files"] != 1 || info["missing_files"] != 1 || body["is_valid"] != true {
		t.Errorf("list file: %#v", body)
	}
}

func TestImportValidateOwnerIdentityShapes(t *testing.T) {
	// The association record is not an object: `.get` raises inside
	// validate_owner_identity's outer except.
	reply, err := importTestValidate(t, importTestMembers(t,
		map[string]any{"data/01_sport_association.json": []any{[]any{json.Number("1")}}}))
	body := importTestSuccess(t, reply, err)
	if errors_ := body["errors"].([]string); len(errors_) != 1 ||
		errors_[0] != "Error reading owner identity: 'list' object has no attribute 'get'" {
		t.Errorf("association record: %v", errors_)
	}

	cases := []struct {
		name  string
		users any
		want  string
	}{
		{"object", map[string]any{"user_id": importTestOwner}, "Error reading owner identity: Expected a JSON array"},
		{"non-dict", []any{json.Number("1")}, "Error reading owner identity: 'int' object has no attribute 'get'"},
		{"truncated", []byte(`[{"user_id": "a"}, {"use`), "Invalid JSON in data files: Unterminated string starting at: line 1 column 21 (char 20)"},
		{"not-array", []byte(`NaN`), "Error reading owner identity: Expected a JSON array"},
	}
	for _, c := range cases {
		body := importTestBody(t, importTestMembers(t,
			map[string]any{"data/02_users.json": c.users}))
		if errors_ := body["errors"].([]string); len(errors_) != 1 || errors_[0] != c.want {
			t.Errorf("%s: %v, want %q", c.name, errors_, c.want)
		}
		if body["is_valid"] != false {
			t.Errorf("%s: valid", c.name)
		}
	}
}

func TestImportValidateUserIDShapes(t *testing.T) {
	// str(user_id) is compared: an int user_id matches an int owner id, and
	// the owner info keeps the raw value.
	members := importTestMembers(t, nil)
	members["data/01_sport_association.json"] = importTestJSON(t, []any{map[string]any{
		"sport_association_id": importTestAssociation, "user_id": json.Number("5"), "denomination": "x"}})
	members["data/02_users.json"] = importTestJSON(t, []any{map[string]any{"user_id": json.Number("5"), "username": "o"}})
	delete(members, "manifest.json")
	members["manifest.json"] = importTestJSON(t, map[string]any{
		"version": "1.0.0", "export_format": "bakney_sport_export_v1", "export_date": "2026-01-15T10:00:00+00:00",
		"association": map[string]any{"sport_association_id": importTestAssociation}})
	body := importTestBody(t, members)
	if body["is_valid"] != true {
		t.Fatalf("unexpected body %#v", body)
	}
	owner := body["info"].(map[string]any)["owner_user"].(map[string]any)
	if owner["user_id"] != json.Number("5") || owner["requires_recovery_password"] != true {
		t.Errorf("owner %#v", owner)
	}

	// A float owner id never equals an int user id: str(1.0) is '1.0'.
	// (json.Marshal writes 1.0 as 1, so the member is written by hand.)
	members["data/01_sport_association.json"] = []byte(`[{"sport_association_id": "` + importTestAssociation + `", "user_id": 1.0}]`)
	members["data/02_users.json"] = importTestJSON(t, []any{map[string]any{"user_id": json.Number("1")}})
	body = importTestBody(t, members)
	if errors_ := body["errors"].([]string); len(errors_) != 1 ||
		errors_[0] != "Expected exactly one User with user_id 1.0 matching SportAssociation.user_id" {
		t.Errorf("float owner: %v", errors_)
	}
}

func TestImportValidateManifestEncoding(t *testing.T) {
	// The manifest as UTF-16 with a BOM is what json.loads reads.
	manifest := map[string]any{"version": "1.0.0", "export_format": "bakney_sport_export_v1",
		"export_date": "2026-01-15T10:00:00+00:00", "association": map[string]any{"sport_association_id": importTestAssociation},
		"models_exported": []any{map[string]any{"file": "data/01_sport_association.json"}}}
	text, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	wide := []byte{0xFF, 0xFE}
	for _, r := range string(text) {
		wide = append(wide, byte(r), byte(r>>8))
	}
	body := importTestBody(t, importTestMembers(t, map[string]any{"manifest.json": wide}))
	if body["is_valid"] != true || body["warnings"].([]string) == nil {
		t.Errorf("utf-16 manifest: %#v", body)
	}

	// Invalid UTF-8 is a UnicodeDecodeError: the generic manifest text.
	body = importTestBody(t, importTestMembers(t, map[string]any{"manifest.json": []byte("{\"a\": \"\xff\"}")}))
	if errors_ := body["errors"].([]string); len(errors_) != 1 || errors_[0] !=
		"Error reading manifest: 'utf-8' codec can't decode byte 0xff in position 7: invalid start byte" {
		t.Errorf("invalid utf-8: %v", errors_)
	}
	if info := body["info"].(map[string]any); len(info) != 2 {
		t.Errorf("invalid utf-8 info %#v", info)
	}
}

func TestImportValidateLoneSurrogate(t *testing.T) {
	// DRF's renderer encodes its output as UTF-8: a lone surrogate in the
	// body is Django's 500 page.
	manifest := []byte(`{"version": "1.0.0", "export_format": "bakney_sport_export_v1\ud800",` +
		` "export_date": "2026-01-15T10:00:00+00:00", "association": {}}`)
	transfer := (&AssociationTransfer{}).With(AssociationTransferOptions{Writes: &importTestWriteStub{}})
	archive := importTestArchive(t, importTestMembers(t, map[string]any{"manifest.json": manifest}))
	_, err := transfer.ImportValidate(context.Background(),
		AssociationImportUpload{File: &AssociationImportFile{Name: "export.zip", Content: archive}})
	if !errors.Is(err, errTransferUnrenderable) {
		t.Fatalf("unexpected error %v", err)
	}
}

func TestImportValidateUUIDConflicts(t *testing.T) {
	// A non-UUID sport_association_id is UUIDField.to_python's ValidationError.
	reply, err := importTestValidate(t, importTestMembers(t, map[string]any{
		"data/01_sport_association.json": []any{map[string]any{
			"sport_association_id": "abc", "user_id": importTestOwner, "denomination": "x"}}}))
	want := "Errore durante la validazione: ['“abc” is not a valid UUID.']"
	if got := importTestFailure(t, reply, err); got != want {
		t.Errorf("association id: %q, want %q", got, want)
	}

	// The owner id is converted next.
	members := importTestMembers(t, map[string]any{"data/01_sport_association.json": []any{map[string]any{
		"sport_association_id": importTestAssociation, "user_id": "abc"}}})
	members["data/02_users.json"] = importTestJSON(t, []any{map[string]any{"user_id": "abc"}})
	reply, err = importTestValidate(t, members)
	want = "Errore durante la validazione: ['“abc” is not a valid UUID.']"
	if got := importTestFailure(t, reply, err); got != want {
		t.Errorf("owner id: %q, want %q", got, want)
	}

	// And every other archive user is converted for the __in lookup.
	members = importTestMembers(t, nil)
	members["data/02_users.json"] = importTestJSON(t, []any{
		map[string]any{"user_id": importTestOwner}, map[string]any{"user_id": "nothex"}})
	reply, err = importTestValidate(t, members)
	want = "Errore durante la validazione: ['“nothex” is not a valid UUID.']"
	if got := importTestFailure(t, reply, err); got != want {
		t.Errorf("candidate: %q, want %q", got, want)
	}
}

func TestImportValidateConflictAnswers(t *testing.T) {
	// Django converts ints and bools to uuid.UUID(int=n); the answer is the
	// conflict text with the raw value.
	stub := &importTestWriteStub{conflicts: repository.ImportConflicts{
		AssociationExists: true, OwnerExists: true, OtherAssociation: importTestStringPointer("00000000-0000-0000-0000-000000000009"),
		Unlinked: []string{"00000000-0000-0000-0000-000000000007"}}}
	// The archive's other users are canonicalized for the __in lookup too, so
	// they must be UUIDs (an int user_id would be Django's ValidationError).
	members := importTestMembers(t, map[string]any{"data/01_sport_association.json": []any{map[string]any{
		"sport_association_id": json.Number("5"), "user_id": json.Number("6")}}})
	members["data/02_users.json"] = importTestJSON(t, []any{
		map[string]any{"user_id": json.Number("6")},
		map[string]any{"user_id": "a1000000-0000-4000-8000-000000976952"}})
	transfer := (&AssociationTransfer{}).With(AssociationTransferOptions{Writes: stub})
	archive := importTestArchive(t, members)
	reply, err := transfer.ImportValidate(context.Background(),
		AssociationImportUpload{File: &AssociationImportFile{Name: "export.zip", Content: archive}})
	body := importTestSuccess(t, reply, err)
	want := []string{
		"Association UUID conflict: 5 already exists",
		"Owner user already owns another SportAssociation: 00000000-0000-0000-0000-000000000009",
		"User UUID collision: 00000000-0000-0000-0000-000000000007 already exists and is not linked to the archive owner",
	}
	if got := body["errors"].([]string); strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Errorf("errors %v, want %v", got, want)
	}
	if body["is_valid"] != false {
		t.Errorf("valid despite conflicts")
	}
	if owner := body["info"].(map[string]any)["owner_user"].(map[string]any); owner["requires_recovery_password"] != false {
		t.Errorf("an existing owner keeps its credentials: %#v", owner)
	}
}

func importTestStringPointer(value string) *string { return &value }

// TestImportBase64Len checks base64.b64decode(text, validate=True) lengths;
// the expected values are CPython 3.13.16's.
func TestImportBase64Len(t *testing.T) {
	cases := []struct {
		text string
		want int
		ok   bool
	}{
		{"AA==", 1, true},
		{"AA=", 0, false}, // Incorrect padding
		{"AA", 0, false},  // Incorrect padding
		{"AAA=", 2, true},
		{"AAA", 0, false}, // Incorrect padding
		{"AAAA", 3, true},
		{"AAAA=", 0, false}, // Excess padding not allowed
		{"A===", 0, false},  // 1 data character
		{"====", 0, false},  // Leading padding not allowed
		{"=AAA", 0, false},  // Leading padding not allowed
		{"AB=CD", 0, false}, // Discontinuous padding not allowed
		{"AB=C", 0, false},
		{"A A==", 0, false},  // Only base64 data is allowed
		{"AA==AA", 0, false}, // Excess data after padding
		{"AAA==", 0, false},  // Excess padding not allowed
		{"AAAAA", 0, false},
		{"AAAAAA", 0, false}, // Incorrect padding
		{"A", 0, false},
		{"", 0, true},
	}
	for _, c := range cases {
		got, ok := importBase64Len(c.text)
		if got != c.want || ok != c.ok {
			t.Errorf("importBase64Len(%q) = (%d, %v), want (%d, %v)", c.text, got, ok, c.want, c.ok)
		}
	}
}

// TestImportValidateOwnerMatchedThroughStr: the owner record is found with
// str(user_id) == str(owner_user_id), so an owner id "None" matches a user
// whose user_id is null; validate_uuid_conflicts then reads that record's
// user_id (None) and skips the owner checks (`if owner_id:`).
func TestImportValidateOwnerMatchedThroughStr(t *testing.T) {
	members := importTestMembers(t, map[string]any{
		"data/01_sport_association.json": []any{map[string]any{"sport_association_id": importTestAssociation, "user_id": "None",
			"denomination": "x", "tax_code": "y"}},
		"data/02_users.json": []any{map[string]any{"user_id": nil, "username": "o", "email": "e", "password": "!"}},
	})
	body := importTestBody(t, members)
	if got := fmt.Sprint(body["errors"]); got != "[]" {
		t.Fatalf("errors %s", got)
	}
	owner, _ := body["info"].(map[string]any)["owner_user"].(map[string]any)
	if owner == nil || owner["user_id"] != nil {
		t.Fatalf("owner_user %#v", owner)
	}
}
