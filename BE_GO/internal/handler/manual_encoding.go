package handler

import (
	"bytes"
	"compress/gzip"
	"crypto/rand"
	"math/big"
	"net/http"
	"regexp"
	"strconv"
	"strings"

	"github.com/labstack/echo/v4"
)

var manualAcceptsGzip = regexp.MustCompile(`\bgzip\b`)

// ManualResponseEncoding follows Django's response-size and streaming-asset
// rules. Reader responses already own complete validated bytes; buffering here
// never reads the asset a second time or changes the hash-verification boundary.
func ManualResponseEncoding() echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			original := c.Response().Writer
			buffer := &manualEncodingWriter{ResponseWriter: original}
			c.Response().Writer = buffer
			err := next(c)
			c.Response().Writer = original
			// Nothing has reached the client yet. Preserve normal Echo error
			// handling and count the final encoded bytes when the response is sent.
			c.Response().Committed = false
			c.Response().Size = 0
			if err != nil {
				return err
			}
			if buffer.status == 0 {
				return nil
			}
			body := buffer.body.Bytes()
			header := original.Header()
			streamingAsset := header.Get("Content-Type") == "image/png"
			if (streamingAsset || len(body) >= 200) && header.Get("Content-Encoding") == "" {
				vary := false
				for _, value := range header.Values("Vary") {
					for _, token := range strings.Split(value, ",") {
						vary = vary || strings.EqualFold(strings.TrimSpace(token), "Accept-Encoding")
					}
				}
				if !vary {
					header.Add("Vary", "Accept-Encoding")
				}
				if manualAcceptsGzip.MatchString(c.Request().Header.Get("Accept-Encoding")) {
					var compressed bytes.Buffer
					writer, err := gzip.NewWriterLevel(&compressed, 6)
					if err != nil {
						return err
					}
					// Django randomizes the gzip filename to mitigate compression
					// side channels; retain that property without fixed fixture bytes.
					padding, err := rand.Int(rand.Reader, big.NewInt(100))
					if err != nil {
						return err
					}
					writer.Name = strings.Repeat("a", int(padding.Int64()))
					if _, err = writer.Write(body); err != nil {
						return err
					}
					if err = writer.Close(); err != nil {
						return err
					}
					if streamingAsset || compressed.Len() < len(body) {
						body = compressed.Bytes()
						header.Set("Content-Encoding", "gzip")
						if streamingAsset {
							header.Del("Content-Length")
						} else {
							header.Set("Content-Length", strconv.Itoa(len(body)))
						}
						if etag := header.Get("ETag"); strings.HasPrefix(etag, `"`) {
							header.Set("ETag", "W/"+etag)
						}
					}
				}
			}
			c.Response().WriteHeader(buffer.status)
			if streamingAsset && header.Get("Content-Encoding") == "gzip" {
				// A compressed Django FileResponse has unknown Content-Length.
				// Flush headers so net/http cannot synthesize a short-body length.
				if err := http.NewResponseController(original).Flush(); err != nil && err != http.ErrNotSupported {
					return err
				}
			}
			_, err = c.Response().Write(body)
			return err
		}
	}
}

type manualEncodingWriter struct {
	http.ResponseWriter
	status int
	body   bytes.Buffer
}

func (w *manualEncodingWriter) WriteHeader(status int) {
	if w.status == 0 {
		w.status = status
	}
}
func (w *manualEncodingWriter) Write(body []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK
	}
	return w.body.Write(body)
}
