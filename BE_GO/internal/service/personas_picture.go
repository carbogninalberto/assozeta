package service

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
)

// PersonaPictureObjects is the default storage the picture upload uses.
type PersonaPictureObjects interface {
	Location() string
	Exists(ctx context.Context, key string) (bool, error)
	Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error
	PutACL(ctx context.Context, key, acl string) error
}

// PersonaPictureInvalid is the serializer's ValidationError({'picture_path': text}).
type PersonaPictureInvalid struct{ Text string }

func (e *PersonaPictureInvalid) Error() string { return "persona picture: " + e.Text }

// errPersonaPictureKey is a picture name the storage location cannot hold:
// the name is built from a UUID and a float, so only a location setting
// with NUL or invalid UTF-8 gets here (long-tail rule 3).
var errPersonaPictureKey = refusal.AppState("persona picture key outside the location")

// PersonaPictureSettings are the storage settings of picture_path uploads:
// AWS_S3_FILE_OVERWRITE (no HeadObject before the save), AWS_S3_USE_OBJECT_ACL,
// AWS_S3_PUBLIC_BASE_URL and the storage's endpoint and bucket.
type PersonaPictureSettings struct {
	Overwrite, UseObjectACL         bool
	PublicBaseURL, Endpoint, Bucket string
}

// WithPictures sets the storage of picture_path uploads.
func (s *PersonaWrites) WithPictures(objects PersonaPictureObjects, settings PersonaPictureSettings) *PersonaWrites {
	s.pictures, s.pictureSettings = objects, settings
	return s
}

// personaPicturePlan reads the body's picture_path like to_internal_value:
// upload is true for a string containing 'base64', decoded from the text
// after the first comma (decode failures are the serializer's 400, before
// the field validation). A list or dict that reaches the branch fails in
// `value.split(',', 1)` (a ',' element or key) or in base64.b64decode(value),
// and the handler answers that exception's text as the same 400.
func personaPicturePlan(body domain.RequestObject) (decoded []byte, upload bool, err error) {
	branch, err := pictureBranch(body)
	if err != nil || !branch {
		return nil, false, err
	}
	value, _ := body.Lookup("picture_path")
	data, ok := value.(string)
	if !ok {
		comma, kind := false, "list"
		switch v := value.(type) {
		case []any:
			comma = pictureListHas(v, ",")
		case domain.RequestObject:
			_, comma = v.Lookup(",")
			kind = "dict"
		}
		if comma {
			return nil, false, &PersonaPictureInvalid{Text: "Error processing image: '" + kind + "' object has no attribute 'split'"}
		}
		return nil, false, &PersonaPictureInvalid{Text: "Error processing image: argument should be a bytes-like object or ASCII string, not '" + kind + "'"}
	}
	encoded := data
	if _, after, found := strings.Cut(data, ","); found {
		encoded = after
	}
	decoded, err = dto.PythonB64Decode(encoded)
	if err != nil {
		var invalid *dto.PythonBase64Error
		if errors.As(err, &invalid) && invalid.Message == "string argument should contain only ASCII characters" {
			return nil, false, &PersonaPictureInvalid{Text: "Error processing image: " + invalid.Message}
		}
		// binascii.Error becomes a ValidationError the surrounding handler wraps again.
		return nil, false, &PersonaPictureInvalid{Text: "Error processing image: {'picture_path': ErrorDetail(string='Invalid base64 string', code='invalid')}"}
	}
	return decoded, true, nil
}

// personaPictureTimestamp is str(timezone.now().timestamp()): the aware
// datetime's microseconds since the epoch divided by 10**6.
func personaPictureTimestamp(now time.Time) string {
	text := strconv.FormatFloat(float64(now.UnixMicro())/1e6, 'f', -1, 64)
	if !strings.Contains(text, ".") {
		text += ".0"
	}
	return text
}

// personaPictureURL is the object's address: the public base URL of the
// storage, or its endpoint and bucket, then the object key.
func personaPictureURL(settings PersonaPictureSettings, key string) string {
	base := strings.TrimRight(strings.TrimSpace(settings.PublicBaseURL), "/")
	if base == "" {
		base = strings.TrimRight(settings.Endpoint, "/") + "/" + settings.Bucket
	}
	return base + "/" + key
}

// uploadPicture is _store_picture, run by create() / update() once the data
// is valid: the picture saved as associates/<associate id>/picture_<timestamp>.jpg
// (Django's get_available_name picks a random alternative name when the key is
// taken, unless AWS_S3_FILE_OVERWRITE is on), put_object_acl public-read when
// object ACLs are on, and the URL stored as picture_path. Every storage
// exception is the serializer's 400 with botocore's final text.
func (s *PersonaWrites) uploadPicture(ctx context.Context, decoded []byte, associate string) (string, error) {
	if s.pictures == nil {
		// cmd/server/main.go always calls WithPictures.
		return "", domain.ErrServerComposition
	}
	location := strings.Trim(s.pictures.Location(), "/")
	name := "associates/" + associate + "/picture_" + personaPictureTimestamp(s.now()) + ".jpg"
	key, err := documentkey.S3Key(name, s.pictures.Location())
	if err != nil {
		return "", errPersonaPictureKey
	}
	if !s.pictureSettings.Overwrite {
		// Storage.save: get_available_name (HeadObject), then one PutObject.
		name, err = documentkey.AvailableName(ctx, name, func(ctx context.Context, candidate string) (bool, error) {
			candidateKey, err := documentkey.S3Key(candidate, s.pictures.Location())
			if err != nil {
				return false, err
			}
			return s.pictures.Exists(ctx, candidateKey)
		}, documentkey.RandomName7)
		if err != nil {
			if text, ok := botocoreFinalErrorText(err, "HeadObject"); ok {
				return "", &PersonaPictureInvalid{Text: "Error processing image: " + text}
			}
			return "", err
		}
		key, err = documentkey.S3Key(name, s.pictures.Location())
		if err != nil {
			return "", errPersonaPictureKey
		}
	}
	contentType, known, encoding, err := pymime.GuessType(key)
	if err != nil {
		return "", err
	}
	if !known {
		contentType = "application/octet-stream"
	}
	if err := s.pictures.Put(ctx, key, decoded, contentType, encoding); err != nil {
		// default_storage.save's exception, as for the document uploads.
		if text, ok := documentSaveFailureText(err, len(decoded)); ok {
			return "", &PersonaPictureInvalid{Text: "Error processing image: " + text}
		}
		return "", err
	}
	object := name
	if location != "" {
		object = location + "/" + name
	}
	if s.pictureSettings.UseObjectACL {
		if err := s.pictures.PutACL(ctx, object, "public-read"); err != nil {
			// botocore's text for a failed put_object_acl.
			if text, ok := botocoreFinalErrorText(err, "PutObjectAcl"); ok {
				return "", &PersonaPictureInvalid{Text: "Error processing image: " + text}
			}
			return "", err
		}
	}
	return personaPictureURL(s.pictureSettings, object), nil
}

// personaWithoutPicture is the body without picture_path: the serializer
// validates the other fields and sets the stored URL itself.
func personaWithoutPicture(body domain.RequestObject) domain.RequestObject {
	result := make(domain.RequestObject, 0, len(body))
	for _, field := range body {
		if field.Name != "picture_path" {
			result = append(result, field)
		}
	}
	return result
}
