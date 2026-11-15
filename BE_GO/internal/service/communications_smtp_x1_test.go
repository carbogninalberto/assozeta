package service

import (
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// X1: a plan failure is verified false with Go's text (Django answers the
// platform's text), and an empty host before the port dials the local system.
func TestSMTPVerifyPlanX1(t *testing.T) {
	text := func(value string) *string { return &value }
	number := func(value int64) *int64 { return &value }
	s := NewCommunicationSMTP(nil, nil)
	for _, test := range []struct {
		name       string
		host       *string
		port       *int64
		encryption string
		connect    string
		wantPort   int
		want       string
	}{
		{name: "empty host", host: text(":25"), encryption: "None", connect: "", wantPort: 25},
		{name: "split host", host: text("smtp.example.test:587"), encryption: "None", connect: "smtp.example.test", wantPort: 587},
		{name: "split host with TLS", host: text("smtp.example.test:587"), encryption: "TLS", connect: "smtp.example.test", wantPort: 587},
		{name: "default port", host: text("smtp.example.test"), encryption: "None", wantPort: 25},
		{name: "default SSL port", host: text("smtp.example.test"), encryption: "SSL", wantPort: 465},
		{name: "port zero from the host", host: text("smtp.example.test:0"), encryption: "None", connect: "smtp.example.test", wantPort: 25},
		{name: "port too high", host: text("smtp.example.test"), port: number(70000), encryption: "None",
			want: "dial tcp: address 70000: invalid port"},
		{name: "negative port", host: text("smtp.example.test"), port: number(-1), encryption: "None",
			want: "dial tcp: address -1: invalid port"},
		{name: "tls host", host: text("smtp.exämple.test:587"), encryption: "TLS",
			want: `tls: invalid server name "smtp.exämple.test:587"`},
	} {
		plan, err := s.smtpVerifyPlan(domain.CommunicationConfigurationRecord{Host: test.host, Port: test.port, Encryption: test.encryption},
			"socio@example.test")
		if test.want != "" {
			if err == nil || err.Error() != test.want {
				t.Fatalf("%s: got %v, want %q", test.name, err, test.want)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s: plan: %v", test.name, err)
		}
		if plan.server.ConnectHost != test.connect || plan.server.Port != test.wantPort {
			t.Fatalf("%s: plan = %+v, want connect %q port %d", test.name, plan.server, test.connect, test.wantPort)
		}
	}
}
