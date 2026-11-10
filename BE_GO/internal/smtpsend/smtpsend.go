// Package smtpsend sends mail the way CommunicationConfiguration does in
// BE/communications/models.py: Python 3.13 smtplib for the dialogue and
// email.mime (compat32, as_string) for the message, not Django's EmailMessage.
//
// Verify mirrors verify_smtp(send_email=False): connect (SMTP_SSL, or SMTP
// followed by starttls() when the encryption is "TLS"), login, then the
// method returns without quit(), so the socket is closed when the SMTP object
// is released. Send mirrors the second connection of send_email: connect,
// starttls, login, sendmail(user, recipients, message), quit. Render mirrors
// the MIMEMultipart that send_email builds and its as_string().
//
// TLS follows smtplib's default context (ssl._create_stdlib_context), with
// the host sent as the server name. Each socket operation has its own timeout, like a Python
// socket timeout.
//
// Golden evidence: TESTS/adapters/go/smtpsend_golden.py records the client
// bytes of the actual Django methods against a scripted server, the
// send_email messages and smtplib.quoteaddr results in
// testdata/python_smtp.json; the tests replay the same scripts against this
// client and require identical bytes.
package smtpsend

import (
	"bufio"
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/md5"
	"crypto/tls"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// ErrUnsupported marks input outside the subset proven against Python; it is
// returned before any connection is made.
var ErrUnsupported = errors.New("smtpsend: input outside the supported subset")

// Server is the SMTP part of a CommunicationConfiguration.
type Server struct {
	Host       string
	Port       int
	User       string
	Password   string
	Encryption string // "SSL", "TLS"; any other value is a plain connection
	// Timeout bounds the connect and each read or write (SMTP_TIMEOUT_SECONDS).
	Timeout time.Duration
	// LocalHostname is the EHLO/HELO argument; empty means LocalName().
	LocalHostname string
	Dial          func(ctx context.Context, network, address string) (net.Conn, error)
	// TLSConfig, when set, replaces smtplib's unverified default context
	// (ssl.create_default_context(), as Django's SMTP backend passes); its
	// ServerName defaults to Host.
	TLSConfig *tls.Config
}

// Error is a failure Python raises from smtplib (or while encoding a command);
// Kind names the Python exception class.
type Error struct {
	Kind string
	Code int
	Msg  string
	// Text is the Python exception message of a failure without a reply
	// code, when it is a fixed text (see PythonMessage).
	Text string
	// Sender is SMTPSenderRefused's third argument (the from_addr given).
	Sender string
	// Refused are SMTPRecipientsRefused's recipients in command order.
	Refused []Refusal
}

// Refusal is one refused recipient with the server's reply.
type Refusal struct {
	Address string
	Code    int
	Msg     string
}

func (e *Error) Error() string {
	if e.Code != 0 {
		return fmt.Sprintf("smtpsend: %s (%d)", e.Kind, e.Code)
	}
	return "smtpsend: " + e.Kind
}

func failure(kind string) error { return &Error{Kind: kind} }

func failureText(kind, text string) error { return &Error{Kind: kind, Text: text} }

func replyFailure(kind string, code int, msg []byte) error {
	return &Error{Kind: kind, Code: code, Msg: string(msg)}
}

// Verify mirrors verify_smtp(send_email=False). The connection is closed
// without QUIT on success and on failure.
func Verify(ctx context.Context, s Server) error {
	c, err := open(ctx, s)
	if err != nil {
		return err
	}
	defer c.close()
	return c.login(s.User, s.Password)
}

// Send mirrors the delivery connection of send_email. from and to must pass
// CheckEnvelope and message must be ASCII (Render's output); otherwise
// ErrUnsupported is returned before connecting.
func Send(ctx context.Context, s Server, from string, to []string, message string) error {
	if err := CheckEnvelope(from, to); err != nil {
		return err
	}
	for i := 0; i < len(message); i++ {
		if message[i] >= 0x80 {
			return ErrUnsupported
		}
	}
	c, err := open(ctx, s)
	if err != nil {
		return err
	}
	defer c.close()
	if err = c.login(s.User, s.Password); err != nil {
		return err
	}
	if err = c.sendmail(from, to, message); err != nil {
		return err
	}
	return c.quit()
}

type client struct {
	ctx      context.Context
	raw      net.Conn
	conn     net.Conn
	reader   *bufio.Reader
	timeout  time.Duration
	host     string
	local    string
	features map[string]string
	order    []string
	esmtp    bool
	heloResp []byte
	ehloResp []byte
	helo     bool
	ehlo     bool
	count    int
	stop     func() bool
	tls      *tls.Config
	// handshake records a failed TLS handshake: Python has then detached
	// the plain socket, so a later QUIT cannot be sent.
	handshake bool
}

func open(ctx context.Context, s Server) (*client, error) {
	timeout := s.Timeout
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	dial := s.Dial
	if dial == nil {
		dialer := &net.Dialer{Timeout: timeout}
		dial = dialer.DialContext
	}
	connectCtx, cancel := context.WithTimeout(ctx, timeout)
	raw, err := dial(connectCtx, "tcp", net.JoinHostPort(s.Host, strconv.Itoa(s.Port)))
	cancel()
	if err != nil {
		return nil, err
	}
	c := &client{ctx: ctx, raw: raw, conn: raw, timeout: timeout, host: s.Host, features: map[string]string{}, tls: s.TLSConfig}
	c.stop = context.AfterFunc(ctx, func() { _ = raw.Close() })
	if s.Encryption == "SSL" {
		if err = c.wrap(); err != nil {
			c.close()
			return nil, err
		}
	}
	c.reader = bufio.NewReader(c.conn)
	code, msg, err := c.getreply()
	if err != nil {
		c.close()
		return nil, err
	}
	if code != 220 {
		c.close()
		return nil, replyFailure("SMTPConnectError", code, msg)
	}
	c.local = s.LocalHostname
	if c.local == "" {
		c.local = LocalName()
	}
	if s.Encryption == "TLS" {
		if err = c.starttls(); err != nil {
			c.close()
			return nil, err
		}
	}
	return c, nil
}

// wrap applies the settings of smtplib's default client context
// (ssl._create_stdlib_context), the host as server name.
func (c *client) wrap() error {
	config := &tls.Config{ServerName: c.host, InsecureSkipVerify: true, MinVersion: tls.VersionTLS12} //nolint:gosec // parity with smtplib's default context
	if c.tls != nil {
		config = c.tls.Clone()
		if config.ServerName == "" {
			config.ServerName = c.host
		}
	}
	conn := tls.Client(c.conn, config)
	_ = conn.SetDeadline(time.Now().Add(c.timeout))
	if err := conn.HandshakeContext(c.ctx); err != nil {
		c.handshake = true
		return err
	}
	_ = conn.SetDeadline(time.Time{})
	c.conn = conn
	return nil
}

// close releases the socket without a TLS close_notify, as Python's release
// of the SMTP object closes the file descriptor.
func (c *client) close() {
	if c.stop != nil {
		c.stop()
	}
	_ = c.raw.Close()
}

func (c *client) send(data []byte) error {
	_ = c.conn.SetWriteDeadline(time.Now().Add(c.timeout))
	if _, err := c.conn.Write(data); err != nil {
		if c.ctx.Err() != nil {
			return c.ctx.Err()
		}
		return failureText("SMTPServerDisconnected", "Server not connected")
	}
	return nil
}

// putcmd encodes with command_encoding 'ascii'; nothing is sent when the
// command has a line break or a non-ASCII character.
func (c *client) putcmd(cmd, args string) error {
	s := cmd
	if args != "" {
		s = cmd + " " + args
	}
	if strings.ContainsAny(s, "\r\n") {
		return failure("ValueError")
	}
	if !isASCII(s) {
		return failure("UnicodeEncodeError")
	}
	return c.send([]byte(s + "\r\n"))
}

const maxLine = 8192

func (c *client) readline() ([]byte, error) {
	var line []byte
	for len(line) < maxLine+1 {
		_ = c.conn.SetReadDeadline(time.Now().Add(c.timeout))
		b, err := c.reader.ReadByte()
		if err != nil {
			if c.ctx.Err() != nil {
				return nil, c.ctx.Err()
			}
			if len(line) > 0 && errors.Is(err, io.EOF) {
				// readline returns a final line without a terminator.
				return line, nil
			}
			return nil, err
		}
		line = append(line, b)
		if b == '\n' {
			break
		}
	}
	return line, nil
}

// getreply follows SMTP.getreply: lines up to 8192 bytes, the text after the
// code stripped of " \t\r\n", Python int() of the first three bytes.
func (c *client) getreply() (int, []byte, error) {
	var resp [][]byte
	code := -1
	for {
		line, err := c.readline()
		if err != nil && len(line) == 0 {
			c.close()
			if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
				return 0, nil, err
			}
			switch {
			case errors.Is(err, io.EOF):
				return 0, nil, failureText("SMTPServerDisconnected", "Connection unexpectedly closed")
			case errors.Is(err, os.ErrDeadlineExceeded) && c.conn == c.raw:
				// A plain socket's timeout text; ssl sockets word theirs differently.
				return 0, nil, failureText("SMTPServerDisconnected", "Connection unexpectedly closed: timed out")
			}
			return 0, nil, failure("SMTPServerDisconnected")
		}
		if len(line) > maxLine {
			c.close()
			return 0, nil, replyFailure("SMTPResponseException", 500, []byte("Line too long."))
		}
		rest := []byte{}
		if len(line) > 4 {
			rest = line[4:]
		}
		resp = append(resp, bytes.Trim(rest, " \t\r\n"))
		head := line
		if len(head) > 3 {
			head = head[:3]
		}
		value, ok := pythonInt(head)
		if !ok {
			code = -1
			break
		}
		code = value
		if len(line) < 4 || line[3] != '-' {
			break
		}
	}
	return code, bytes.Join(resp, []byte("\n")), nil
}

func (c *client) docmd(cmd, args string) (int, []byte, error) {
	if err := c.putcmd(cmd, args); err != nil {
		return 0, nil, err
	}
	return c.getreply()
}

var (
	oldStyleAuth = regexp.MustCompile(`(?i)^auth=(.*)`)
	featureLine  = regexp.MustCompile(`^([A-Za-z0-9][A-Za-z0-9\-]*) ?`)
)

func (c *client) sendEhlo() (int, []byte, error) {
	c.features, c.order = map[string]string{}, nil
	if err := c.putcmd("ehlo", c.local); err != nil {
		return 0, nil, err
	}
	code, msg, err := c.getreply()
	if err != nil {
		return 0, nil, err
	}
	if code == -1 && len(msg) == 0 {
		c.close()
		return 0, nil, failure("SMTPServerDisconnected")
	}
	c.ehloResp, c.ehlo = msg, true
	if code != 250 {
		return code, msg, nil
	}
	c.esmtp = true
	lines := strings.Split(latin1(msg), "\n")[1:]
	for _, each := range lines {
		if m := oldStyleAuth.FindStringSubmatch(each); m != nil {
			c.setFeature("auth", c.features["auth"]+" "+m[1])
			continue
		}
		if m := featureLine.FindStringSubmatchIndex(each); m != nil {
			feature := strings.ToLower(each[m[2]:m[3]])
			params := pythonStrip(each[m[3]:])
			if feature == "auth" {
				c.setFeature(feature, c.features[feature]+" "+params)
			} else {
				c.setFeature(feature, params)
			}
		}
	}
	return code, msg, nil
}

func (c *client) setFeature(name, value string) {
	if _, ok := c.features[name]; !ok {
		c.order = append(c.order, name)
	}
	c.features[name] = value
}

func (c *client) sendHelo() (int, []byte, error) {
	if err := c.putcmd("helo", c.local); err != nil {
		return 0, nil, err
	}
	code, msg, err := c.getreply()
	if err != nil {
		return 0, nil, err
	}
	c.heloResp, c.helo = msg, true
	return code, msg, nil
}

func (c *client) ehloOrHeloIfNeeded() error {
	if c.helo || c.ehlo {
		return nil
	}
	code, _, err := c.sendEhlo()
	if err != nil {
		return err
	}
	if code >= 200 && code <= 299 {
		return nil
	}
	code, msg, err := c.sendHelo()
	if err != nil {
		return err
	}
	if code < 200 || code > 299 {
		return replyFailure("SMTPHeloError", code, msg)
	}
	return nil
}

func (c *client) hasExtn(name string) bool {
	_, ok := c.features[strings.ToLower(name)]
	return ok
}

func (c *client) starttls() error {
	if err := c.ehloOrHeloIfNeeded(); err != nil {
		return err
	}
	if !c.hasExtn("starttls") {
		return failureText("SMTPNotSupportedError", "STARTTLS extension not supported by server.")
	}
	code, msg, err := c.docmd("STARTTLS", "")
	if err != nil {
		return err
	}
	if code != 220 {
		return replyFailure("SMTPResponseException", code, msg)
	}
	if err = c.wrap(); err != nil {
		return err
	}
	c.reader = bufio.NewReader(c.conn)
	c.helo, c.ehlo, c.heloResp, c.ehloResp = false, false, nil, nil
	c.features, c.order, c.esmtp = map[string]string{}, nil, false
	return nil
}

const maxChallenge = 5

func (c *client) login(user, password string) error {
	if err := c.ehloOrHeloIfNeeded(); err != nil {
		return err
	}
	if !c.hasExtn("auth") {
		return failureText("SMTPNotSupportedError", "SMTP AUTH extension not supported by server.")
	}
	advertised := pythonSplit(c.features["auth"])
	var methods []string
	for _, preferred := range []string{"CRAM-MD5", "PLAIN", "LOGIN"} {
		for _, item := range advertised {
			if item == preferred {
				methods = append(methods, preferred)
				break
			}
		}
	}
	if len(methods) == 0 {
		return failureText("SMTPException", "No suitable authentication method found.")
	}
	var last error
	for _, method := range methods {
		err := c.auth(method, user, password)
		if err == nil {
			return nil
		}
		var smtpErr *Error
		if errors.As(err, &smtpErr) && smtpErr.Kind == "SMTPAuthenticationError" {
			last = err
			continue
		}
		return err
	}
	return last
}

// authObject returns the authobject's answer; nil means None.
func (c *client) authObject(method, user, password string, challenge []byte) (*string, error) {
	switch method {
	case "CRAM-MD5":
		if challenge == nil {
			return nil, nil
		}
		if !isASCII(password) {
			return nil, failure("UnicodeEncodeError")
		}
		mac := hmac.New(md5.New, []byte(password))
		mac.Write(challenge)
		answer := user + " " + hex.EncodeToString(mac.Sum(nil))
		return &answer, nil
	case "PLAIN":
		answer := "\x00" + user + "\x00" + password
		return &answer, nil
	default:
		answer := password
		if challenge == nil || c.count < 2 {
			answer = user
		}
		return &answer, nil
	}
}

func encodeASCII(s string) (string, error) {
	if !isASCII(s) {
		return "", failure("UnicodeEncodeError")
	}
	return base64.StdEncoding.EncodeToString([]byte(s)), nil
}

func (c *client) auth(method, user, password string) error {
	initial, err := c.authObject(method, user, password, nil)
	if err != nil {
		return err
	}
	var code int
	var resp []byte
	if initial != nil {
		encoded, err := encodeASCII(*initial)
		if err != nil {
			return err
		}
		if code, resp, err = c.docmd("AUTH", method+" "+encoded); err != nil {
			return err
		}
		c.count = 1
	} else {
		if code, resp, err = c.docmd("AUTH", method); err != nil {
			return err
		}
		c.count = 0
	}
	for code == 334 {
		c.count++
		challenge, err := pythonB64Decode(resp)
		if err != nil {
			return err
		}
		answer, err := c.authObject(method, user, password, challenge)
		if err != nil {
			return err
		}
		encoded, err := encodeASCII(*answer)
		if err != nil {
			return err
		}
		if code, resp, err = c.docmd(encoded, ""); err != nil {
			return err
		}
		if c.count > maxChallenge {
			return failure("SMTPException")
		}
	}
	if code == 235 || code == 503 {
		return nil
	}
	return replyFailure("SMTPAuthenticationError", code, resp)
}

func (c *client) rset() {
	// _rset ignores a disconnection; any other error still propagates in
	// Python but its only source here is the socket, so it is ignored too.
	_, _, _ = c.docmd("rset", "")
}

// sendmail follows SMTP.sendmail for a str message without options.
func (c *client) sendmail(from string, to []string, message string) error {
	return c.sendmailBytes(from, to, []byte(fixEOLs(message)))
}

// sendmailBytes follows SMTP.sendmail for a bytes message (sent as given)
// without options.
func (c *client) sendmailBytes(from string, to []string, data []byte) error {
	return c.sendmailFrom(from, from, to, data)
}

// sendmailFrom is sendmailBytes for a from_addr whose envelope address
// (smtplib.quoteaddr's parse) is given separately; given is the from_addr
// SMTPSenderRefused reports.
func (c *client) sendmailFrom(given, from string, to []string, data []byte) error {
	if err := c.ehloOrHeloIfNeeded(); err != nil {
		return err
	}
	var options string
	if c.esmtp && c.hasExtn("size") {
		options = " size=" + strconv.Itoa(len(data))
	}
	code, msg, err := c.docmd("mail", "from:"+quoteaddr(from)+options)
	if err != nil {
		return err
	}
	if code != 250 {
		if code == 421 {
			c.close()
		} else {
			c.rset()
		}
		return &Error{Kind: "SMTPSenderRefused", Code: code, Msg: string(msg), Sender: given}
	}
	var refused []Refusal
	for _, each := range to {
		code, msg, err := c.docmd("rcpt", "to:"+quoteaddr(each))
		if err != nil {
			return err
		}
		if code != 250 && code != 251 {
			refused = append(refused, Refusal{Address: each, Code: code, Msg: string(msg)})
		}
		if code == 421 {
			c.close()
			return &Error{Kind: "SMTPRecipientsRefused", Refused: refused}
		}
	}
	if len(refused) == len(to) {
		c.rset()
		return &Error{Kind: "SMTPRecipientsRefused", Refused: refused}
	}
	code, msg, err = c.docmd("data", "")
	if err != nil {
		return err
	}
	if code != 354 {
		return replyFailure("SMTPDataError", code, msg)
	}
	quoted := quotePeriods(data)
	if !bytes.HasSuffix(quoted, []byte("\r\n")) {
		quoted = append(quoted, '\r', '\n')
	}
	quoted = append(quoted, '.', '\r', '\n')
	if err = c.send(quoted); err != nil {
		return err
	}
	code, msg, err = c.getreply()
	if err != nil {
		return err
	}
	if code != 250 {
		if code == 421 {
			c.close()
		} else {
			c.rset()
		}
		return replyFailure("SMTPDataError", code, msg)
	}
	return nil
}

func (c *client) quit() error {
	_, _, err := c.docmd("quit", "")
	c.close()
	return err
}

var eolPattern = regexp.MustCompile(`\r\n|\n|\r`)

// fixEOLs is smtplib._fix_eols: \r\n, \n and a lone \r become \r\n.
func fixEOLs(s string) string { return eolPattern.ReplaceAllString(s, "\r\n") }

var periodPattern = regexp.MustCompile(`(?m)^\.`)

func quotePeriods(b []byte) []byte { return periodPattern.ReplaceAll(b, []byte("..")) }

// quoteaddr is smtplib.quoteaddr for addresses CheckEnvelope accepts, which
// email.utils.parseaddr returns unchanged.
func quoteaddr(address string) string { return "<" + address + ">" }
