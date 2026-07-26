package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/mrcha033/secuway-native/portable/internal/engine"
	"github.com/mrcha033/secuway-native/portable/internal/gateway"
	"github.com/mrcha033/secuway-native/portable/internal/profile"
	"github.com/mrcha033/secuway-native/portable/internal/prompt"
	"github.com/mrcha033/secuway-native/portable/internal/store"
)

const version = "0.4.0"
const defaultServer = "https://ysvpn.yonsei.ac.kr"

func main() {
	if err := run(context.Background(), os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "secuway:", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, arguments []string) error {
	command := "help"
	if len(arguments) > 0 {
		command, arguments = arguments[0], arguments[1:]
	}
	switch command {
	case "help", "-h", "--help":
		fmt.Print(help)
		return nil
	case "version", "--version":
		fmt.Printf("secuway-portable %s (%s/%s)\n", version, runtime.GOOS, runtime.GOARCH)
		return nil
	case "doctor":
		layout, err := engine.Discover()
		if err != nil {
			return err
		}
		if err := engine.Doctor(ctx, layout); err != nil {
			return err
		}
		fmt.Printf("OK  platform: %s/%s\n", runtime.GOOS, runtime.GOARCH)
		fmt.Println("OK  cipher: LEA-128-CBC")
		fmt.Println("OK  compression: LZO")
		fmt.Printf("OK  provider: %s\n", layout.ProviderDir)
		return nil
	case "probe":
		flags, server, _, err := commonFlags(command, arguments, false)
		if err != nil {
			return err
		}
		_ = flags
		client, err := gateway.New(server)
		if err != nil {
			return err
		}
		policy, err := client.AuthenticationPolicy(ctx)
		if err != nil {
			return err
		}
		fmt.Println("OK  gateway: SecuwaySSL login page")
		fmt.Printf("OK  auth: %s\n", policyLabel(policy))
		fmt.Printf("OK  loginselect: %d\n", policy.LoginSelect)
		return nil
	case "status":
		_, server, jsonOutput, err := commonFlags(command, arguments, true)
		if err != nil {
			return err
		}
		client, err := gateway.New(server)
		if err != nil {
			return err
		}
		backend, err := store.New()
		if err != nil {
			return err
		}
		status, err := backend.Status(store.Account(client.Server()))
		if err != nil {
			return err
		}
		if jsonOutput {
			encoded, err := json.Marshal(status)
			if err != nil {
				return err
			}
			fmt.Println(string(encoded))
		} else {
			fmt.Printf("%s  cached authentication: %s\n", status.Status, status.CredentialStore)
			fmt.Printf("OK  server: %s\n", status.Server)
		}
		return nil
	case "forget":
		_, server, _, err := commonFlags(command, arguments, false)
		if err != nil {
			return err
		}
		client, err := gateway.New(server)
		if err != nil {
			return err
		}
		backend, err := store.New()
		if err != nil {
			return err
		}
		deleted, err := backend.Delete(store.Account(client.Server()))
		if err != nil {
			return err
		}
		if deleted {
			fmt.Println("OK  removed cached authentication")
		} else {
			fmt.Println("OK  no cached authentication")
		}
		return nil
	case "login":
		flags := flag.NewFlagSet(command, flag.ContinueOnError)
		server := flags.String("server", defaultServer, "HTTPS gateway")
		output := flags.String("output", "", "write an OpenVPN profile (macOS/Linux only)")
		if err := flags.Parse(arguments); err != nil {
			return err
		}
		if err := validateLoginOutput(runtime.GOOS, *output); err != nil {
			return err
		}
		client, backend, result, parsed, err := authenticate(ctx, *server)
		if err != nil {
			return err
		}
		account := store.Account(client.Server())
		if err := backend.Save(account, []byte(result.Plaintext)); err != nil {
			return err
		}
		fmt.Printf("OK  authenticated: %s\n", result.FinalURL)
		fmt.Printf("OK  cached authentication: %s\n", backend.Kind())
		fmt.Printf("OK  tunnel profile: %d remote, %d routes\n", len(parsed.Remotes), len(parsed.Routes))
		if *output != "" {
			if err := writeSecure(*output, []byte(parsed.OpenVPNConfiguration())); err != nil {
				return err
			}
			fmt.Printf("OK  wrote mode-0600 profile: %s\n", *output)
		}
		return nil
	case "connect":
		flags := flag.NewFlagSet(command, flag.ContinueOnError)
		server := flags.String("server", defaultServer, "HTTPS gateway")
		config := flags.String("config", "", "existing OpenVPN profile (macOS/Linux only)")
		if err := flags.Parse(arguments); err != nil {
			return err
		}
		if err := validateConnectConfig(runtime.GOOS, *config); err != nil {
			return err
		}
		layout, err := engine.Discover()
		if err != nil {
			return err
		}
		if *config != "" {
			return engine.Connect(*config, layout)
		}
		client, err := gateway.New(*server)
		if err != nil {
			return err
		}
		backend, err := store.New()
		if err != nil {
			return err
		}
		account := store.Account(client.Server())
		cached, found, err := backend.Load(account)
		if err != nil {
			return err
		}
		var parsed profile.Profile
		if found {
			parsed, err = profile.Parse(string(cached))
			if err != nil {
				return fmt.Errorf("저장 프로필 검증 실패: %w", err)
			}
			fmt.Printf("OK  cached authentication: %s\n", backend.Kind())
		} else {
			_, freshBackend, result, freshProfile, err := authenticate(ctx, *server)
			if err != nil {
				return err
			}
			if err := freshBackend.Save(account, []byte(result.Plaintext)); err != nil {
				return err
			}
			parsed = freshProfile
			fmt.Printf("OK  authenticated once; cached in %s\n", freshBackend.Kind())
		}
		fmt.Println("OK  starting tunnel")
		return engine.ConnectConfiguration(parsed.OpenVPNConfiguration(), layout)
	default:
		return fmt.Errorf("알 수 없는 명령: %s", command)
	}
}

func validateLoginOutput(goos string, output string) error {
	if goos == "windows" && output != "" {
		return errors.New(
			"Windows에서는 login --output을 지원하지 않습니다; " +
				"평문 프로필 대신 DPAPI 캐시와 secuway connect를 사용하세요",
		)
	}
	return nil
}

func validateConnectConfig(goos string, config string) error {
	if goos == "windows" && config != "" {
		return errors.New(
			"Windows에서는 connect --config를 지원하지 않습니다; " +
				"DPAPI에 저장된 인증으로 secuway connect를 사용하세요",
		)
	}
	return nil
}

func commonFlags(name string, arguments []string, allowJSON bool) (*flag.FlagSet, string, bool, error) {
	flags := flag.NewFlagSet(name, flag.ContinueOnError)
	server := flags.String("server", defaultServer, "HTTPS gateway")
	jsonOutput := flags.Bool("json", false, "machine-readable status")
	if err := flags.Parse(arguments); err != nil {
		return flags, "", false, err
	}
	if !allowJSON && *jsonOutput {
		return flags, "", false, errors.New("--json은 status에서만 사용할 수 있습니다")
	}
	return flags, *server, *jsonOutput, nil
}

func authenticate(
	ctx context.Context,
	server string,
) (*gateway.Client, store.Store, gateway.LoginResult, profile.Profile, error) {
	client, err := gateway.New(server)
	if err != nil {
		return nil, nil, gateway.LoginResult{}, profile.Profile{}, err
	}
	policy, err := client.AuthenticationPolicy(ctx)
	if err != nil {
		return nil, nil, gateway.LoginResult{}, profile.Profile{}, err
	}
	user, err := prompt.Text("User ID: ")
	if err != nil || user == "" {
		return nil, nil, gateway.LoginResult{}, profile.Profile{}, errors.New("ID는 비어 있을 수 없습니다")
	}
	password := ""
	if policy.RequiresPassword {
		password, err = prompt.Secret("Password: ")
		if err != nil || password == "" {
			return nil, nil, gateway.LoginResult{}, profile.Profile{}, errors.New("비밀번호는 비어 있을 수 없습니다")
		}
	}
	otp := ""
	if policy.RequiresOTP {
		otp, err = prompt.Secret("Google Authenticator OTP: ")
		if err != nil || otp == "" {
			return nil, nil, gateway.LoginResult{}, profile.Profile{}, errors.New("Google Authenticator OTP는 비어 있을 수 없습니다")
		}
	}
	deviceID, err := deviceID()
	if err != nil {
		return nil, nil, gateway.LoginResult{}, profile.Profile{}, err
	}
	result, err := client.Login(ctx, gateway.Credentials{
		UserID: user, Password: password, OTP: otp, DeviceID: deviceID,
	}, policy)
	if err != nil {
		return nil, nil, gateway.LoginResult{}, profile.Profile{}, err
	}
	parsed, err := profile.Parse(result.Plaintext)
	if err != nil {
		return nil, nil, gateway.LoginResult{}, profile.Profile{}, err
	}
	backend, err := store.New()
	return client, backend, result, parsed, err
}

func deviceID() (string, error) {
	root, err := store.ConfigRoot()
	if err != nil {
		return "", err
	}
	path := filepath.Join(root, "device-id")
	if data, err := os.ReadFile(path); err == nil {
		if value := strings.TrimSpace(string(data)); value != "" {
			return value, nil
		}
	}
	random := make([]byte, 16)
	if _, err := rand.Read(random); err != nil {
		return "", fmt.Errorf("장치 식별자 생성 실패: %w", err)
	}
	value := hex.EncodeToString(random)
	if err := writeSecure(path, []byte(value+"\n")); err != nil {
		return "", err
	}
	return value, nil
}

func writeSecure(path string, data []byte) error {
	parent := filepath.Dir(path)
	if err := os.MkdirAll(parent, 0o700); err != nil {
		return err
	}
	temp, err := os.CreateTemp(parent, ".secuway-")
	if err != nil {
		return err
	}
	name := temp.Name()
	defer os.Remove(name)
	if err := temp.Chmod(0o600); err != nil {
		temp.Close()
		return err
	}
	if _, err := temp.Write(data); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return os.Rename(name, path)
}

func policyLabel(policy gateway.Policy) string {
	label := "ID"
	if policy.RequiresPassword {
		label += "/PW"
	}
	if policy.RequiresOTP {
		label += " + app OTP"
	}
	return label
}

const help = `secuway — portable SecuwaySSL CLI

Usage:
  secuway doctor
  secuway probe [--server https://host]
  secuway status [--server https://host] [--json]
  secuway login [--server https://host]
  secuway login --output profile.ovpn  (macOS/Linux only; refused on Windows)
  secuway connect [--server https://host]
  secuway connect --config profile.ovpn  (macOS/Linux only; refused on Windows)
  secuway forget [--server https://host]
  secuway version
`
