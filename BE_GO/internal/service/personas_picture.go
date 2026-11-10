package service

import (
	"context"
	"errors"
	"net/http"
	"regexp"
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

// personaPictureBase is the host AssociateSerializer.to_internal_value joins
// AWS_LOCATION and the storage path to.
const personaPictureBase = "https://bakney-object-spaces.fra1.cdn.digitaloceanspaces.com"

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

// WithPictures sets the storage of picture_path uploads; overwrite is
// AWS_S3_FILE_OVERWRITE (no HeadObject before the save).
func (s *PersonaWrites) WithPictures(objects PersonaPictureObjects, overwrite bool) *PersonaWrites {
	s.pictures, s.pictureOverwrite = objects, overwrite
	return s
}

var personaPictureFolder = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

// personaPicturePlan reads the body's picture_path like to_internal_value:
// upload is true for a string containing 'base64'. Other shapes that reach
// the upload branch, and folders outside plain identifiers, are not ported.
func personaPicturePlan(body domain.RequestObject) (data, folder string, upload bool, err error) {
	if err = pictureUnported(body); err == nil {
		return "", "", false, nil
	}
	value, _ := body.Lookup("picture_path")
	text, ok := value.(string)
	if !ok {
		return "", "", false, err
	}
	folder = "None"
	if id, present := body.Lookup("associate_id"); present && id != nil {
		name, ok := id.(string)
		if !ok || !personaPictureFolder.MatchString(name) {
			return "", "", false, ErrPersonaNotPorted
		}
		folder = name
	}
	return text, folder, true, nil
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

// personaPictureJoin is os.path.join(base, location, path).
func personaPictureJoin(base, location, path string) string {
	joined := base
	for _, part := range []string{location, path} {
		switch {
		case strings.HasPrefix(part, "/"):
			joined = part
		case joined == "" || strings.HasSuffix(joined, "/"):
			joined += part
		default:
			joined += "/" + part
		}
	}
	return joined
}

// uploadPicture is the serializer's upload: the decoded text after the first
// comma saved as associates/<folder>/picture_<timestamp>.jpg, put_object_acl
// public-read, and the URL that replaces picture_path. Decode failures and a
// refused ACL are the serializer's 400; other storage failures are refused.
func (s *PersonaWrites) uploadPicture(ctx context.Context, data, folder string) (string, error) {
	if s.pictures == nil {
		return "", ErrPersonaNotPorted
	}
	encoded := data
	if _, after, found := strings.Cut(data, ","); found {
		encoded = after
	}
	decoded, err := dto.PythonB64Decode(encoded)
	if err != nil {
		var invalid *dto.PythonBase64Error
		if errors.As(err, &invalid) && invalid.Message == "string argument should contain only ASCII characters" {
			return "", &PersonaPictureInvalid{Text: "Error processing image: " + invalid.Message}
		}
		// binascii.Error becomes a ValidationError the surrounding handler wraps again.
		return "", &PersonaPictureInvalid{Text: "Error processing image: {'picture_path': ErrorDetail(string='Invalid base64 string', code='invalid')}"}
	}
	location := s.pictures.Location()
	name := "associates/" + folder + "/picture_" + personaPictureTimestamp(s.now()) + ".jpg"
	key, err := documentkey.S3Key(name, location)
	if err != nil {
		return "", ErrPersonaNotPorted
	}
	if !s.pictureOverwrite {
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
	if err := s.pictures.PutACL(ctx, location+"/"+name, "public-read"); err != nil {
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
	return personaPictureJoin(personaPictureBase, location, name), nil
}

// personaPictureBody is the body with picture_path replaced.
func personaPictureBody(body domain.RequestObject, url string) domain.RequestObject {
	result := make(domain.RequestObject, len(body))
	copy(result, body)
	for i := range result {
		if result[i].Name == "picture_path" {
			result[i].Value = url
		}
	}
	return result
}
