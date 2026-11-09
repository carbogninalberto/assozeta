package service

import (
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
)

func backupFailuresDecode(t *testing.T, text string) any {
	t.Helper()
	value, err := celery.DecodeJSON([]byte(text))
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func TestConsumerBackupFailuresList(t *testing.T) {
	status := backupFailuresDecode(t, `{"history": [
	 {"id": "a", "kind": "backup", "status": "failed", "updated_at": "2026-11-09T10:00:00.123456+00:00", "error": "disco"},
	 {"id": "b", "kind": "restore", "status": "failed", "updated_at": "2026-11-09T10:00:00+00:00"},
	 {"id": "c", "kind": "backup", "status": "failed", "updated_at": null, "created_at": "2026-11-09T09:00:00Z", "error": 0},
	 {"kind": "backup", "status": "failed"},
	 {"id": "d", "kind": "backup", "status": "succeeded", "updated_at": "2026-11-09T10:00:00+01:00"}]}`)
	inventory := backupFailuresDecode(t, `{"schedule": {"last_run": {"at": "2026-11-09T08:00:00+00:00", "status": "failed", "operation": ""}},
	 "remote": {"last_failure": {"at": "2026-11-09T07:00:00+02:00", "error": "rifiutato"}}}`)
	failures, err := consumerBackupFailuresList(status, inventory)
	if err != nil {
		t.Fatal(err)
	}
	var keys []string
	for _, failure := range failures {
		keys = append(keys, failure.key+"|"+failure.reason+"|"+failure.when.UTC().Format(time.RFC3339Nano))
	}
	want := []string{"a|disco|2026-11-09T10:00:00.123456Z", "c||2026-11-09T09:00:00Z",
		"schedule:2026-11-09T08:00:00+00:00||2026-11-09T08:00:00Z", "remote:2026-11-09T07:00:00+02:00|rifiutato|2026-11-09T05:00:00Z"}
	if len(keys) != len(want) {
		t.Fatalf("got %q", keys)
	}
	for i := range want {
		if keys[i] != want[i] {
			t.Fatalf("got %q, want %q", keys, want)
		}
	}
	subject, body := consumerBackupFailuresNotice(failures[3], "")
	if subject != "Copia remota non riuscita" || body != "La copia remota del backup non è riuscita (09/11/2026 05:00).\n\nMotivo: rifiutato\n\nControlla in Istanza > Backup.\n" {
		t.Fatalf("notice %q %q", subject, body)
	}
}

func TestConsumerBackupFailuresListHandsOff(t *testing.T) {
	cases := map[string][2]string{
		"naive timestamp":        {`{"history": [{"id": "a", "kind": "restore", "updated_at": "2026-11-09T10:00:00"}]}`, `{}`},
		"numeric timestamp":      {`{"history": [{"id": "a", "updated_at": 5}]}`, `{}`},
		"missing id":             {`{"history": [{"kind": "backup", "status": "failed", "updated_at": "2026-11-09T10:00:00Z"}]}`, `{}`},
		"numeric id":             {`{"history": [{"id": 4, "kind": "backup", "status": "failed", "updated_at": "2026-11-09T10:00:00Z"}]}`, `{}`},
		"numeric reason":         {`{"history": [{"id": "a", "kind": "backup", "status": "failed", "updated_at": "2026-11-09T10:00:00Z", "error": 3}]}`, `{}`},
		"null history":           {`{"history": null}`, `{}`},
		"history of strings":     {`{"history": ["x"]}`, `{}`},
		"status list":            {`[]`, `{}`},
		"inventory list":         {`{}`, `[1]`},
		"schedule string":        {`{}`, `{"schedule": "x"}`},
		"last run list":          {`{}`, `{"schedule": {"last_run": [1]}}`},
		"seven fraction digits":  {`{}`, `{"remote": {"last_failure": {"at": "2026-11-09T10:00:00.1234567+00:00"}}}`},
		"space separator":        {`{}`, `{"remote": {"last_failure": {"at": "2026-11-09 10:00:00+00:00"}}}`},
		"basic offset":           {`{}`, `{"remote": {"last_failure": {"at": "2026-11-09T10:00:00+0100"}}}`},
		"invalid date in subset": {`{}`, `{"remote": {"last_failure": {"at": "2026-02-30T10:00:00+00:00"}}}`},
	}
	for name, input := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := consumerBackupFailuresList(backupFailuresDecode(t, input[0]), backupFailuresDecode(t, input[1]))
			if !errors.Is(err, celeryworker.ErrHandoff) {
				t.Fatalf("want a handoff, got %v", err)
			}
		})
	}
	// Python iterates an empty string or dict without reading anything.
	for _, history := range []string{`""`, `{}`, `[]`} {
		if failures, err := consumerBackupFailuresList(backupFailuresDecode(t, `{"history": `+history+`}`), backupFailuresDecode(t, `{}`)); err != nil || failures != nil {
			t.Fatalf("history %s: %v %v", history, failures, err)
		}
	}
}
