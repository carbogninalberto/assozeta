package s3credentials

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func shellQuote(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'" }

func TestProcessSourceThroughNestedRoles(t *testing.T) {
	directory := isolatedEnvironment(t)
	os.Setenv("PATH", "/usr/bin:/bin")
	counter := filepath.Join(directory, "calls")
	script := filepath.Join(directory, "provider.sh")
	body := "printf x >> " + shellQuote(counter) + "\nprintf '%s' '{\"Version\":1,\"AccessKeyId\":\"process-access\",\"SecretAccessKey\":\"process-secret\"}'\n"
	if err := os.WriteFile(script, []byte(body), 0700); err != nil {
		t.Fatal(err)
	}
	config := "[profile outer]\nrole_arn=arn:aws:iam::123456789012:role/outer\nsource_profile=middle\nrole_session_name=outer-session\nduration_seconds=3600\nexternal_id=outer-external\n[profile middle]\nrole_arn=arn:aws:iam::123456789012:role/middle\nsource_profile=process\nrole_session_name=middle-session\nduration_seconds=3600\n[profile process]\ncredential_process=/bin/sh " + shellQuote(script) + "\n"
	if err := os.WriteFile(filepath.Join(directory, "config"), []byte(config), 0600); err != nil {
		t.Fatal(err)
	}
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if err := r.ParseForm(); err != nil {
			t.Error("invalid STS form")
		}
		role := "middle"
		source := "process"
		if strings.HasSuffix(r.Form.Get("RoleArn"), "/outer") {
			role = "outer"
			source = "middle"
			if r.Form.Get("ExternalId") != "outer-external" {
				t.Error("lost role external ID")
			}
		}
		if r.Form.Get("Action") != "AssumeRole" || r.Form.Get("DurationSeconds") != "3600" || r.Form.Get("RoleSessionName") != role+"-session" {
			t.Error("role request differs")
		}
		if !strings.Contains(r.Header.Get("Authorization"), "Credential="+source+"-access/") {
			t.Error("role used wrong source credentials")
		}
		w.Header().Set("Content-Type", "text/xml")
		fmt.Fprintf(w, `<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><AssumeRoleResult><Credentials><AccessKeyId>%s-access</AccessKeyId><SecretAccessKey>%s-secret</SecretAccessKey><SessionToken>%s-token</SessionToken><Expiration>%s</Expiration></Credentials><AssumedRoleUser><Arn>arn:aws:sts::123456789012:assumed-role/%s/session</Arn><AssumedRoleId>fixture:session</AssumedRoleId></AssumedRoleUser></AssumeRoleResult></AssumeRoleResponse>`, role, role, role, time.Now().Add(time.Hour).UTC().Format(time.RFC3339), role)
	}))
	defer server.Close()
	os.Setenv("AWS_ENDPOINT_URL_STS", server.URL)
	os.Setenv("AWS_REGION", "us-east-1")
	provider, err := New(context.Background(), Options{Profile: "outer"}, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	var group sync.WaitGroup
	for i := 0; i < 32; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			credentials, err := provider.Retrieve(context.Background())
			if err != nil || credentials.AccessKeyID != "outer-access" || credentials.SessionToken != "outer-token" {
				t.Error("nested role credentials differ", err)
			}
		}()
	}
	group.Wait()
	raw, err := os.ReadFile(counter)
	if err != nil || string(raw) != "x" || calls.Load() != 2 {
		t.Fatal("nested process/role request bound differs", calls.Load(), err)
	}
}

func TestCredentialProcessCancellationKillsGroup(t *testing.T) {
	directory := isolatedEnvironment(t)
	os.Setenv("PATH", "/usr/bin:/bin")
	ready := filepath.Join(directory, "ready")
	command := "sleep 60 & printf x > " + shellQuote(ready) + "; wait"
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	cmd, err := credentialCommand(ctx, command)
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Run() }()
	deadline := time.Now().Add(2 * time.Second)
	for {
		if _, err := os.Stat(ready); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("credential child did not start")
		}
		time.Sleep(5 * time.Millisecond)
	}
	start := time.Now()
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled process succeeded")
		}
	case <-time.After(750 * time.Millisecond):
		t.Fatal("process group survived cancellation")
	}
	if time.Since(start) >= time.Second {
		t.Fatal("process only stopped through pipe wait fallback")
	}
}
