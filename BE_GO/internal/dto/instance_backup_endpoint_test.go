package dto

import (
	"encoding/json"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestBackupRemoteEndpointProblem(t *testing.T) {
	allowed := []string{"", "https://s3.example.test", "https://s3.eu-south-1.amazonaws.com/", "https://192.168.1.20:9000",
		"https://10.0.0.5", "https://minio.lan:9000/base", "https://[2001:db8::5]:9000", "https://è.example.test",
		"https://[::ffff:192.168.1.20]", "https://172.16.0.1", "https://8.8.8.8", "https://minio.lan.", "https://s3.example.test:65535/a/b"}
	for _, value := range allowed {
		if problem := BackupRemoteEndpointProblem(value, false); problem != "" {
			t.Errorf("%q: %q", value, problem)
		}
	}
	cases := map[string]string{
		"ftp://s3.example.test": BackupEndpointInvalid, "HTTPS://s3.example.test": BackupEndpointInvalid,
		"s3.example.test": BackupEndpointInvalid, "https://user:pass@s3.example.test": BackupEndpointInvalid,
		"https://s3.example.test:": BackupEndpointInvalid, "https://s3.example.test:99999": BackupEndpointInvalid,
		"https://s3.example.test:0": BackupEndpointInvalid, "https://s3.example.test?x=1": BackupEndpointInvalid,
		"https://::1/": BackupEndpointInvalid, "https://[fe80::1%25en0]": BackupEndpointInvalid, "https://": BackupEndpointInvalid,
		"https://2852039166": BackupEndpointInvalid, "https://0xA9FEA9FE": BackupEndpointInvalid,
		"https://0251.0376.0251.0376": BackupEndpointInvalid, "https://169.254.169.254.1": BackupEndpointInvalid,
		"https://...": BackupEndpointInvalid, "https://[zz::1]": BackupEndpointInvalid, "https://[1::2::3]": BackupEndpointInvalid,
		"https://[1.2.3.4]": BackupEndpointInvalid, "https://s3\v.example.test": BackupEndpointInvalid,
		"https://169.254.169.254": BackupEndpointBlocked, "https://169.254.169.254./latest": BackupEndpointBlocked,
		"https://127.0.0.1:9000": BackupEndpointBlocked, "https://127.1.2.3": BackupEndpointBlocked, "https://0.0.0.0": BackupEndpointBlocked,
		"https://localhost:9000": BackupEndpointBlocked, "https://LocalHost": BackupEndpointBlocked, "https://minio.localhost": BackupEndpointBlocked,
		"https://metadata.google.internal": BackupEndpointBlocked, "https://metadata": BackupEndpointBlocked,
		"https://[::1]": BackupEndpointBlocked, "https://[::]": BackupEndpointBlocked, "https://[fe80::1]": BackupEndpointBlocked,
		"https://[fd00:ec2::254]": BackupEndpointBlocked, "https://[::ffff:169.254.169.254]": BackupEndpointBlocked,
		"https://[::ffff:127.0.0.1]": BackupEndpointBlocked, "https://[64:ff9b::a9fe:a9fe]": BackupEndpointBlocked,
		"https://100.100.100.200": BackupEndpointBlocked, "https://224.0.0.1": BackupEndpointBlocked,
		"https://255.255.255.255": BackupEndpointBlocked, "https://[ff02::1]": BackupEndpointBlocked,
		"http://s3.example.test": BackupEndpointHTTPS, "http://192.168.1.20:9000": BackupEndpointHTTPS,
		"http://169.254.169.254": BackupEndpointBlocked,
	}
	for value, want := range cases {
		if got := BackupRemoteEndpointProblem(value, false); got != want {
			t.Errorf("%q: got %q, want %q", value, got, want)
		}
	}
	// Plain http:// is an opt-in for a storage on the local network; the address rules still apply.
	if got := BackupRemoteEndpointProblem("http://192.168.1.20:9000", true); got != "" {
		t.Errorf("opt-in: %q", got)
	}
	if got := BackupRemoteEndpointProblem("http://127.0.0.1:9000", true); got != BackupEndpointBlocked {
		t.Errorf("opt-in loopback: %q", got)
	}
}

func TestBackupRemoteSettingsFieldsCheckTheEndpoint(t *testing.T) {
	body := domain.RequestObject{{Name: "endpoint", Value: " http://169.254.169.254 "}, {Name: "retention", Value: json.Number("0")}}
	_, errs, err := ValidateBackupActions(body, BackupRemoteSettingsFields(false, true))
	if err != nil {
		t.Fatal(err)
	}
	want := `{"endpoint": ["` + BackupEndpointBlocked + `"], "retention": ["min-value:1"], "password": ["required"]}`
	if got := string(PythonJSONDumps(errs)); got != string(PythonJSONDumps(mustDecodeForTest(t, want))) {
		t.Fatalf("got %s", got)
	}
	// The connection test has no password; a valid endpoint is kept as trimmed.
	value, errs, err := ValidateBackupActions(domain.RequestObject{{Name: "endpoint", Value: " https://10.0.0.5 "}}, BackupRemoteSettingsFields(false, false))
	if err != nil || errs != nil || string(PythonJSONDumps(value)) != `{"endpoint": "https://10.0.0.5"}` {
		t.Fatalf("got %v %v %v", value, errs, err)
	}
}

func mustDecodeForTest(t *testing.T, text string) domain.RequestObject {
	t.Helper()
	var raw map[string][]string
	if err := json.Unmarshal([]byte(text), &raw); err != nil {
		t.Fatal(err)
	}
	out := domain.RequestObject{}
	for _, name := range []string{"endpoint", "retention", "password"} {
		var items []any
		for _, item := range raw[name] {
			items = append(items, item)
		}
		out = append(out, domain.RequestField{Name: name, Value: items})
	}
	return out
}
