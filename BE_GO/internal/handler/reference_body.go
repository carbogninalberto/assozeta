package handler

import (
	"errors"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"golang.org/x/text/encoding/charmap"
)

type restInputError struct {
	Status int
	Detail string
}

func (e *restInputError) Error() string { return e.Detail }

func restLanguage(r *http.Request) string {
	if strings.HasPrefix(strings.ToLower(r.Header.Get("Accept-Language")), "en") {
		return "en"
	}
	return "it"
}
func restText(r *http.Request, kind string) string {
	english := restLanguage(r) == "en"
	switch kind {
	case "credentials":
		if english {
			return "Authentication credentials were not provided."
		}
		return "Non sono state immesse le credenziali di autenticazione."
	case "accept":
		if english {
			return "Could not satisfy the request Accept header."
		}
		return "Impossibile soddisfare l'header \"Accept\" presente nella richiesta."
	case "method":
		if english {
			return `Method "` + r.Method + `" not allowed.`
		}
		return `Metodo "` + r.Method + `" non consentito`
	case "notfound":
		if english {
			return "Not found."
		}
		return "Non trovato."
	case "media":
		if english {
			return `Unsupported media type "` + r.Header.Get("Content-Type") + `" in request.`
		}
		return `Tipo di media "` + r.Header.Get("Content-Type") + `"non supportato.`
	}
	return ""
}
func acceptsJSON(accept string) bool {
	if accept == "" {
		return true
	}
	for _, value := range strings.Split(accept, ",") {
		media := strings.TrimSpace(strings.SplitN(value, ";", 2)[0])
		if media == "*/*" || media == "application/*" || media == "application/json" {
			return true
		}
	}
	return false
}

// parseReferenceForm is the QueryDict of a request's query string: '+' becomes
// ' ', every valid percent escape is decoded, a malformed one stays as written,
// the bytes are read as UTF-8 with errors='replace', and repeated keys keep
// every value.
func parseReferenceForm(raw string) domain.RequestObject {
	return parseQueryPairs(raw, true)
}

func readReferenceBody(r *http.Request) (any, error) {
	return readReferenceBodyJSON(r, dto.ParseReferenceJSON)
}

func readReferenceBodyJSON(r *http.Request, parseJSON func([]byte) (any, error)) (any, error) {
	if r.Body == nil || r.ContentLength == 0 {
		return domain.RequestObject{}, nil
	}
	const maximum = 262144000 // existing DATA_UPLOAD_MAX_MEMORY_SIZE
	media, params, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if media == "multipart/form-data" {
		if params["boundary"] == "" {
			return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid boundary in multipart: None"}
		}
		limited := &io.LimitedReader{R: r.Body, N: maximum + 1}
		reader := multipart.NewReader(limited, params["boundary"])
		fields, files := domain.RequestObject{}, domain.RequestObject{}
		put := func(object *domain.RequestObject, name string, value any) {
			for i, f := range *object {
				if f.Name == name {
					if (*object)[i].All == nil {
						(*object)[i].All = []any{f.Value}
					}
					(*object)[i].All = append((*object)[i].All, value)
					(*object)[i].Value = value
					return
				}
			}
			*object = append(*object, domain.RequestField{Name: name, Value: value})
		}
		for {
			// NextRawPart leaves a quoted-printable body undecoded, as Django does.
			part, err := reader.NextRawPart()
			if err == io.EOF {
				break
			}
			if err != nil {
				return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid multipart data"}
			}
			// Django decodes every header line as UTF-8 and skips the part when
			// no Content-Disposition line survives.
			disposed := false
			for _, value := range part.Header.Values("Content-Disposition") {
				if utf8.ValidString("Content-Disposition: " + value) {
					disposed = true
					break
				}
			}
			if !disposed {
				part.Close()
				continue
			}
			transferEncoding, _ := djangoHeaderValue(part.Header, "Content-Transfer-Encoding")
			base64 := transferEncoding == "base64"
			contentType, _ := djangoHeaderValue(part.Header, "Content-Type")
			if part.FileName() != "" {
				// Django only makes a part a file when the Content-Disposition
				// carries a non-empty filename, and UploadedFile.name is
				// MultiPartParser.sanitize_file_name's name. part.FileName()
				// is Go's filepath.Base: it keeps what Django rewrites
				// (entities, backslash paths) or skips (non-printables, "..").
				uploadName := part.FileName()
				if header, outcome := djangoPartHeaders(part.Header); outcome != headerNotPorted {
					if raw := header.disposition["filename"]; raw != "" {
						sanitized, valid := djangoUploadName(raw)
						if !valid {
							// djangoPartHeaders keeps only valid UTF-8 lines and every
							// decoded parameter is valid UTF-8: no request reaches this.
							return nil, refusal.AppState("multipart file name: unreachable state")
						}
						if sanitized == "" {
							// Django skips the part entirely: it is neither a field nor a file.
							part.Close()
							continue
						}
						uploadName = sanitized
					}
				}
				// An RFC 2231 charset without a Go decoder keeps the name Go read.
				upload := domain.RequestUpload{
					Name:        uploadName,
					Temp:        r.ContentLength > domain.RequestUploadMemory,
					ContentType: contentType,
				}
				// A route that echoes the whole file (withWholeUploads) keeps every byte.
				keepAll := wholeUploads(r.Context())
				if base64 {
					var content []byte
					content, err = io.ReadAll(part)
					if err == nil {
						content, err = pyBase64Decode(stripASCIISpace(content))
						if err != nil {
							return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Could not decode base64 data."}
						}
					}
					upload.Size = int64(len(content))
					if keepAll || upload.Size <= domain.RequestUploadHead {
						upload.Content, upload.Whole = string(content), true
					}
				} else {
					// The first chunk (64 KiB, Django's File.chunks size) is kept for the consumers that read the lines.
					var head []byte
					if keepAll {
						head, _ = io.ReadAll(part)
					} else {
						head, _ = io.ReadAll(io.LimitReader(part, domain.RequestUploadHead+1))
					}
					upload.Size = int64(len(head))
					var rest int64
					rest, err = io.Copy(io.Discard, part)
					upload.Size += rest
					if keepAll || upload.Size <= domain.RequestUploadHead {
						upload.Content, upload.Whole = string(head), true
					}
				}
				put(&files, part.FormName(), upload)
			} else {
				var value []byte
				value, err = io.ReadAll(part)
				if base64 {
					if decoded, decodeErr := pyBase64Decode(value); decodeErr == nil {
						value = decoded
					}
				}
				put(&fields, part.FormName(), dto.PythonUTF8Replace(string(value)))
			}
			part.Close()
			if err != nil {
				return nil, err
			}
			if limited.N <= 0 {
				return nil, &restInputError{Status: 413, Detail: "Request body exceeded settings.DATA_UPLOAD_MAX_MEMORY_SIZE."}
			}
		}
		// The view's request.data is the form's QueryDict updated with the files: text values first, then files.
		for _, file := range files {
			values := file.All
			if values == nil {
				values = []any{file.Value}
			}
			for _, value := range values {
				put(&fields, file.Name, value)
			}
		}
		return fields, nil
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, maximum+1))
	if err != nil {
		return nil, err
	}
	if len(raw) == 0 {
		return domain.RequestObject{}, nil
	}
	if len(raw) > maximum {
		return nil, &restInputError{Status: 413, Detail: "Request body exceeded settings.DATA_UPLOAD_MAX_MEMORY_SIZE."}
	}
	switch media {
	case "application/json":
		switch strings.ToLower(strings.ReplaceAll(params["charset"], "_", "-")) {
		case "", "utf-8", "utf8":
		case "latin1", "latin-1", "iso-8859-1", "iso8859-1":
			raw, _ = charmap.ISO8859_1.NewDecoder().Bytes(raw)
		default:
			return nil, domain.ErrInvalidScopedRequest
		}
		if !utf8.Valid(raw) {
			return nil, &restInputError{Status: 400, Detail: dto.UTF8ParseDetail(raw)}
		}
		value, err := parseJSON(raw)
		if err != nil {
			if errors.Is(err, dto.ErrReferenceNesting) {
				return nil, err
			}
			return nil, &restInputError{Status: 400, Detail: dto.JSONParseDetail(raw)}
		}
		return value, nil
	case "application/x-www-form-urlencoded":
		switch strings.ToLower(strings.ReplaceAll(params["charset"], "_", "-")) {
		case "", "utf-8", "utf8":
			// QueryDict falls back to Latin-1 for a raw body that cannot
			// decode as UTF-8; percent escapes still use replacement UTF-8.
			if !utf8.Valid(raw) {
				raw, _ = charmap.ISO8859_1.NewDecoder().Bytes(raw)
			}
			return parseQueryPairs(string(raw), true), nil
		}
		return parseReferenceForm(string(raw)), nil
	default:
		return nil, &restInputError{Status: 415, Detail: restText(r, "media")}
	}
}
