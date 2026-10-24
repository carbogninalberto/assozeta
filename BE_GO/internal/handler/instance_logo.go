package handler

import (
	"compress/gzip"
	"context"
	"crypto/rand"
	"errors"
	"io"
	"math/big"
	"net/http"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/labstack/echo/v4"
)

type InstanceLogoReader interface {
	Read(context.Context) (*domain.InstanceLogo, error)
}
type InstanceLogoHandler struct{ reader InstanceLogoReader }

func NewInstanceLogoHandler(reader InstanceLogoReader) *InstanceLogoHandler {
	return &InstanceLogoHandler{reader: reader}
}

// Finite responses use the shared encoder with Django's combined header value.
// Keep normalization local to this view and restore the caller's request even
// when rendering fails; other routes retain their existing transport behavior.
func renderInstanceLogoFinite(c echo.Context, next echo.HandlerFunc) error {
	original := c.Request()
	request := original.Clone(original.Context())
	request.Header.Set("Accept-Encoding", strings.Join(original.Header.Values("Accept-Encoding"), ","))
	c.SetRequest(request)
	defer c.SetRequest(original)
	return ManualResponseEncoding()(next)(c)
}

func (h *InstanceLogoHandler) Read(c echo.Context) error {
	r := c.Request()
	c.Response().Header().Set("Allow", "GET, HEAD, OPTIONS")
	c.Response().Header().Set("Content-Language", restLanguage(r))
	// Only finite JSON/HTML transport responses use the existing buffered encoder.
	// Successful files retain a streaming writer and bounded memory.
	render := func(next echo.HandlerFunc) error { return renderInstanceLogoFinite(c, next) }
	fail := func(err error) error { return render(func(c echo.Context) error { return restError(c, err) }) }
	query := parseReferenceForm(r.URL.RawQuery)
	if format, ok := query.Lookup("format"); ok && format != "" && format != "json" {
		return fail(&restInputError{Status: 404, Detail: restText(r, "notfound")})
	}
	if !acceptsJSON(strings.Join(r.Header.Values("Accept"), ",")) {
		return fail(&restInputError{Status: 406, Detail: restText(r, "accept")})
	}
	// Authentication is explicitly disabled by the Django view. Global session,
	// restore, locale and CORS middleware still run at the composition boundary.
	if r.Method == http.MethodOptions {
		return render(func(c echo.Context) error {
			return c.JSON(200, map[string]any{"name": "Instance Logo Serve",
				"description": "GET /instance/logo.png (or .jpg, .svg, .webp)\nServe the instance logo.", "renders": []string{"application/json"},
				"parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
		})
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		return fail(&restInputError{Status: 405, Detail: restText(r, "method")})
	}
	value, err := h.reader.Read(r.Context())
	if errors.Is(err, domain.ErrInstanceLogoMissing) {
		return fail(&restInputError{Status: 404, Detail: "Logo not found"})
	}
	if errors.Is(err, domain.ErrInstanceLogoPath) {
		return render(func(c echo.Context) error {
			c.Response().Header().Del("Allow")
			return c.Blob(400, "text/html; charset=utf-8", []byte("\n<!doctype html>\n<html lang=\"en\">\n<head>\n  <title>Bad Request (400)</title>\n</head>\n<body>\n  <h1>Bad Request (400)</h1><p></p>\n</body>\n</html>\n"))
		})
	}
	if err != nil {
		return fail(err)
	}
	defer value.Body.Close()
	header := c.Response().Header()
	header.Set("Content-Type", value.ContentType)
	header.Set("Content-Disposition", `inline; filename="`+value.Filename+`"`)
	header.Set("Cache-Control", "no-cache")
	header.Add("Vary", "Accept-Encoding")
	compressed := manualAcceptsGzip.MatchString(strings.Join(r.Header.Values("Accept-Encoding"), ","))
	if !compressed {
		header.Set("Content-Length", strconv.FormatInt(value.Size, 10))
		c.Response().WriteHeader(http.StatusOK)
		if r.Method == http.MethodHead {
			return nil
		}
		_, err = io.CopyBuffer(c.Response(), value.Body, make([]byte, 64<<10))
		return err
	}
	writer, err := gzip.NewWriterLevel(c.Response(), 6)
	if err != nil {
		return fail(err)
	}
	padding, err := rand.Int(rand.Reader, big.NewInt(100))
	if err != nil {
		return fail(err)
	}
	writer.Name = strings.Repeat("a", int(padding.Int64()))
	header.Set("Content-Encoding", "gzip")
	header.Del("Content-Length")
	c.Response().WriteHeader(http.StatusOK)
	// FileResponse has unknown compressed length even for tiny/empty files and
	// HEAD. Flush prevents net/http from inventing a short-body Content-Length.
	if err = http.NewResponseController(c.Response().Writer).Flush(); err != nil && err != http.ErrNotSupported {
		return err
	}
	if r.Method == http.MethodHead {
		return nil
	}
	_, copyErr := io.CopyBuffer(writer, value.Body, make([]byte, 64<<10))
	closeErr := writer.Close()
	if copyErr != nil {
		return copyErr
	}
	return closeErr
}
