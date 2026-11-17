package pdfexport

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

// The ftp: transcripts come from CPython 3.13.16 with reportlab 4.2.5
// (TESTS/fixtures/generate_ftp_transcripts.py): for every scenario, the
// commands urllib's urlopen sends and the bytes it reads or the exception it
// raises. The server below is the same scripted server as the generator's, and
// the client under test is Resource.Open.

type ftpTranscriptFile struct {
	Cases []ftpTranscript `json:"cases"`
}

type ftpTranscript struct {
	Name    string    `json:"name"`
	URL     string    `json:"url"`
	Welcome []string  `json:"welcome"`
	Rules   []ftpRule `json:"rules"`
	Default []string  `json:"default"`
	// Commands and Outcome are what urlopen did.
	Commands []string `json:"commands"`
	Outcome  struct {
		Base64 string `json:"base64"`
		Error  string `json:"error"`
	} `json:"outcome"`
}

type ftpRule struct {
	Command string   `json:"command"`
	Replies []string `json:"replies"`
	PASV    *struct {
		Host string `json:"host"`
	} `json:"pasv"`
	Data  *string  `json:"data"`
	Final []string `json:"final"`
}

// ftpScriptServer answers the commands of one transcript.
type ftpScriptServer struct {
	listener net.Listener
	mu       sync.Mutex
	welcome  []string
	rules    []ftpRule
	fallback []string
	commands []string
	pasv     net.Listener
}

// sendFTPLines writes the reply lines with CRLF.
func sendFTPLines(w *bufio.Writer, lines []string) error {
	for _, line := range lines {
		if _, err := w.WriteString(line + "\r\n"); err != nil {
			return err
		}
	}
	return w.Flush()
}

// takeFTPRule removes and returns the first rule of a command, as the scripted
// server consumes its rules.
func (s *ftpScriptServer) takeFTPRule(command string) *ftpRule {
	s.mu.Lock()
	defer s.mu.Unlock()
	for index := range s.rules {
		if s.rules[index].Command == command {
			rule := s.rules[index]
			s.rules = append(s.rules[:index], s.rules[index+1:]...)
			return &rule
		}
	}
	return nil
}

// setFTPPassive keeps the listener of a PASV reply, closing the one it
// replaces.
func (s *ftpScriptServer) setFTPPassive(listener net.Listener) net.Listener {
	s.mu.Lock()
	defer s.mu.Unlock()
	previous := s.pasv
	s.pasv = listener
	return previous
}

func (s *ftpScriptServer) takeFTPPassive() net.Listener {
	s.mu.Lock()
	defer s.mu.Unlock()
	listener := s.pasv
	s.pasv = nil
	return listener
}

// handleFTPConnection is the body of the generator's Handler.handle.
func (s *ftpScriptServer) handleFTPConnection(conn net.Conn) {
	defer conn.Close()
	reader := bufio.NewReader(conn)
	writer := bufio.NewWriter(conn)
	if sendFTPLines(writer, s.welcome) != nil {
		return
	}
	for {
		line, err := reader.ReadString('\n')
		if err != nil && line == "" {
			return
		}
		command := strings.TrimRight(line, "\r\n")
		s.mu.Lock()
		s.commands = append(s.commands, command)
		s.mu.Unlock()
		rule := s.takeFTPRule(command)
		if rule == nil {
			if sendFTPLines(writer, s.fallback) != nil {
				return
			}
			continue
		}
		replies := rule.Replies
		if rule.PASV != nil {
			listener, err := net.Listen("tcp4", "127.0.0.1:0")
			if err != nil {
				return
			}
			if previous := s.setFTPPassive(listener); previous != nil {
				previous.Close()
			}
			port := listener.Addr().(*net.TCPAddr).Port
			replies = make([]string, len(rule.Replies))
			for i, reply := range rule.Replies {
				replies[i] = strings.NewReplacer("{p1}", strconv.Itoa(port>>8), "{p2}", strconv.Itoa(port&0xff)).Replace(reply)
			}
		}
		if sendFTPLines(writer, replies) != nil {
			return
		}
		if rule.Data == nil {
			continue
		}
		listener := s.takeFTPPassive()
		if listener != nil {
			listener.(*net.TCPListener).SetDeadline(time.Now().Add(5 * time.Second))
			dataConn, err := listener.Accept()
			listener.Close()
			if err == nil {
				payload, decodeErr := base64.StdEncoding.DecodeString(*rule.Data)
				if decodeErr == nil {
					dataConn.Write(payload)
				}
				dataConn.Close()
			}
		}
		if sendFTPLines(writer, rule.Final) != nil {
			return
		}
	}
}

// serveFTPTranscript starts the scripted server of a case and answers it with
// the URL of its port.
func serveFTPTranscript(t *testing.T, transcript ftpTranscript) (string, *ftpScriptServer) {
	t.Helper()
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	server := &ftpScriptServer{
		listener: listener,
		welcome:  transcript.Welcome,
		rules:    append([]ftpRule(nil), transcript.Rules...),
		fallback: transcript.Default,
	}
	if len(server.fallback) == 0 {
		server.fallback = []string{"500 Command not understood"}
	}
	t.Cleanup(func() {
		listener.Close()
		if pasv := server.takeFTPPassive(); pasv != nil {
			pasv.Close()
		}
	})
	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			go server.handleFTPConnection(conn)
		}
	}()
	port := listener.Addr().(*net.TCPAddr).Port
	return strings.ReplaceAll(transcript.URL, "{port}", strconv.Itoa(port)), server
}

func (s *ftpScriptServer) receivedCommands() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.commands...)
}

// TestFTPImageSourceTranscripts replays every scenario of
// TESTS/fixtures/generate_ftp_transcripts.py: the same commands as urllib and
// the same bytes or a failure.
func TestFTPImageSourceTranscripts(t *testing.T) {
	raw, err := os.ReadFile("testdata/ftp_transcripts.json")
	if err != nil {
		t.Fatal(err)
	}
	var file ftpTranscriptFile
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatal(err)
	}
	if len(file.Cases) < 10 {
		t.Fatalf("%d transcripts, want at least 10", len(file.Cases))
	}
	for _, transcript := range file.Cases {
		transcript := transcript
		t.Run(transcript.Name, func(t *testing.T) {
			url, server := serveFTPTranscript(t, transcript)
			data, err := (DefaultResourceOpener{}).Open(context.Background(), url)
			got := server.receivedCommands()
			if strings.Join(got, "\n") != strings.Join(transcript.Commands, "\n") {
				t.Errorf("commands %q, want %q", got, transcript.Commands)
			}
			if transcript.Outcome.Error != "" {
				if err == nil {
					t.Fatalf("read %q, want an error (%s)", data, transcript.Outcome.Error)
				}
				return
			}
			if err != nil {
				t.Fatalf("%v (urlopen raises no exception)", err)
			}
			want, err := base64.StdEncoding.DecodeString(transcript.Outcome.Base64)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(data, want) {
				t.Fatalf("read %q, want %q", data, want)
			}
		})
	}
}
