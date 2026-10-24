package dto

import (
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var ErrProfilePasswordShape = errors.New("invalid profile password input")

type ProfilePasswordRequest struct {
	Current any
	New     string
}

func ParseProfilePassword(body any) (ProfilePasswordRequest, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return ProfilePasswordRequest{}, ErrProfilePasswordShape
	}
	nested, ok := object.Lookup("password_data")
	if !ok {
		return ProfilePasswordRequest{}, ErrProfilePasswordShape
	}
	data, ok := nested.(domain.RequestObject)
	if !ok {
		return ProfilePasswordRequest{}, ErrProfilePasswordShape
	}
	next, ok := data.Lookup("new_password")
	if !ok {
		return ProfilePasswordRequest{}, ErrProfilePasswordShape
	}
	text, ok := next.(string)
	if !ok {
		return ProfilePasswordRequest{}, ErrProfilePasswordShape
	}
	current, ok := data.Lookup("current_password")
	if !ok {
		return ProfilePasswordRequest{}, ErrProfilePasswordShape
	}
	return ProfilePasswordRequest{Current: current, New: text}, nil
}

type ProfilePasswordResponse struct {
	Message  string              `json:"msg"`
	Code     string              `json:"code,omitempty"`
	Role     string              `json:"role"`
	UserData ProfilePasswordUser `json:"user_data"`
}
type ProfilePasswordUser struct {
	UserAuthProfile
	SportAssociation *AssociationProfile `json:"sport_association,omitempty"`
}
