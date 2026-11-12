package smtpsend

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"errors"
	"math/big"
	"net"
	"os"
	"reflect"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
)

type goldenReply struct {
	Reply       string `json:"reply"`
	StartTLS    bool   `json:"starttls"`
	ImplicitTLS bool   `json:"implicit_tls"`
}

type goldenUnit map[string]string

type goldenSMTP struct {
	Name   string `json:"name"`
	Config struct {
		User       string  `json:"email_smtp_user"`
		Password   string  `json:"email_smtp_password"`
		SenderName *string `json:"email_sender_name"`
		Encryption string  `json:"email_encryption"`
	} `json:"config"`
	Call string `json:"call"`
	Args *struct {
		Subject    string   `json:"subject"`
		Body       *string  `json:"body"`
		HTML       *string  `json:"html_body"`
		Recipients []string `json:"recipient_list"`
		ReplyTo    []string `json:"reply_to"`
	} `json:"args"`
	Scripts     [][]goldenReply `json:"scripts"`
	Transcripts [][]goldenUnit  `json:"transcripts"`
	OK          bool            `json:"ok"`
	Error       *string         `json:"error"`
	Saves       []int           `json:"saves"`
	Boundary    *string         `json:"boundary"`
}

type goldenFile struct {
	Python        string       `json:"python"`
	LocalHostname string       `json:"local_hostname"`
	SMTP          []goldenSMTP `json:"smtp"`
	MIME          []struct {
		Input struct {
			Name    *string  `json:"name"`
			User    string   `json:"user"`
			Subject string   `json:"subject"`
			To      []string `json:"to"`
			Reply   []string `json:"reply"`
			HTML    *string  `json:"html"`
			Text    *string  `json:"text"`
		} `json:"input"`
		OK      bool    `json:"ok"`
		Message *string `json:"message"`
	} `json:"mime"`
	Addresses []struct {
		Address string `json:"address"`
		Quoted  string `json:"quoted"`
	} `json:"addresses"`
}

func loadGolden(t *testing.T) goldenFile {
	t.Helper()
	raw, err := os.ReadFile("testdata/python_smtp.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden goldenFile
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if golden.Python == "" || len(golden.SMTP) == 0 || len(golden.MIME) == 0 || len(golden.Addresses) == 0 {
		t.Fatal("incomplete golden file")
	}
	return golden
}

func serverTLS(t *testing.T) *tls.Config {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "fake.smtp.test"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour)}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	return &tls.Config{Certificates: []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: key}}}
}

// scripted mirrors ScriptedServer in smtpsend_golden.py.
type scripted struct {
	listener    net.Listener
	transcripts [][]goldenUnit
	done        sync.WaitGroup
}

func startScripted(t *testing.T, scripts [][]goldenReply, config *tls.Config) *scripted {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	s := &scripted{listener: listener, transcripts: make([][]goldenUnit, 0, len(scripts))}
	s.done.Add(1)
	go func() {
		defer s.done.Done()
		for _, script := range scripts {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			units := []goldenUnit{}
			units = converse(conn, script, config, units)
			_ = conn.Close()
			s.transcripts = append(s.transcripts, units)
		}
	}()
	return s
}

func converse(raw net.Conn, script []goldenReply, config *tls.Config, units []goldenUnit) []goldenUnit {
	_ = raw.SetDeadline(time.Now().Add(5 * time.Second))
	var stream net.Conn = raw
	if len(script) > 0 && script[0].ImplicitTLS {
		stream = tls.Server(raw, config)
	}
	failed := func(err error) []goldenUnit {
		return append(units, goldenUnit{"event": "error", "detail": err.Error()})
	}
	replies := script
	if _, err := stream.Write([]byte(replies[0].Reply + "\r\n")); err != nil {
		return failed(err)
	}
	replies = replies[1:]
	var buffer []byte
	dataMode := false
	chunk := make([]byte, 65536)
	for {
		terminator := []byte("\n")
		if dataMode {
			terminator = []byte("\r\n.\r\n")
		}
		for !bytes.Contains(buffer, terminator) {
			n, err := stream.Read(chunk)
			buffer = append(buffer, chunk[:n]...)
			if err != nil {
				if n == 0 && !bytes.Contains(buffer, terminator) {
					if len(buffer) > 0 {
						units = append(units, goldenUnit{"bytes": latin1(buffer)})
					}
					return append(units, goldenUnit{"event": "eof"})
				}
			}
		}
		end := bytes.Index(buffer, terminator) + len(terminator)
		units = append(units, goldenUnit{"bytes": latin1(buffer[:end])})
		buffer = buffer[end:]
		if len(replies) == 0 {
			return append(units, goldenUnit{"event": "closed by server"})
		}
		reply := replies[0]
		replies = replies[1:]
		if _, err := stream.Write([]byte(reply.Reply + "\r\n")); err != nil {
			return failed(err)
		}
		dataMode = strings.HasPrefix(reply.Reply, "354")
		if reply.StartTLS {
			stream = tls.Server(stream, config)
			buffer = nil
		}
	}
}

func (s *scripted) port() int { return s.listener.Addr().(*net.TCPAddr).Port }

func (s *scripted) finish(t *testing.T) [][]goldenUnit {
	t.Helper()
	finished := make(chan struct{})
	go func() { s.done.Wait(); close(finished) }()
	select {
	case <-finished:
	case <-time.After(10 * time.Second):
		t.Fatal("scripted server did not finish")
	}
	_ = s.listener.Close()
	return s.transcripts
}

func errorKind(err error) *string {
	if err == nil {
		return nil
	}
	var smtpErr *Error
	if errors.As(err, &smtpErr) {
		return &smtpErr.Kind
	}
	kind := err.Error()
	return &kind
}

func TestPythonSMTPTranscripts(t *testing.T) {
	golden := loadGolden(t)
	config := serverTLS(t)
	for _, item := range golden.SMTP {
		t.Run(item.Name, func(t *testing.T) {
			server := startScripted(t, item.Scripts, config)
			target := Server{Host: "127.0.0.1", Port: server.port(), User: item.Config.User, Password: item.Config.Password,
				Encryption: item.Config.Encryption, Timeout: 5 * time.Second, LocalHostname: golden.LocalHostname}
			ctx := context.Background()
			var err error
			saves := []int{}
			switch item.Call {
			case "verify":
				err = Verify(ctx, target)
			case "send":
				if err = Verify(ctx, target); err != nil {
					t.Fatalf("send cases verify first: %v", err)
				}
				message := Message{SenderUser: item.Config.User, To: item.Args.Recipients, Subject: item.Args.Subject, ReplyTo: item.Args.ReplyTo}
				if item.Config.SenderName != nil {
					message.SenderName = *item.Config.SenderName
				}
				if item.Args.HTML != nil {
					message.HTML = *item.Args.HTML
				}
				if item.Args.Body != nil {
					message.Text = *item.Args.Body
				}
				boundary := NewBoundary()
				if item.Boundary != nil {
					boundary = *item.Boundary
				}
				rendered, renderErr := Render(message, boundary)
				if renderErr != nil {
					t.Fatal(renderErr)
				}
				if err = Send(ctx, target, item.Config.User, item.Args.Recipients, rendered); err == nil {
					saves = append(saves, 1)
				}
			default:
				t.Fatalf("unknown call %q", item.Call)
			}
			transcripts := server.finish(t)
			if !reflect.DeepEqual(transcripts, item.Transcripts) {
				got, _ := json.MarshalIndent(transcripts, "", " ")
				want, _ := json.MarshalIndent(item.Transcripts, "", " ")
				t.Fatalf("client bytes differ from Python\ngo: %s\npython: %s", got, want)
			}
			if (err == nil) != item.OK || !reflect.DeepEqual(errorKind(err), item.Error) || !reflect.DeepEqual(saves, item.Saves) {
				t.Fatalf("outcome: go err=%v saves=%v; python ok=%v error=%v saves=%v", err, saves, item.OK, item.Error, item.Saves)
			}
		})
	}
}

var boundaryPattern = regexp.MustCompile(`boundary="([^"]+)"`)

func TestRenderMatchesPythonAsString(t *testing.T) {
	golden := loadGolden(t)
	accepted, refused := 0, 0
	for i, item := range golden.MIME {
		input := item.Input
		if (input.HTML == nil || *input.HTML == "") && input.Text == nil {
			continue // MIMEText(None) raises; the Go API takes strings only
		}
		message := Message{SenderUser: input.User, To: input.To, Subject: input.Subject, ReplyTo: input.Reply}
		if input.Name != nil {
			message.SenderName = *input.Name
		}
		if input.HTML != nil {
			message.HTML = *input.HTML
		}
		if input.Text != nil {
			message.Text = *input.Text
		}
		boundary := NewBoundary()
		if item.Message != nil {
			boundary = boundaryPattern.FindStringSubmatch(*item.Message)[1]
		}
		rendered, err := Render(message, boundary)
		if err != nil {
			if !errors.Is(err, ErrUnsupported) {
				t.Fatalf("%d: %v", i, err)
			}
			if i < 8 {
				t.Fatalf("authored sample %d refused", i)
			}
			refused++
			continue
		}
		if !item.OK || item.Message == nil {
			t.Fatalf("%d: Python failed but Go rendered %q", i, rendered)
		}
		if rendered != *item.Message {
			t.Fatalf("%d: render differs\ngo:     %q\npython: %q", i, rendered, *item.Message)
		}
		accepted++
	}
	if accepted < 150 || refused == 0 {
		t.Fatalf("coverage: accepted %d refused %d", accepted, refused)
	}
	t.Logf("accepted %d, refused %d", accepted, refused)
}

func TestCheckEnvelopeMatchesQuoteaddr(t *testing.T) {
	golden := loadGolden(t)
	accepted := 0
	for _, item := range golden.Addresses {
		if CheckEnvelope(item.Address, nil) != nil {
			continue
		}
		accepted++
		if quoteaddr(item.Address) != item.Quoted {
			t.Fatalf("%q: go %q python %q", item.Address, quoteaddr(item.Address), item.Quoted)
		}
	}
	if accepted < 1000 {
		t.Fatalf("only %d addresses accepted", accepted)
	}
	for _, login := range []string{"apikey", "user.name"} {
		if CheckEnvelope(login, nil) != nil {
			t.Fatalf("%q refused", login)
		}
	}
}

func TestRefusalsBeforeConnecting(t *testing.T) {
	dial := func(context.Context, string, string) (net.Conn, error) {
		t.Fatal("refused input must not connect")
		return nil, nil
	}
	server := Server{Host: "127.0.0.1", Port: 25, Dial: dial}
	for _, args := range []struct {
		from string
		to   []string
		body string
	}{
		{"a@b", []string{"x y@z"}, "m"}, {"<a@b>", []string{"c@d"}, "m"}, {"a@b", []string{"c@d@e"}, "m"},
		{"a@b", []string{"\"q\"@x"}, "m"}, {"a@b", []string{"c@d"}, "è"}, {"a@b", []string{""}, "m"},
		{"api key", []string{"c@d"}, "m"}, {"a@b", []string{"@d"}, "m"}, {"a@b", []string{"c@"}, "m"},
	} {
		if err := Send(context.Background(), server, args.from, args.to, args.body); !errors.Is(err, ErrUnsupported) {
			t.Fatalf("%+v: %v", args, err)
		}
	}
	base := Message{SenderUser: "u@x", To: []string{"a@x"}, Subject: "s", HTML: "<p>"}
	for name, mutate := range map[string]func(*Message){
		"subject newline":   func(m *Message) { m.Subject = "a\nb" },
		"subject separator": func(m *Message) { m.Subject = "a b" },
		"name vertical tab": func(m *Message) { m.SenderName = "a\vb" },
		"reply invalid":     func(m *Message) { m.ReplyTo = []string{"\xff"} },
		"body invalid":      func(m *Message) { m.HTML = "\xff" },
		"long subject":      func(m *Message) { m.Subject = strings.Repeat("é", maxHeaderRunes+1) },
	} {
		message := base
		mutate(&message)
		if _, err := Render(message, NewBoundary()); !errors.Is(err, ErrUnsupported) {
			t.Fatalf("%s: %v", name, err)
		}
	}
}

func TestNewBoundaryShape(t *testing.T) {
	pattern := regexp.MustCompile(`^={15}\d{19}==$`)
	for range 100 {
		if b := NewBoundary(); !pattern.MatchString(b) {
			t.Fatal(b)
		}
	}
	text := "--" + "=b==" + "\n"
	if got := uniqueBoundary("=b==", text); got != "=b==.0" {
		t.Fatal(got)
	}
}

func TestPythonHelpers(t *testing.T) {
	for input, want := range map[string]int{"250": 250, " 25": 25, "2_5": 25, "+25": 25, "-25": -25, "25\r": 25} {
		if got, ok := pythonInt([]byte(input)); !ok || got != want {
			t.Fatalf("%q: %d %v", input, got, ok)
		}
	}
	for _, input := range []string{"", "2__5", "_25", "25_", "abc", "2 5", "+", "2\x005"} {
		if _, ok := pythonInt([]byte(input)); ok {
			t.Fatalf("%q accepted", input)
		}
	}
	for input, want := range map[string]string{"QUJD": "ABC", "QUI=": "AB", "QQ==": "A", "Q Q=\n=": "A", "Q!U#J$D": "ABC", "QQ==QQ==": "A\x04\x10",
		"QUJD=QUJD": "ABCABC", "QUI=Q": "AB\x10", "QUI==": "AB", "Q=Q==": "A", "==QUJD": "ABC", "QQ==QQ": "A\x04\x10"} {
		got, err := pythonB64Decode([]byte(input))
		if err != nil || string(got) != want {
			t.Fatalf("%q: %q %v", input, got, err)
		}
	}
	for _, input := range []string{"Q", "QUJDQ", "QU", "QUI=QUJD", "QUI=QUI=", "QQ==QUJD", "QQ=Q", "QUI=QU", "QQ==QUI=", "QUI=QQ=="} {
		if _, err := pythonB64Decode([]byte(input)); err == nil {
			t.Fatalf("%q decoded", input)
		}
	}
}

func TestContextCancelsDialogue(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go func() {
		conn, err := listener.Accept()
		if err == nil {
			defer conn.Close()
			time.Sleep(2 * time.Second) // never greets
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	started := time.Now()
	err = Verify(ctx, Server{Host: "127.0.0.1", Port: listener.Addr().(*net.TCPAddr).Port, Timeout: 5 * time.Second, LocalHostname: "h"})
	if err == nil || time.Since(started) > time.Second {
		t.Fatalf("%v after %v", err, time.Since(started))
	}
}
