package engine

import (
	"errors"
	"path/filepath"
	"testing"
)

func TestWindowsDiscoveryIgnoresSiblingAndPathExecutables(t *testing.T) {
	const installed = `C:\Program Files\OpenVPN\bin\openvpn.exe`
	regularFileCalled := false
	lookPathCalled := false
	installedCalled := false

	executable, err := discoverOpenVPNExecutable(
		"windows",
		t.TempDir(),
		"openvpn.exe",
		func(string) bool {
			regularFileCalled = true
			return true
		},
		func(string) (string, error) {
			lookPathCalled = true
			return filepath.Join(t.TempDir(), "openvpn.exe"), nil
		},
		func() (string, error) {
			installedCalled = true
			return installed, nil
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if executable != installed {
		t.Fatalf("Windows selected %q instead of installed executable %q", executable, installed)
	}
	if regularFileCalled {
		t.Fatal("Windows discovery inspected the sibling executable")
	}
	if lookPathCalled {
		t.Fatal("Windows discovery searched PATH")
	}
	if !installedCalled {
		t.Fatal("Windows discovery did not use the installed executable resolver")
	}
}

func TestWindowsDiscoveryDoesNotFallbackWhenInstalledExecutableIsMissing(t *testing.T) {
	expected := errors.New("OpenVPN is not installed")
	executable, err := discoverOpenVPNExecutable(
		"windows",
		t.TempDir(),
		"openvpn.exe",
		func(string) bool {
			t.Fatal("Windows discovery inspected the sibling executable")
			return true
		},
		func(string) (string, error) {
			t.Fatal("Windows discovery searched PATH")
			return "openvpn.exe", nil
		},
		func() (string, error) {
			return "", expected
		},
	)
	if executable != "" {
		t.Fatalf("Windows selected an executable after installed lookup failed: %q", executable)
	}
	if !errors.Is(err, expected) {
		t.Fatalf("Windows returned %v instead of installed lookup error %v", err, expected)
	}
}

func TestUnixDiscoveryRetainsSiblingThenPathOrder(t *testing.T) {
	root := t.TempDir()
	sibling := filepath.Join(root, "libexec", "openvpn")

	executable, err := discoverOpenVPNExecutable(
		"linux",
		root,
		"openvpn",
		func(path string) bool { return path == sibling },
		func(string) (string, error) {
			return "", errors.New("PATH lookup must not run when sibling exists")
		},
		func() (string, error) {
			return "", errors.New("platform lookup must not run when sibling exists")
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if executable != sibling {
		t.Fatalf("Unix selected %q instead of sibling %q", executable, sibling)
	}
}

func TestWindowsProviderDiscoveryIgnoresEnvironmentAndSibling(t *testing.T) {
	const installed = `C:\Program Files\OpenVPN\ssl\modules`
	regularFileCalled := false
	installedCalled := false

	directory, err := discoverProviderDirectory(
		"windows",
		t.TempDir(),
		"lea.dll",
		`C:\Users\attacker\modules`,
		func(string) bool {
			regularFileCalled = true
			return true
		},
		func(providerName string) (string, error) {
			installedCalled = true
			if providerName != "lea.dll" {
				t.Fatalf("unexpected provider name: %q", providerName)
			}
			return installed, nil
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if directory != installed {
		t.Fatalf("Windows selected provider directory %q instead of %q", directory, installed)
	}
	if regularFileCalled {
		t.Fatal("Windows provider discovery inspected an environment or sibling path")
	}
	if !installedCalled {
		t.Fatal("Windows provider discovery did not use the installed provider resolver")
	}
}

func TestWindowsProviderDiscoveryDoesNotFallbackWhenInstalledProviderIsMissing(t *testing.T) {
	expected := errors.New("LEA provider is not installed")
	directory, err := discoverProviderDirectory(
		"windows",
		t.TempDir(),
		"lea.dll",
		`C:\Users\attacker\modules`,
		func(string) bool {
			t.Fatal("Windows provider discovery inspected an environment or sibling path")
			return true
		},
		func(string) (string, error) {
			return "", expected
		},
	)
	if directory != "" {
		t.Fatalf("Windows selected a provider after installed lookup failed: %q", directory)
	}
	if !errors.Is(err, expected) {
		t.Fatalf("Windows returned %v instead of installed lookup error %v", err, expected)
	}
}
