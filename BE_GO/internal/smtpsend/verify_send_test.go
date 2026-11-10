package smtpsend

import "testing"

func TestPythonMessage(t *testing.T) {
	cases := []struct {
		err  error
		want string
		ok   bool
	}{
		{&Error{Kind: "SMTPAuthenticationError", Code: 535, Msg: "it's \"no\"\n\xff"}, `(535, b'it\'s "no"\n\xff')`, true},
		{&Error{Kind: "SMTPAuthenticationError", Code: 535, Msg: "it's"}, `(535, b"it's")`, true},
		{&Error{Kind: "SMTPSenderRefused", Code: 550, Msg: "no", Sender: "L'Associazione è <a@b.test>"}, `(550, b'no', "L'Associazione è <a@b.test>")`, true},
		{&Error{Kind: "SMTPSenderRefused", Code: 550, Msg: "no", Sender: "a\tb"}, "", false},
		{&Error{Kind: "SMTPRecipientsRefused", Refused: []Refusal{{Address: "a@b.test", Code: 550, Msg: "refused"}}}, `{'a@b.test': (550, b'refused')}`, true},
		{&Error{Kind: "SMTPNotSupportedError", Text: "STARTTLS extension not supported by server."}, "STARTTLS extension not supported by server.", true},
		{&Error{Kind: "SMTPServerDisconnected"}, "", false},
		{ErrUnsupported, "", false},
	}
	for _, c := range cases {
		got, ok := PythonMessage(c.err)
		if got != c.want || ok != c.ok {
			t.Errorf("PythonMessage(%v) = %q, %v; want %q, %v", c.err, got, ok, c.want, c.ok)
		}
	}
}
