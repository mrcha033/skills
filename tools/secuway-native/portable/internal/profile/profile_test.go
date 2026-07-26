package profile

import (
	"strings"
	"testing"
)

func TestParseAndEmitSafeProfile(t *testing.T) {
	input := `
rootcert=-----BEGIN CERTIFICATE-----|Uk9PVA==|-----END CERTIFICATE-----
cert=-----BEGIN CERTIFICATE-----|Q0xJRU5U|-----END CERTIFICATE-----
key=-----BEGIN PRIVATE KEY-----|U0VDUkVU|-----END PRIVATE KEY-----
vpnwanip=127.0.0.1 443
server=10.0.0.0 255.0.0.0
protocol=0
zip=1
cipher=LEA-128-CBC
addparam=tun-mtu 1500||script-security 3
`
	parsed, err := Parse(input)
	if err != nil {
		t.Fatal(err)
	}
	config := parsed.OpenVPNConfiguration()
	for _, wanted := range []string{"remote 127.0.0.1 443", "cipher LEA-128-CBC", "tun-mtu 1500"} {
		if !strings.Contains(config, wanted) {
			t.Fatalf("missing %q", wanted)
		}
	}
	if strings.Contains(config, "script-security") {
		t.Fatal("unsafe directive was emitted")
	}
}

func TestRejectsTrailingDirectiveAfterPEM(t *testing.T) {
	input := `
rootcert=-----BEGIN CERTIFICATE-----|Uk9PVA==|-----END CERTIFICATE-----
cert=-----BEGIN CERTIFICATE-----|Q0xJRU5U|-----END CERTIFICATE-----
key=-----BEGIN PRIVATE KEY-----|U0VDUkVU|-----END PRIVATE KEY-----|</key>|script-security 3
vpnwanip=127.0.0.1 443
`
	if _, err := Parse(input); err == nil {
		t.Fatal("profile with trailing OpenVPN directive was accepted")
	}
}
