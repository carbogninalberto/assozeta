package handler

import (
	"errors"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/url"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
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

func parseReferenceForm(raw string) domain.RequestObject {
	result := domain.RequestObject{}
	positions := map[string]int{}
	decode := func(s string) string {
		decoded, err := url.QueryUnescape(s)
		if err != nil {
			return strings.ReplaceAll(s, "+", " ")
		}
		return decoded
	}
	for _, pair := range strings.Split(raw, "&") {
		if pair == "" {
			continue
		}
		key, value, _ := strings.Cut(pair, "=")
		key = decode(key)
		value = decode(value)
		if i, ok := positions[key]; ok {
			result[i].Value = value
		} else {
			positions[key] = len(result)
			result = append(result, domain.RequestField{Name: key, Value: value})
		}
	}
	return result
}

func readReferenceBody(r *http.Request) (any, error) {
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
					(*object)[i].Value = value
					return
				}
			}
			*object = append(*object, domain.RequestField{Name: name, Value: value})
		}
		for {
			part, err := reader.NextPart()
			if err == io.EOF {
				break
			}
			if err != nil {
				return nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid multipart data"}
			}
			if part.FileName() != "" {
				var size int64
				size, err = io.Copy(io.Discard, part)
				put(&files, part.FormName(), domain.RequestUpload{Name: part.FileName(), Size: size})
			} else {
				var value []byte
				value, err = io.ReadAll(part)
				put(&fields, part.FormName(), string(value))
			}
			part.Close()
			if err != nil {
				return nil, err
			}
			if limited.N <= 0 {
				return nil, &restInputError{Status: 413, Detail: "Request body exceeded settings.DATA_UPLOAD_MAX_MEMORY_SIZE."}
			}
		}
		for _, file := range files {
			put(&fields, file.Name, file.Value)
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
		value, err := dto.ParseReferenceJSON(raw)
		if err != nil {
			if errors.Is(err, dto.ErrReferenceNesting) {
				return nil, err
			}
			return nil, &restInputError{Status: 400, Detail: dto.JSONParseDetail(raw)}
		}
		return value, nil
	case "application/x-www-form-urlencoded":
		return parseReferenceForm(string(raw)), nil
	default:
		return nil, &restInputError{Status: 415, Detail: restText(r, "media")}
	}
}
