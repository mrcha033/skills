package gateway

import (
	"net/http"
	"net/url"
	"testing"
)

func TestGatewayAESVectorAndRoundTrip(t *testing.T) {
	encoded, err := Encrypt("index.jsp")
	if err != nil {
		t.Fatal(err)
	}
	if encoded != "2C1+KowMKjl8OXyNgnW4Sg==" {
		t.Fatalf("unexpected vector: %s", encoded)
	}
	plain, err := Decrypt(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if plain != "index.jsp" {
		t.Fatalf("unexpected plaintext: %q", plain)
	}
}

func TestYonseiPolicy(t *testing.T) {
	policy, err := ParsePolicy(`<a href="javascript:go('1','0','2')">LOGIN</a>`)
	if err != nil {
		t.Fatal(err)
	}
	if !policy.RequiresPassword || !policy.RequiresOTP || policy.LoginSelect != 51 {
		t.Fatalf("unexpected policy: %+v", policy)
	}
}

func TestPlaintextFailureDoesNotReportBase64(t *testing.T) {
	_, err := DecodeLoginResponse([]byte(`go('OTP failed')`))
	if err == nil || err.Error() != "게이트웨이 인증 실패: OTP failed" {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestRedirectPolicyRejectsCrossOriginAndDowngrade(t *testing.T) {
	base, err := url.Parse("https://vpn.example")
	if err != nil {
		t.Fatal(err)
	}
	check := redirectPolicy(base)
	for _, target := range []string{
		"https://attacker.example/collect",
		"http://vpn.example/collect",
	} {
		redirect, err := http.NewRequest(http.MethodPost, target, nil)
		if err != nil {
			t.Fatal(err)
		}
		if err := check(redirect, nil); err == nil {
			t.Fatalf("redirect unexpectedly allowed: %s", target)
		}
	}
	sameOrigin, err := http.NewRequest(http.MethodGet, "https://vpn.example/client/login.jsp", nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := check(sameOrigin, nil); err != nil {
		t.Fatalf("same-origin redirect rejected: %v", err)
	}
}
