package pdfexport

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"unicode/utf8"
)

// This file ports the ftp: URL read of reportlab's open_for_read
// (lib/utils.py: rlUrlRead is urlopen(name).read()) as CPython 3.13 runs it:
// urllib.request.FTPHandler.ftp_open with urllib.request.ftpwrapper and
// ftplib.FTP. The command sequence and the reply handling follow those classes.

// ftpMaxLine is ftplib.MAXLINE: the longest reply line ftplib reads.
const ftpMaxLine = 8192

// ftpReplyError is one of ftplib's exceptions: a 4xx or 5xx reply (error_temp
// and error_perm, the latter marked perm) or a reply of an unexpected code
// (error_reply and error_proto). The URL read turns every one of them into a
// failure.
type ftpReplyError struct {
	reply string
	code  string // the three-digit code of the reply
	perm  bool   // error_perm, a 5xx reply
}

func (e *ftpReplyError) Error() string {
	if e.reply == "" {
		return "ftp error"
	}
	return e.reply
}

// replyError is error_reply(resp).
func replyError(reply string) *ftpReplyError {
	return &ftpReplyError{reply: reply, code: ftpCode(reply)}
}

// ftpCode is resp[:3] of a reply.
func ftpCode(reply string) string {
	if len(reply) > 3 {
		return reply[:3]
	}
	return reply
}

// ftpClient is ftplib.FTP over one control connection.
type ftpClient struct {
	conn   net.Conn
	reader *bufio.Reader
}

// putLine is FTP.putline: the command and CRLF, refusing a command that carries
// a line break.
func (c *ftpClient) putLine(line string) error {
	if strings.ContainsAny(line, "\r\n") {
		return errors.New("an illegal newline character should not be contained")
	}
	_, err := c.conn.Write([]byte(line + "\r\n"))
	return err
}

// getLine is FTP.getline: one reply line without its newline, with ftplib's
// line-length limit; a closed connection is EOF. The line ends at LF, CRLF or a
// lone CR, as makefile('r', encoding='utf-8') reads it, and the limit counts
// characters, the newline included as one.
func (c *ftpClient) getLine() (string, error) {
	line := make([]rune, 0, 64)
	for len(line) <= ftpMaxLine {
		r, size, err := c.reader.ReadRune()
		if err != nil {
			if len(line) == 0 {
				return "", err
			}
			break
		}
		if r == utf8.RuneError && size == 1 {
			// makefile('r', encoding='utf-8') decodes strictly.
			return "", errors.New("invalid utf-8 in the reply")
		}
		line = append(line, r)
		if r == '\n' {
			break
		}
		if r == '\r' {
			// CRLF is one newline: Peek sees the LF without consuming it.
			if next, err := c.reader.Peek(1); err == nil && next[0] == '\n' {
				c.reader.Discard(1)
			}
			break
		}
	}
	if len(line) > ftpMaxLine {
		return "", fmt.Errorf("got more than %d bytes", ftpMaxLine)
	}
	text := strings.TrimSuffix(string(line), "\r")
	return strings.TrimSuffix(text, "\n"), nil
}

// getMultiline is FTP.getmultiline: a reply that continues while the first line
// is "NNN-" until a line starts with the same code and continues with anything
// but '-'.
func (c *ftpClient) getMultiline() (string, error) {
	line, err := c.getLine()
	if err != nil {
		return "", err
	}
	if len(line) >= 4 && line[3] == '-' {
		code := line[:3]
		for {
			next, err := c.getLine()
			if err != nil {
				return "", err
			}
			line += "\n" + next
			if len(next) >= 3 && next[:3] == code && (len(next) < 4 || next[3] != '-') {
				break
			}
		}
	}
	return line, nil
}

// getResp is FTP.getresp: the next reply; a 4xx or 5xx reply is an error.
func (c *ftpClient) getResp() (string, error) {
	resp, err := c.getMultiline()
	if err != nil {
		return "", err
	}
	if resp == "" {
		return "", replyError(resp)
	}
	switch resp[0] {
	case '1', '2', '3':
		return resp, nil
	case '4':
		return "", &ftpReplyError{reply: resp, code: ftpCode(resp)}
	case '5':
		return "", &ftpReplyError{reply: resp, code: ftpCode(resp), perm: true}
	}
	return "", replyError(resp)
}

// sendCmd is FTP.sendcmd: the command and the reply.
func (c *ftpClient) sendCmd(command string) (string, error) {
	if err := c.putLine(command); err != nil {
		return "", err
	}
	return c.getResp()
}

// voidCmd is FTP.voidcmd: the command and a reply that must start with '2'.
func (c *ftpClient) voidCmd(command string) (string, error) {
	resp, err := c.sendCmd(command)
	if err != nil {
		return "", err
	}
	if !strings.HasPrefix(resp, "2") {
		return "", replyError(resp)
	}
	return resp, nil
}

// close is FTP.close: the control connection.
func (c *ftpClient) close() {
	if c.conn != nil {
		c.conn.Close()
	}
}

// login is FTP.login: an empty user is anonymous, and an anonymous login
// without a password (or with "-") sends "anonymous@" as its password.
func (c *ftpClient) login(user, passwd string) error {
	if user == "" {
		user = "anonymous"
	}
	if user == "anonymous" && (passwd == "" || passwd == "-") {
		passwd += "anonymous@"
	}
	resp, err := c.sendCmd("USER " + user)
	if err != nil {
		return err
	}
	if resp[0] == '3' {
		if resp, err = c.sendCmd("PASS " + passwd); err != nil {
			return err
		}
	}
	if resp[0] == '3' {
		// FTP.login sends ACCT with the empty account ftpwrapper passes.
		if resp, err = c.sendCmd("ACCT "); err != nil {
			return err
		}
	}
	if resp[0] != '2' {
		return replyError(resp)
	}
	return nil
}

// cwd is FTP.cwd: ".." first asks CDUP, an empty name is ".".
func (c *ftpClient) cwd(dirname string) error {
	if dirname == ".." {
		_, err := c.voidCmd("CDUP")
		if err == nil {
			return nil
		}
		var failure *ftpReplyError
		if !errors.As(err, &failure) || !failure.perm || failure.code != "500" {
			return err
		}
	} else if dirname == "" {
		dirname = "."
	}
	_, err := c.voidCmd("CWD " + dirname)
	return err
}

// pwd is FTP.pwd: the directory a 257 reply names, or the empty text of a reply
// that follows no conventions.
func (c *ftpClient) pwd() (string, error) {
	resp, err := c.voidCmd("PWD")
	if err != nil {
		return "", err
	}
	if !strings.HasPrefix(resp, "257") {
		return "", nil
	}
	return parse257(resp), nil
}

// parse257 is ftplib.parse257.
func parse257(resp string) string {
	if len(resp) < 5 || resp[3:5] != " \"" {
		return ""
	}
	var dirname strings.Builder
	for i := 5; i < len(resp); {
		c := resp[i]
		i++
		if c == '"' {
			if i >= len(resp) || resp[i] != '"' {
				break
			}
			i++
		}
		dirname.WriteByte(c)
	}
	return dirname.String()
}

// peerHost is the host of the control connection, which is where the data
// connection goes: ftplib does not trust the address of a 227 reply.
func (c *ftpClient) peerHost() string {
	if address, ok := c.conn.RemoteAddr().(*net.TCPAddr); ok {
		return address.IP.String()
	}
	return ""
}

// makePASV is FTP.makepasv: PASV, then the peer host with the port of the
// reply. urllib resolves the host with socket.gethostbyname, so the control
// connection is always IPv4 and the EPSV branch of ftplib never runs.
func (c *ftpClient) makePASV() (string, int, error) {
	resp, err := c.sendCmd("PASV")
	if err != nil {
		return "", 0, err
	}
	port, ok := parse227(resp)
	if !ok {
		return "", 0, replyError(resp) // ftplib.parse227: error_proto
	}
	return c.peerHost(), port, nil
}

// parse227 is the port of ftplib.parse227: the six numbers of a 227 reply.
func parse227(resp string) (int, bool) {
	if ftpCode(resp) != "227" {
		return 0, false
	}
	for i := 0; i < len(resp); i++ {
		if resp[i] < '0' || resp[i] > '9' {
			continue
		}
		numbers := [6]int{}
		position, matched := i, true
		for group := 0; group < 6 && matched; group++ {
			start := position
			for position < len(resp) && resp[position] >= '0' && resp[position] <= '9' {
				position++
			}
			if start == position {
				matched = false
				break
			}
			value, err := strconv.Atoi(resp[start:position])
			if err != nil {
				matched = false
				break
			}
			numbers[group] = value
			if group < 5 {
				if position >= len(resp) || resp[position] != ',' {
					matched = false
					break
				}
				position++
			}
		}
		if matched {
			return numbers[4]<<8 + numbers[5], true
		}
	}
	return 0, false
}

// ftpDialFunc is the connection of ftpDial, replaceable in tests.
var ftpDialFunc = func(ctx context.Context, network, address string) (net.Conn, error) {
	return (&net.Dialer{}).DialContext(ctx, network, address)
}

// ftpDial opens a connection to address, as ftplib's socket.create_connection
// with no timeout does.
func ftpDial(ctx context.Context, host string, port int) (net.Conn, error) {
	return ftpDialFunc(ctx, "tcp4", net.JoinHostPort(host, strconv.Itoa(port)))
}

// connectFTP is ftplib.FTP.connect: the control connection and its welcome. A
// port that is not positive keeps FTP.port, which is 21.
func connectFTP(ctx context.Context, host string, port int) (*ftpClient, error) {
	if port <= 0 {
		port = 21
	}
	conn, err := ftpDial(ctx, host, port)
	if err != nil {
		return nil, err
	}
	client := &ftpClient{conn: conn, reader: bufio.NewReader(conn)}
	if _, err := client.getResp(); err != nil {
		client.close()
		return nil, err
	}
	return client, nil
}

// ftpSource is what urllib's FTPHandler makes of a URL: the login, the host,
// the directories to change to and the file to retrieve (empty for a
// directory), with the transfer type of the ;type= attribute.
type ftpSource struct {
	user, passwd string
	host         string
	port         int
	dirs         []string
	file         string
	typ          byte
}

// urllibUnwrap is urllib.parse.unwrap.
func urllibUnwrap(raw string) string {
	url := strings.TrimSpace(raw)
	if strings.HasPrefix(url, "<") && strings.HasSuffix(url, ">") {
		url = strings.TrimSpace(url[1 : len(url)-1])
	}
	if strings.HasPrefix(url, "URL:") {
		url = strings.TrimSpace(url[4:])
	}
	return url
}

// urllibSplitTag is urllib.parse._splittag: the URL before its last '#'.
func urllibSplitTag(url string) string {
	if index := strings.LastIndex(url, "#"); index >= 0 {
		return url[:index]
	}
	return url
}

// urllibSplitType is urllib.parse._splittype: the scheme and the rest.
func urllibSplitType(url string) (string, string, bool) {
	index := strings.Index(url, ":")
	if index <= 0 || strings.ContainsAny(url[:index], "/") {
		return "", url, false
	}
	return strings.ToLower(url[:index]), url[index+1:], true
}

// urllibSplitHost is urllib.parse._splithost: the authority and the selector
// of a "//..." URL.
func urllibSplitHost(rest string) (string, string, bool) {
	if !strings.HasPrefix(rest, "//") {
		return "", rest, false
	}
	rest = rest[2:]
	end := len(rest)
	for i := 0; i < len(rest); i++ {
		if rest[i] == '/' || rest[i] == '#' || rest[i] == '?' {
			end = i
			break
		}
	}
	path := rest[end:]
	if path != "" && path[0] != '/' {
		path = "/" + path
	}
	return rest[:end], path, true
}

// urllibSplitPort is urllib.parse._splitport: "host:port" with digits only
// after the last colon, an empty port counting as none.
func urllibSplitPort(host string) (string, string, bool) {
	index := strings.LastIndex(host, ":")
	if index < 0 {
		return host, "", false
	}
	port := host[index+1:]
	for i := 0; i < len(port); i++ {
		if port[i] < '0' || port[i] > '9' {
			return host, "", false
		}
	}
	if port == "" {
		return host[:index], "", false
	}
	return host[:index], port, true
}

// urllibSplitUser is urllib.parse._splituser: the user information before the
// last '@'.
func urllibSplitUser(host string) (string, string, bool) {
	if index := strings.LastIndex(host, "@"); index >= 0 {
		return host[:index], host[index+1:], true
	}
	return "", host, false
}

// urllibSplitPasswd is urllib.parse._splitpasswd: the password after the first
// ':'.
func urllibSplitPasswd(user string) (string, string, bool) {
	if index := strings.Index(user, ":"); index >= 0 {
		return user[:index], user[index+1:], true
	}
	return user, "", false
}

// urllibUnquote is urllib.parse.unquote: the percent escapes of a str decoded
// as UTF-8 with an invalid sequence replaced.
func urllibUnquote(text string) string {
	if !strings.Contains(text, "%") {
		return text
	}
	return strings.ToValidUTF8(string(unquoteToBytes(text)), "\uFFFD")
}

// parseFTPSource is urllib.request.FTPHandler.ftp_open's reading of the URL:
// the port (21 by default), the user and password, the host, the path split
// into the directories to change to and the file, and the transfer type.
func parseFTPSource(raw string) (ftpSource, error) {
	var source ftpSource
	url := urllibSplitTag(urllibUnwrap(raw))
	scheme, rest, _ := urllibSplitType(url)
	if scheme != "ftp" {
		return source, fmt.Errorf("unknown url type %q", scheme)
	}
	authority, selector, ok := urllibSplitHost(rest)
	if !ok || authority == "" {
		return source, errors.New("ftp error: no host given")
	}
	host, portText, hasPort := urllibSplitPort(urllibUnquote(authority))
	port := 21
	if hasPort {
		value, err := strconv.Atoi(portText)
		if err != nil || value > 65535 {
			return source, fmt.Errorf("port must be 0-65535")
		}
		port = value
	}
	user, host, hasUser := urllibSplitUser(host)
	passwd := ""
	if hasUser {
		user, passwd, _ = urllibSplitPasswd(user)
	}
	host = urllibUnquote(host)
	source.host, source.port = host, port
	source.user, source.passwd = user, passwd

	path, attrs, hasAttr := strings.Cut(selector, ";")
	segments := strings.Split(path, "/")
	for i := range segments {
		segments[i] = urllibUnquote(segments[i])
	}
	source.file = segments[len(segments)-1]
	source.dirs = append([]string(nil), segments[:len(segments)-1]...)
	if len(source.dirs) > 0 && source.dirs[0] == "" {
		source.dirs = source.dirs[1:]
	}
	source.typ = 'D'
	if source.file != "" {
		source.typ = 'I'
	}
	if hasAttr {
		for _, attribute := range strings.Split(attrs, ";") {
			name, value, hasValue := strings.Cut(attribute, "=")
			if !hasValue || strings.ToLower(name) != "type" {
				continue
			}
			switch value {
			case "a", "A", "i", "I", "d", "D":
				source.typ = byte(strings.ToUpper(value)[0])
			}
		}
	}
	return source, nil
}

// ftpResolve is socket.gethostbyname: the first IPv4 address of the host.
func ftpResolve(ctx context.Context, host string) (string, error) {
	addresses, err := net.DefaultResolver.LookupIP(ctx, "ip4", host)
	if err != nil {
		return "", err
	}
	if len(addresses) == 0 {
		return "", fmt.Errorf("no address for %s", host)
	}
	return addresses[0].String(), nil
}

// ftpWrapper is urllib.request.ftpwrapper: one login per file read, the
// directories entered, and the TYPE/RETR or TYPE/LIST pair of retrfile.
type ftpWrapper struct {
	source  ftpSource
	address string
	client  *ftpClient
}

// init is ftpwrapper.init: connect, log in, and change to the joined
// directories (an empty list changes to ".").
func (w *ftpWrapper) init(ctx context.Context) error {
	client, err := connectFTP(ctx, w.address, w.source.port)
	if err != nil {
		return err
	}
	if err := client.login(w.source.user, w.source.passwd); err != nil {
		client.close()
		return err
	}
	if err := client.cwd(strings.Join(w.source.dirs, "/")); err != nil {
		client.close()
		return err
	}
	w.client = client
	return nil
}

// reinit is the second ftpwrapper.init of retrfile: a new connection in place
// of the one whose TYPE command failed.
func (w *ftpWrapper) reinit(ctx context.Context) error {
	if w.client != nil {
		w.client.close()
	}
	w.client = nil
	return w.init(ctx)
}

// retrieve is ftplib.FTP.ntransfercmd and the read of the data connection: a
// passive connection to the control peer, the transfer command, then the bytes
// until the server closes the data connection.
func (w *ftpWrapper) retrieve(ctx context.Context, command string) ([]byte, error) {
	host, port, err := w.client.makePASV()
	if err != nil {
		return nil, err
	}
	conn, err := ftpDial(ctx, host, port)
	if err != nil {
		return nil, err
	}
	resp, err := w.client.sendCmd(command)
	if err != nil {
		conn.Close()
		return nil, err
	}
	if strings.HasPrefix(resp, "2") {
		// ftplib discards a 2xx reply before the 1xx one.
		if resp, err = w.client.getResp(); err != nil {
			conn.Close()
			return nil, err
		}
	}
	if !strings.HasPrefix(resp, "1") {
		conn.Close()
		return nil, replyError(resp)
	}
	data, err := io.ReadAll(conn)
	conn.Close()
	if err != nil {
		return nil, err
	}
	return data, nil
}

// retrfile is ftpwrapper.retrfile: the TYPE command (a failed one opens the
// connection again), RETR of a file, or the TYPE A + LIST fallback when the
// server answers RETR with a 550 reply. A directory name (the file name of a
// URL ending in '/') is always listed.
func (w *ftpWrapper) retrfile(ctx context.Context, file string, typ byte) ([]byte, error) {
	command, isdir := "TYPE "+string(typ), false
	if typ == 'd' || typ == 'D' {
		command, isdir = "TYPE A", true
	}
	if _, err := w.client.voidCmd(command); err != nil {
		if err := w.reinit(ctx); err != nil {
			return nil, err
		}
		if _, err := w.client.voidCmd(command); err != nil {
			return nil, err
		}
	}
	if file != "" && !isdir {
		data, err := w.retrieve(ctx, "RETR "+file)
		if err == nil {
			return data, nil
		}
		var failure *ftpReplyError
		if !errors.As(err, &failure) || !failure.perm || failure.code != "550" {
			return nil, err
		}
	}
	if _, err := w.client.voidCmd("TYPE A"); err != nil {
		return nil, err
	}
	listing := "LIST"
	if file != "" {
		pwd, err := w.client.pwd()
		if err != nil {
			return nil, err
		}
		changeErr := w.client.cwd(file)
		restoreErr := w.client.cwd(pwd)
		if restoreErr != nil {
			return nil, restoreErr
		}
		if changeErr != nil {
			return nil, changeErr
		}
		listing = "LIST " + file
	}
	return w.retrieve(ctx, listing)
}

// readFTPURL is urlopen of an ftp: URL and the read of its bytes.
func readFTPURL(ctx context.Context, name string) ([]byte, error) {
	source, err := parseFTPSource(name)
	if err != nil {
		return nil, err
	}
	address, err := ftpResolve(ctx, source.host)
	if err != nil {
		return nil, err
	}
	wrapper := &ftpWrapper{source: source, address: address}
	defer func() {
		if wrapper.client != nil {
			wrapper.client.close()
		}
	}()
	if err := wrapper.init(ctx); err != nil {
		return nil, err
	}
	return wrapper.retrfile(ctx, source.file, source.typ)
}
