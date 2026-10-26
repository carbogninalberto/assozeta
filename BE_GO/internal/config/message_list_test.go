package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMessageListSpoolDirectoryEnvironmentAndRedaction(t *testing.T) {
	env := environment()
	cfg, err := Parse(env)
	if err != nil || cfg.MessageListSpoolDirectory != "" {
		t.Fatal("native default must preserve os.TempDir", err)
	}
	env["GO_MESSAGE_LIST_SPOOL_DIR"] = "/var/cache/private-message-list"
	cfg, err = Parse(env)
	if err != nil || cfg.MessageListSpoolDirectory != env["GO_MESSAGE_LIST_SPOOL_DIR"] {
		t.Fatal("explicit spool directory ignored", err)
	}
	encoded, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []string{string(encoded), fmt.Sprint(cfg), fmt.Sprintf("%#v", cfg)} {
		if strings.Contains(value, "private-message-list") {
			t.Fatal("private spool path leaked")
		}
	}
	env["GO_MESSAGE_LIST_SPOOL_DIR"] = "$PRIVATE_MESSAGE_DIRECTORY"
	env["PRIVATE_MESSAGE_DIRECTORY"] = "/private/owned-message-list"
	cfg, err = Parse(env)
	if err != nil || cfg.MessageListSpoolDirectory != env["PRIVATE_MESSAGE_DIRECTORY"] {
		t.Fatal("environment indirection lost", err)
	}
	delete(env, "PRIVATE_MESSAGE_DIRECTORY")
	cfg, err = Parse(env)
	if err != nil || cfg.MessageListSpoolDirectory != "" {
		t.Fatal("missing optional proxy must retain default", err)
	}
	env["PRIVATE_MESSAGE_DIRECTORY"] = "$GO_MESSAGE_LIST_SPOOL_DIR"
	if _, err = Parse(env); err == nil || strings.Contains(err.Error(), "/private/") {
		t.Fatal("cycle must fail without disclosing values")
	}
	env["GO_MESSAGE_LIST_SPOOL_DIR"] = ""
	cfg, err = Parse(env)
	if err != nil || cfg.MessageListSpoolDirectory != "" {
		t.Fatal("explicit empty directory must retain native default", err)
	}
}

func TestMessageListSpoolDirectoryLoadPrecedence(t *testing.T) {
	for key, value := range environment() {
		t.Setenv(key, value)
	}
	path := filepath.Join(t.TempDir(), "message-list.env")
	if err := os.WriteFile(path, []byte("GO_MESSAGE_LIST_SPOOL_DIR=/private/from-file\n"), 0600); err != nil {
		t.Fatal(err)
	}
	// Start without a process override, restoring any inherited value at cleanup.
	t.Setenv("GO_MESSAGE_LIST_SPOOL_DIR", "")
	if err := os.Unsetenv("GO_MESSAGE_LIST_SPOOL_DIR"); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil || cfg.MessageListSpoolDirectory != "/private/from-file" {
		t.Fatal("file value ignored", err)
	}
	t.Setenv("GO_MESSAGE_LIST_SPOOL_DIR", "$MESSAGE_LIST_TEST_DIRECTORY")
	t.Setenv("MESSAGE_LIST_TEST_DIRECTORY", "/private/from-process")
	cfg, err = Load(path)
	if err != nil || cfg.MessageListSpoolDirectory != "/private/from-process" {
		t.Fatal("process override/expansion ignored", err)
	}
	t.Setenv("GO_MESSAGE_LIST_SPOOL_DIR", "")
	cfg, err = Load(path)
	if err != nil || cfg.MessageListSpoolDirectory != "" {
		t.Fatal("empty process value must override file", err)
	}
}
