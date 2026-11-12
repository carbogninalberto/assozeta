package dto

type TwoFactorSetup struct {
	Data TwoFactorProvisioning `json:"data"`
}
type TwoFactorProvisioning struct {
	QRCodeURI string `json:"qrcode_uri"`
	Secret    string `json:"otp_secret"`
}
