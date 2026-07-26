package gateway

import (
	"bytes"
	"context"
	"crypto/aes"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const gatewayKey = "secuwiz1234567890*&^%$#@!sslvpn~"

type Policy struct {
	RequiresPassword bool
	RequiresOTP      bool
	LoginSelect      int
}

type Credentials struct {
	UserID   string
	Password string
	OTP      string
	DeviceID string
}

type LoginResult struct {
	FinalURL  *url.URL
	Plaintext string
}

type Client struct {
	base *url.URL
	http *http.Client
}

func New(rawBase string) (*Client, error) {
	base, err := url.Parse(rawBase)
	if err != nil || base.Scheme != "https" || base.Host == "" {
		return nil, errors.New("올바른 HTTPS 서버 URL이 아닙니다")
	}
	base.Path = ""
	base.RawQuery = ""
	base.Fragment = ""
	jar, err := cookiejar.New(nil)
	if err != nil {
		return nil, fmt.Errorf("쿠키 저장소 생성 실패: %w", err)
	}
	return &Client{
		base: base,
		http: &http.Client{
			Jar:           jar,
			Timeout:       20 * time.Second,
			CheckRedirect: redirectPolicy(base),
		},
	}, nil
}

func redirectPolicy(base *url.URL) func(*http.Request, []*http.Request) error {
	return func(request *http.Request, via []*http.Request) error {
		if len(via) >= 5 {
			return errors.New("게이트웨이 리다이렉트가 너무 많습니다")
		}
		if request.URL.Scheme != "https" || !strings.EqualFold(request.URL.Host, base.Host) {
			return errors.New("게이트웨이가 다른 출처로 리다이렉트했습니다")
		}
		return nil
	}
}

func (c *Client) Server() *url.URL {
	copy := *c.base
	return &copy
}

func (c *Client) Probe(ctx context.Context) (string, error) {
	target := *c.base
	target.Path = "/client/login.jsp"
	query := target.Query()
	query.Set("firstpage", "index.jsp")
	target.RawQuery = query.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target.String(), nil)
	if err != nil {
		return "", err
	}
	request.Header.Set("User-Agent", "EmdClient v1.0")
	response, err := c.http.Do(request)
	if err != nil {
		return "", fmt.Errorf("게이트웨이 연결 실패: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 400 {
		return "", fmt.Errorf("게이트웨이 HTTP 상태 %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 8<<20))
	if err != nil {
		return "", fmt.Errorf("게이트웨이 응답 읽기 실패: %w", err)
	}
	return string(body), nil
}

var policyPattern = regexp.MustCompile(`go\(\s*['"]([012])['"]\s*,\s*['"]([012])['"]\s*,\s*['"]([012])['"]\s*\)`)

func ParsePolicy(html string) (Policy, error) {
	match := policyPattern.FindStringSubmatch(html)
	if len(match) != 4 {
		return Policy{}, errors.New("게이트웨이 인증 정책을 읽지 못했습니다")
	}
	passwordFlag, certificateFlag, otpFlag := match[1], match[2], match[3]
	if certificateFlag == "1" {
		return Policy{}, errors.New("인증서 로그인은 지원하지 않습니다")
	}
	if otpFlag == "1" {
		return Policy{}, errors.New("SMS OTP 로그인은 지원하지 않습니다")
	}
	switch {
	case passwordFlag == "1" && otpFlag == "0":
		return Policy{RequiresPassword: true, LoginSelect: 1}, nil
	case passwordFlag == "0" && otpFlag == "2":
		return Policy{RequiresOTP: true, LoginSelect: 31}, nil
	case passwordFlag == "1" && otpFlag == "2":
		return Policy{RequiresPassword: true, RequiresOTP: true, LoginSelect: 51}, nil
	default:
		return Policy{}, errors.New("지원하지 않는 인증 조합입니다")
	}
}

func (c *Client) AuthenticationPolicy(ctx context.Context) (Policy, error) {
	html, err := c.Probe(ctx)
	if err != nil {
		return Policy{}, err
	}
	return ParsePolicy(html)
}

func (c *Client) Login(ctx context.Context, credentials Credentials, policy Policy) (LoginResult, error) {
	user, err := Encrypt(credentials.UserID)
	if err != nil {
		return LoginResult{}, err
	}
	password := ""
	if credentials.Password != "" {
		password, err = Encrypt(credentials.Password)
		if err != nil {
			return LoginResult{}, err
		}
	}
	otp := ""
	if credentials.OTP != "" {
		otp, err = Encrypt(credentials.OTP)
		if err != nil {
			return LoginResult{}, err
		}
	}
	evalue, err := Encrypt("linux")
	if err != nil {
		return LoginResult{}, err
	}

	form := url.Values{
		"userid_rsa":  {user},
		"userpw_rsa":  {password},
		"usersms_rsa": {otp},
		"loginselect": {fmt.Sprintf("%d", policy.LoginSelect)},
		"pcid":        {credentials.DeviceID},
		"equip_flag":  {"linux"},
		"evalue":      {evalue},
	}
	target := *c.base
	target.Path = "/client/logincheck_cs.jsp"
	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		target.String(),
		strings.NewReader(form.Encode()),
	)
	if err != nil {
		return LoginResult{}, err
	}
	request.Header.Set("User-Agent", "EmdClient v1.0")
	request.Header.Set("Accept", "*/*")
	request.Header.Set("Accept-Encoding", "binary")
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded; charset=euc-kr")

	response, err := c.http.Do(request)
	if err != nil {
		return LoginResult{}, fmt.Errorf("로그인 요청 실패: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 400 {
		return LoginResult{}, fmt.Errorf("게이트웨이 HTTP 상태 %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 8<<20))
	if err != nil {
		return LoginResult{}, fmt.Errorf("로그인 응답 읽기 실패: %w", err)
	}
	plaintext, err := DecodeLoginResponse(body)
	if err != nil {
		return LoginResult{}, err
	}
	finalURL := response.Request.URL
	return LoginResult{FinalURL: finalURL, Plaintext: plaintext}, nil
}

func Encrypt(value string) (string, error) {
	block, err := aes.NewCipher([]byte(gatewayKey))
	if err != nil {
		return "", err
	}
	input := []byte(value)
	padding := aes.BlockSize - len(input)%aes.BlockSize
	padded := make([]byte, len(input)+padding)
	copy(padded, input)
	for i := len(input); i < len(padded); i++ {
		padded[i] = byte(padding)
	}
	output := make([]byte, len(padded))
	for offset := 0; offset < len(padded); offset += aes.BlockSize {
		block.Encrypt(output[offset:offset+aes.BlockSize], padded[offset:offset+aes.BlockSize])
	}
	return base64.StdEncoding.EncodeToString(output), nil
}

func Decrypt(encoded string) (string, error) {
	compact := strings.Map(func(r rune) rune {
		if r == ' ' || r == '\n' || r == '\r' || r == '\t' {
			return -1
		}
		return r
	}, encoded)
	input, err := base64.StdEncoding.DecodeString(compact)
	if err != nil || len(input) == 0 || len(input)%aes.BlockSize != 0 {
		return "", errors.New("게이트웨이 암호문이 올바른 Base64가 아닙니다")
	}
	block, err := aes.NewCipher([]byte(gatewayKey))
	if err != nil {
		return "", err
	}
	output := make([]byte, len(input))
	for offset := 0; offset < len(input); offset += aes.BlockSize {
		block.Decrypt(output[offset:offset+aes.BlockSize], input[offset:offset+aes.BlockSize])
	}
	padding := int(output[len(output)-1])
	if padding < 1 || padding > aes.BlockSize || padding > len(output) {
		return "", errors.New("게이트웨이 인증 응답 복호화에 실패했습니다")
	}
	if !bytes.Equal(output[len(output)-padding:], bytes.Repeat([]byte{byte(padding)}, padding)) {
		return "", errors.New("게이트웨이 인증 응답 복호화에 실패했습니다")
	}
	return string(output[:len(output)-padding]), nil
}

var errorPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)go\(\s*['"]([^'"]{1,200})['"]`),
	regexp.MustCompile(`(?i)alert\(\s*['"]([^'"]{1,200})['"]\s*\)`),
	regexp.MustCompile(`(?i)error\s*=\s*([^\r\n<]{1,200})`),
}

func DecodeLoginResponse(body []byte) (string, error) {
	encoded := strings.TrimSpace(string(body))
	if encoded == "" {
		return "", errors.New("게이트웨이가 빈 로그인 응답을 반환했습니다")
	}
	compact := strings.Join(strings.Fields(encoded), "")
	if ciphertext, err := base64.StdEncoding.DecodeString(compact); err == nil &&
		len(ciphertext) > 0 && len(ciphertext)%aes.BlockSize == 0 {
		return Decrypt(compact)
	}
	for _, pattern := range errorPatterns {
		if match := pattern.FindStringSubmatch(encoded); len(match) == 2 {
			return "", fmt.Errorf("게이트웨이 인증 실패: %s", strings.TrimSpace(match[1]))
		}
	}
	return "", errors.New("게이트웨이 인증이 거부되었습니다. ID, 비밀번호와 Google Authenticator OTP를 확인하세요")
}
