package main

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestValidateLoginOutputRejectsWindowsPlaintextProfile(t *testing.T) {
	err := validateLoginOutput("windows", "profile.ovpn")
	if err == nil {
		t.Fatal("Windows plaintext profile export was accepted")
	}
	if !strings.Contains(err.Error(), "DPAPI") {
		t.Fatalf("Windows rejection does not direct the user to protected storage: %v", err)
	}
}

func TestWindowsLoginOutputIsRejectedBeforeAuthentication(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows-only command boundary")
	}
	output := filepath.Join(t.TempDir(), "profile.ovpn")
	err := run(context.Background(), []string{
		"login",
		"--server", "https://must-not-be-contacted.invalid",
		"--output", output,
	})
	if err == nil || !strings.Contains(err.Error(), "DPAPI") {
		t.Fatalf("Windows login --output was not rejected at the command boundary: %v", err)
	}
	if _, statErr := os.Stat(output); !os.IsNotExist(statErr) {
		t.Fatalf("Windows login --output created a plaintext profile: %v", statErr)
	}
}

func TestValidateLoginOutputAllowsProtectedFlowAndUnixExport(t *testing.T) {
	for _, test := range []struct {
		goos   string
		output string
	}{
		{goos: "windows", output: ""},
		{goos: "darwin", output: "profile.ovpn"},
		{goos: "linux", output: "profile.ovpn"},
	} {
		if err := validateLoginOutput(test.goos, test.output); err != nil {
			t.Fatalf("validateLoginOutput(%q, %q): %v", test.goos, test.output, err)
		}
	}
}

func TestValidateConnectConfigRejectsWindowsArbitraryProfile(t *testing.T) {
	err := validateConnectConfig("windows", "profile.ovpn")
	if err == nil {
		t.Fatal("Windows arbitrary profile connection was accepted")
	}
	if !strings.Contains(err.Error(), "DPAPI") {
		t.Fatalf("Windows rejection does not direct the user to protected storage: %v", err)
	}
}

func TestWindowsConnectConfigIsRejectedBeforeDiscovery(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows-only command boundary")
	}
	config := filepath.Join(t.TempDir(), "profile.ovpn")
	if err := os.WriteFile(config, []byte("client\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	err := run(context.Background(), []string{"connect", "--config", config})
	if err == nil || !strings.Contains(err.Error(), "DPAPI") {
		t.Fatalf("Windows connect --config was not rejected at the command boundary: %v", err)
	}
}

func TestValidateConnectConfigAllowsProtectedFlowAndUnixConfig(t *testing.T) {
	for _, test := range []struct {
		goos   string
		config string
	}{
		{goos: "windows", config: ""},
		{goos: "darwin", config: "profile.ovpn"},
		{goos: "linux", config: "profile.ovpn"},
	} {
		if err := validateConnectConfig(test.goos, test.config); err != nil {
			t.Fatalf("validateConnectConfig(%q, %q): %v", test.goos, test.config, err)
		}
	}
}

func TestHelpDisclosesWindowsPlaintextRestrictions(t *testing.T) {
	if strings.Count(help, "refused on Windows") != 2 {
		t.Fatal("help does not disclose both Windows plaintext-profile restrictions")
	}
}
