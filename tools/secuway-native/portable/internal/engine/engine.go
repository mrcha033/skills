package engine

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

type Layout struct {
	OpenVPN     string
	ProviderDir string
}

func Discover() (Layout, error) {
	executable, err := os.Executable()
	if err != nil {
		return Layout{}, err
	}
	executable, _ = filepath.EvalSymlinks(executable)
	root := filepath.Dir(filepath.Dir(executable))
	engineName, providerName := "openvpn", "lea.so"
	switch runtime.GOOS {
	case "darwin":
		providerName = "lea.dylib"
	case "windows":
		engineName, providerName = "openvpn.exe", "lea.dll"
	}
	engine, err := discoverOpenVPNExecutable(
		runtime.GOOS,
		root,
		engineName,
		isRegularFile,
		exec.LookPath,
		platformOpenVPNExecutable,
	)
	if err != nil {
		return Layout{}, errors.New("OpenVPN 실행 파일을 찾지 못했습니다")
	}
	providerDir, err := discoverProviderDirectory(
		runtime.GOOS,
		root,
		providerName,
		os.Getenv("SECUWAY_OPENSSL_MODULES"),
		isRegularFile,
		platformOpenVPNProviderDirectory,
	)
	if err != nil {
		return Layout{}, errors.New("LEA OpenSSL provider를 찾지 못했습니다")
	}
	return Layout{OpenVPN: engine, ProviderDir: providerDir}, nil
}

func discoverOpenVPNExecutable(
	goos string,
	root string,
	engineName string,
	regularFile func(string) bool,
	lookPath func(string) (string, error),
	installedExecutable func() (string, error),
) (string, error) {
	// Windows must only trust the OpenVPN Community installation recorded in
	// HKLM. In particular, never execute a sibling or PATH-provided binary.
	if goos == "windows" {
		return installedExecutable()
	}

	engine := filepath.Join(root, "libexec", engineName)
	if regularFile(engine) {
		return engine, nil
	}
	engine, err := lookPath(engineName)
	if err == nil {
		return engine, nil
	}
	return installedExecutable()
}

func isRegularFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func discoverProviderDirectory(
	goos string,
	root string,
	providerName string,
	override string,
	regularFile func(string) bool,
	installedProviderDirectory func(string) (string, error),
) (string, error) {
	// The Windows Interactive Service is privileged. Only load a provider from
	// the HKLM-recorded OpenVPN installation, never from the invoking user's
	// environment or a directory next to the CLI.
	if goos == "windows" {
		return installedProviderDirectory(providerName)
	}

	providerDirectory := filepath.Join(root, "lib", "ossl-modules")
	if override != "" {
		providerDirectory = override
	}
	if regularFile(filepath.Join(providerDirectory, providerName)) {
		return providerDirectory, nil
	}
	return "", os.ErrNotExist
}

func Doctor(ctx context.Context, layout Layout) error {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	ciphers, err := capture(ctx, layout, "--providers", "lea", "default", "--show-ciphers")
	if err != nil || !strings.Contains(ciphers, "LEA-128-CBC") {
		return fmt.Errorf("OpenVPN이 LEA provider를 읽지 못했습니다: %w", err)
	}
	version, err := capture(ctx, layout, "--version")
	if err != nil {
		return err
	}
	if !strings.Contains(version, "[LZO]") {
		return errors.New("OpenVPN에 Secuway 호환 LZO 지원이 없습니다")
	}
	return nil
}

func Connect(config string, layout Layout) error {
	configuration, err := os.ReadFile(config)
	if err != nil {
		return err
	}
	defer clear(configuration)
	return platformConnectConfiguration(configuration, layout)
}

func ConnectConfiguration(config string, layout Layout) error {
	configuration := []byte(config)
	defer clear(configuration)
	return platformConnectConfiguration(configuration, layout)
}

func capture(ctx context.Context, layout Layout, arguments ...string) (string, error) {
	command := exec.CommandContext(ctx, layout.OpenVPN, arguments...)
	command.Env = append(os.Environ(), "OPENSSL_MODULES="+layout.ProviderDir)
	var output bytes.Buffer
	command.Stdout = &output
	command.Stderr = &output
	err := command.Run()
	return output.String(), err
}
