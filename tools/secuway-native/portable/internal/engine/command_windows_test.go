//go:build windows

package engine

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode/utf16"
)

func TestOpenVPNExecutableComesFromInstallDirectoryBin(t *testing.T) {
	installDirectory := t.TempDir()
	expected := filepath.Join(installDirectory, "bin", "openvpn.exe")
	if err := os.MkdirAll(filepath.Dir(expected), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(expected, []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}

	executable, err := openVPNExecutableFromInstallDirectory(installDirectory)
	if err != nil {
		t.Fatal(err)
	}
	if executable != expected {
		t.Fatalf("selected %q instead of HKLM install-root binary %q", executable, expected)
	}
}

func TestProviderComesFromInstallDirectorySSLModules(t *testing.T) {
	installDirectory := t.TempDir()
	expected := filepath.Join(installDirectory, "ssl", "modules")
	provider := filepath.Join(expected, "lea.dll")
	if err := os.MkdirAll(expected, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(provider, []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}

	directory, err := openVPNProviderDirectoryFromInstallDirectory(
		installDirectory,
		"lea.dll",
	)
	if err != nil {
		t.Fatal(err)
	}
	if directory != expected {
		t.Fatalf("selected %q instead of HKLM install-root provider directory %q", directory, expected)
	}
}

func TestWindowsConfigurationAddsRequiredDirectives(t *testing.T) {
	configuration := string(windowsConfiguration([]byte("client\n")))
	for _, directive := range []string{
		"providers lea default\n",
		"disable-dco\n",
		"windows-driver tap-windows6\n",
	} {
		if !strings.Contains(configuration, directive) {
			t.Fatalf("missing directive %q", directive)
		}
	}
}

func TestInteractiveStartupMessageHasThreeUTF16Strings(t *testing.T) {
	message := startupMessage(`C:\OpenVPN\config`, `--config "client.ovpn"`)
	if len(message)%2 != 0 {
		t.Fatal("message length is not UTF-16 aligned")
	}
	units := make([]uint16, len(message)/2)
	for index := range units {
		units[index] = binary.LittleEndian.Uint16(message[index*2:])
	}
	decoded := string(utf16.Decode(units))
	if decoded != "C:\\OpenVPN\\config\x00--config \"client.ovpn\"\x00\x00" {
		t.Fatalf("unexpected startup message: %q", decoded)
	}
}

func TestParseInteractiveProcessID(t *testing.T) {
	processID, err := parseInteractiveProcessID("0x00000000\n0x00001A2B\nProcess ID")
	if err != nil {
		t.Fatal(err)
	}
	if processID != 0x1A2B {
		t.Fatalf("unexpected process ID: %x", processID)
	}
	if _, err := parseInteractiveProcessID("0x20000001\nValidateOptions\nrejected"); err == nil {
		t.Fatal("service rejection was accepted")
	}
}

func TestMaterializeInteractiveProfileRoundTripAndCleanup(t *testing.T) {
	directory := t.TempDir()
	profile, log, cleanup, err := materializeInteractiveProfile(
		directory,
		[]byte("client\n"),
	)
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{profile, log} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("temporary file missing: %s: %v", path, err)
		}
	}
	configuration, err := os.ReadFile(profile)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(configuration), "windows-driver tap-windows6") {
		t.Fatal("materialized profile is missing Windows directives")
	}
	cleanup()
	for _, path := range []string{profile, log} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("temporary file was not removed: %s", path)
		}
	}
}
