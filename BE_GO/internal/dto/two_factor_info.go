package dto

type TwoFactorInfo struct {
	Data TwoFactorStatus `json:"data"`
}
type TwoFactorStatus struct {
	Enabled bool `json:"enabled"`
}
