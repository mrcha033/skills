package profile

import (
	"encoding/pem"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

type Profile struct {
	RootCA               string
	Certificate          string
	PrivateKey           string
	Remotes              []string
	Routes               []string
	Protocol             string
	Compression          bool
	Cipher               string
	RedirectInternet     bool
	AdditionalParameters []string
	IdleMinutes          int
}

var safeArgument = regexp.MustCompile(`^[A-Za-z0-9.:\-_/ ]+$`)

var allowedAdditional = map[string]bool{
	"auth":                 true,
	"dhcp-option":          true,
	"explicit-exit-notify": true,
	"fragment":             true,
	"keepalive":            true,
	"mssfix":               true,
	"mute":                 true,
	"ping":                 true,
	"ping-exit":            true,
	"ping-restart":         true,
	"rcvbuf":               true,
	"remote-cert-tls":      true,
	"reneg-sec":            true,
	"route-delay":          true,
	"route-method":         true,
	"sndbuf":               true,
	"tls-version-min":      true,
	"tun-mtu":              true,
	"verb":                 true,
	"verify-x509-name":     true,
}

func Parse(plaintext string) (Profile, error) {
	fields := map[string]string{}
	for _, raw := range strings.Split(plaintext, "\n") {
		line := strings.TrimSpace(raw)
		key, value, ok := strings.Cut(line, "=")
		if !ok || strings.TrimSpace(key) == "" {
			continue
		}
		fields[strings.ToLower(strings.TrimSpace(key))] = strings.TrimSpace(value)
	}
	if message := fields["error"]; message != "" {
		return Profile{}, fmt.Errorf("게이트웨이 인증 실패: %s", message)
	}
	root, err := exactPEM(fields["rootcert"], "root certificate", map[string]bool{
		"CERTIFICATE": true,
	})
	if err != nil {
		return Profile{}, err
	}
	certificate, err := exactPEM(fields["cert"], "client certificate", map[string]bool{
		"CERTIFICATE": true,
	})
	if err != nil {
		return Profile{}, err
	}
	key, err := exactPEM(fields["key"], "private key", map[string]bool{
		"PRIVATE KEY":           true,
		"RSA PRIVATE KEY":       true,
		"EC PRIVATE KEY":        true,
		"ENCRYPTED PRIVATE KEY": true,
	})
	if err != nil {
		return Profile{}, err
	}
	remotes, err := arguments(split(fields["vpnwanip"], "&&"))
	if err != nil {
		return Profile{}, err
	}
	if len(remotes) == 0 {
		return Profile{}, errors.New("인증 응답에 VPN 접속 주소가 없습니다")
	}
	routes, err := arguments(split(fields["server"], "&&"))
	if err != nil {
		return Profile{}, err
	}
	protocol := "tcp-client"
	if strings.EqualFold(fields["protocol"], "udp") || fields["protocol"] == "1" {
		protocol = "udp"
	}
	cipher := strings.ToUpper(fields["cipher"])
	if cipher == "" {
		cipher = "LEA-128-CBC"
	}
	if cipher != "LEA-128-CBC" {
		return Profile{}, fmt.Errorf("지원하지 않는 VPN cipher: %s", cipher)
	}
	additional := []string{}
	for _, value := range split(fields["addparam"], "||") {
		parts := strings.Fields(value)
		if len(parts) == 0 || !allowedAdditional[strings.ToLower(parts[0])] || !safeArgument.MatchString(value) {
			continue
		}
		additional = append(additional, strings.Join(parts, " "))
	}
	idle, _ := strconv.Atoi(fields["idle"])
	if idle < 0 {
		idle = 0
	}
	return Profile{
		RootCA:               root,
		Certificate:          certificate,
		PrivateKey:           key,
		Remotes:              remotes,
		Routes:               routes,
		Protocol:             protocol,
		Compression:          fields["zip"] == "1",
		Cipher:               cipher,
		RedirectInternet:     fields["internet"] != "1",
		AdditionalParameters: additional,
		IdleMinutes:          idle,
	}, nil
}

func (p Profile) OpenVPNConfiguration() string {
	lines := []string{
		"client",
		"dev tun",
		"resolv-retry infinite",
		"nobind",
		"persist-key",
		"persist-tun",
		"route-delay 0 30",
		"proto " + p.Protocol,
	}
	for _, remote := range p.Remotes {
		lines = append(lines, "remote "+remote)
	}
	if p.Compression {
		lines = append(lines, "comp-lzo yes")
	}
	lines = append(lines,
		"cipher "+p.Cipher,
		"data-ciphers "+p.Cipher,
		"data-ciphers-fallback "+p.Cipher,
		"auth SHA256",
		"auth-nocache",
		"verb 3",
	)
	if p.RedirectInternet {
		lines = append(lines, "redirect-gateway def1")
	}
	if p.IdleMinutes > 0 {
		lines = append(lines, fmt.Sprintf("inactive %d", p.IdleMinutes*60))
	}
	for _, route := range p.Routes {
		lines = append(lines, "route "+route)
	}
	lines = append(lines, p.AdditionalParameters...)
	lines = append(lines,
		"<ca>", p.RootCA, "</ca>",
		"<cert>", p.Certificate, "</cert>",
		"<key>", p.PrivateKey, "</key>",
	)
	return strings.Join(lines, "\n") + "\n"
}

func exactPEM(value, label string, allowedTypes map[string]bool) (string, error) {
	normalized := strings.TrimSpace(strings.ReplaceAll(value, "|", "\n"))
	block, rest := pem.Decode([]byte(normalized))
	if block == nil || !allowedTypes[block.Type] || len(strings.TrimSpace(string(rest))) != 0 {
		return "", fmt.Errorf("인증 응답의 %s 형식이 올바르지 않습니다", label)
	}
	return strings.TrimSpace(string(pem.EncodeToMemory(block))), nil
}

func split(value, separator string) []string {
	output := []string{}
	for _, item := range strings.Split(value, separator) {
		if item = strings.TrimSpace(item); item != "" {
			output = append(output, item)
		}
	}
	return output
}

func arguments(values []string) ([]string, error) {
	output := make([]string, 0, len(values))
	for _, value := range values {
		if strings.ContainsAny(value, "\r\n") || !safeArgument.MatchString(value) {
			return nil, errors.New("게이트웨이가 안전하지 않은 OpenVPN 인수를 반환했습니다")
		}
		output = append(output, strings.Join(strings.Fields(value), " "))
	}
	return output, nil
}
