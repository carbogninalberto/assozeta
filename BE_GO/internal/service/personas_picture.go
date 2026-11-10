package service

import (
	"context"
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	smithy "github.com/aws/smithy-go"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
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
// the field validation). Other shapes that reach the upload branch are not
// ported.
func personaPicturePlan(body domain.RequestObject) (decoded []byte, upload bool, err error) {
	if err = pictureUnported(body); err == nil {
		return nil, false, nil
	}
	value, _ := body.Lookup("picture_path")
	data, ok := value.(string)
	if !ok {
		return nil, false, err
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
// is valid: the picture saved as associates/<associate id>/picture_<timestamp>.jpg,
// put_object_acl public-read when object ACLs are on, and the URL stored as
// picture_path. A refused ACL is the serializer's 400; other storage
// failures are refused.
func (s *PersonaWrites) uploadPicture(ctx context.Context, decoded []byte, associate string) (string, error) {
	if s.pictures == nil {
		return "", ErrPersonaNotPorted
	}
	location := strings.Trim(s.pictures.Location(), "/")
	name := "associates/" + associate + "/picture_" + personaPictureTimestamp(s.now()) + ".jpg"
	key, err := documentkey.S3Key(name, s.pictures.Location())
	if err != nil {
		return "", ErrPersonaNotPorted
	}
	if !s.pictureSettings.Overwrite {
		// Storage.save: get_available_name (HeadObject), then one PutObject.
		exists, err := s.pictures.Exists(ctx, key)
		if err != nil || exists {
			return "", ErrPersonaNotPorted
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
		return "", ErrPersonaNotPorted
	}
	object := name
	if location != "" {
		object = location + "/" + name
	}
	if s.pictureSettings.UseObjectACL {
		if err := s.pictures.PutACL(ctx, object, "public-read"); err != nil {
			// botocore's ClientError text for a refused request.
			var api smithy.APIError
			var response *smithyhttp.ResponseError
			if errors.As(err, &api) && errors.As(err, &response) &&
				(response.HTTPStatusCode() == http.StatusForbidden || response.HTTPStatusCode() == http.StatusNotFound) {
				return "", &PersonaPictureInvalid{Text: "Error processing image: An error occurred (" + api.ErrorCode() +
					") when calling the PutObjectAcl operation: " + api.ErrorMessage()}
			}
			return "", ErrPersonaNotPorted
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
