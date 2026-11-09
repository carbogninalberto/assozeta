// Package pdfrender is the client of the Puppeteer FastRender service that
// Django's application.utils.printing calls (FastRender.get_pdf through
// Puppeteer.pdf_from_url): one POST of the same JSON payload to
// http://PUPPETEER_HOST:PUPPETEER_PORT/render, sent with the requests
// library's default headers, and the same acceptance rules for the answer.
// The service fetches CURRENT_HOST_PUPPETEER/<view path> itself; this client
// never renders HTML.
package pdfrender

import (
	"bytes"
	"compress/flate"
	"compress/gzip"
	"compress/zlib"
	"context"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"strconv"
	"strings"
	"time"
	"unicode/utf16"
	"unicode/utf8"

	"golang.org/x/text/encoding/charmap"
)

// RequestsUserAgent is requests.utils.default_user_agent() for the pinned
// requests release (BE/requirements.txt: requests==2.34.2).
const RequestsUserAgent = "python-requests/2.34.2"

// Client posts render requests; its zero HTTP client gets a 60 s timeout
// (requests.post(..., timeout=60)).
type Client struct {
	renderURL   string
	currentHost string
	http        *http.Client
}

// New mirrors FastRender.render_url ('http://{}:{}/render'.format(host, port))
// and CURRENT_HOST_PUPPETEER.
func New(host, port, currentHost string) *Client {
	return &Client{renderURL: "http://" + host + ":" + port + "/render", currentHost: currentHost,
		http: &http.Client{Timeout: 60 * time.Second}}
}

// Failure is Puppeteer.pdf_from_url's (False, exception): Text is str(exception).
type Failure struct{ Text string }

func (f *Failure) Error() string { return f.Text }

// Payload is json.dumps(FastRender.get_pdf's payload) for the view path and
// the request's Authorization header (nil: JSON null).
func Payload(currentHost, url string, authorization *string) []byte {
	var b bytes.Buffer
	b.WriteString(`{"type": "pdf", "url": `)
	writeString(&b, currentHost+"/"+url)
	b.WriteString(`, "async": false, "headers": {"Authorization": `)
	if authorization == nil {
		b.WriteString("null")
	} else {
		writeString(&b, *authorization)
	}
	b.WriteString(`}, "device": {"userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) ` +
		`Chrome/91.0.4472.124 Safari/537.36", "scale": 1, "width": 1920, "height": 1080}, "render": {"block": {"cookies": false, ` +
		`"ads": false, "trackers": false, "banners": false}, "waitTime": 0, "timeout": 30000, "fullPage": false, ` +
		`"triggerLazyAnimations": false, "scroll": {"position": 0, "animate": false, "duration": 0, "animation": ""}, ` +
		`"waitUntil": "networkidle2"}, "pdf": {"scale": 1, "displayHeaderFooter": false, "headerTemplate": "", "footerTemplate": "", ` +
		`"printBackground": true, "landscape": false, "pageRanges": "", "format": "A4", "margin": {"top": "1cm", "right": "0px", ` +
		`"bottom": "1cm", "left": "0px"}, "preferCSSPageSize": false, "omitBackground": false, "timeout": 30000}}`)
	return b.Bytes()
}

// writeString is json.dumps of a str (ensure_ascii=True).
func writeString(b *bytes.Buffer, s string) {
	b.WriteByte('"')
	for _, r := range s {
		switch {
		case r == '"':
			b.WriteString(`\"`)
		case r == '\\':
			b.WriteString(`\\`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case r == '\b':
			b.WriteString(`\b`)
		case r == '\f':
			b.WriteString(`\f`)
		case r < 0x20 || (r > 0x7e && r < 0x10000):
			fmt.Fprintf(b, `\u%04x`, r)
		case r >= 0x10000:
			high, low := utf16.EncodeRune(r)
			fmt.Fprintf(b, `\u%04x\u%04x`, high, low)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
}

// Render is Puppeteer.pdf_from_url(url, {'Authorization': authorization}):
// the PDF bytes, or *Failure with the exception text Django wraps in
// 'PDF rendering failed: ...'.
func (c *Client) Render(ctx context.Context, url string, authorization *string) ([]byte, error) {
	body := Payload(c.currentHost, url, authorization)
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.renderURL, bytes.NewReader(body))
	if err != nil {
		return nil, &Failure{Text: err.Error()}
	}
	// requests' default headers, then the explicit Content-Type.
	request.Header.Set("User-Agent", RequestsUserAgent)
	request.Header.Set("Accept-Encoding", "gzip, deflate")
	request.Header.Set("Accept", "*/*")
	request.Header.Set("Connection", "keep-alive")
	request.Header.Set("Content-Type", "application/json")
	response, err := c.http.Do(request)
	if err != nil {
		// requests' ConnectionError/Timeout text names urllib3 objects by
		// address: no stable text exists to reproduce.
		return nil, &Failure{Text: err.Error()}
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, &Failure{Text: err.Error()}
	}
	content, err := decodeContent(raw, response.Header.Get("Content-Encoding"))
	if err != nil {
		return nil, &Failure{Text: err.Error()}
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, &Failure{Text: "Renderer returned HTTP " + strconv.Itoa(response.StatusCode) + ": " +
			firstCodePoints(responseText(content, response.Header.Get("Content-Type")), 500)}
	}
	contentType := response.Header.Get("Content-Type")
	if !strings.Contains(contentType, "application/pdf") || !bytes.HasPrefix(content, []byte("%PDF-")) {
		return nil, &Failure{Text: "Renderer returned invalid PDF data (content-type: " + latin1Header(response.Header.Values("Content-Type")) + ")"}
	}
	return content, nil
}

// latin1Header is the first header value as http.client decodes it.
func latin1Header(values []string) string {
	if len(values) == 0 {
		return ""
	}
	text, _ := charmap.ISO8859_1.NewDecoder().String(values[0])
	return text
}

// decodeContent is urllib3's decoding of the Content-Encoding requests advertises.
func decodeContent(raw []byte, encoding string) ([]byte, error) {
	switch strings.ToLower(strings.TrimSpace(encoding)) {
	case "", "identity":
		return raw, nil
	case "gzip", "x-gzip":
		reader, err := gzip.NewReader(bytes.NewReader(raw))
		if err != nil {
			return nil, err
		}
		return io.ReadAll(reader)
	case "deflate":
		// urllib3 tries zlib-wrapped data first, then raw deflate.
		if reader, err := zlib.NewReader(bytes.NewReader(raw)); err == nil {
			if out, err := io.ReadAll(reader); err == nil {
				return out, nil
			}
		}
		return io.ReadAll(flate.NewReader(bytes.NewReader(raw)))
	}
	return nil, errors.New("pdfrender: unsupported Content-Encoding " + encoding)
}

// responseText is requests' Response.text: the charset parameter, else
// ISO-8859-1 for text/*, else UTF-8 for application/json; undecodable bytes
// are replaced. Content without a declared charset outside those types is
// decoded as UTF-8 (requests would guess it with charset_normalizer).
func responseText(content []byte, contentType string) string {
	_, params, err := mime.ParseMediaType(contentType)
	charset := ""
	if err == nil {
		charset = strings.ToLower(strings.Trim(params["charset"], `"' `))
	}
	media := strings.ToLower(strings.TrimSpace(strings.Split(contentType, ";")[0]))
	switch {
	case charset == "" && strings.HasPrefix(media, "text"):
		charset = "iso-8859-1"
	case charset == "":
		charset = "utf-8"
	}
	switch charset {
	case "iso-8859-1", "latin-1", "latin1", "l1", "iso8859-1":
		text, _ := charmap.ISO8859_1.NewDecoder().Bytes(content)
		return string(text)
	}
	if utf8.Valid(content) {
		return string(content)
	}
	return strings.ToValidUTF8(string(content), "�")
}

func firstCodePoints(text string, n int) string {
	count := 0
	for index := range text {
		if count == n {
			return text[:index]
		}
		count++
	}
	return text
}
