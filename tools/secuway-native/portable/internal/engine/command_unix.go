//go:build darwin || linux

package engine

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

func platformOpenVPNProviderDirectory(string) (string, error) {
	return "", exec.ErrNotFound
}

func platformOpenVPNExecutable() (string, error) {
	return "", exec.ErrNotFound
}

func platformConnectConfiguration(configuration []byte, layout Layout) error {
	directory, err := os.MkdirTemp("", "secuway-portable-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(directory)
	if err := os.Chmod(directory, 0o700); err != nil {
		return err
	}
	config := filepath.Join(directory, "client.ovpn")
	profile, err := os.OpenFile(config, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	if _, err := profile.Write(configuration); err != nil {
		profile.Close()
		return err
	}
	if err := profile.Sync(); err != nil {
		profile.Close()
		return err
	}
	if err := profile.Close(); err != nil {
		return err
	}
	command := elevatedCommand(
		layout,
		"--disable-dco",
		"--providers", "lea", "default",
		"--config", config,
	)
	command.Stdin = os.Stdin
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if err := command.Run(); err != nil {
		return fmt.Errorf("OpenVPN 실행 실패: %w", err)
	}
	return nil
}

func elevatedCommand(layout Layout, arguments ...string) *exec.Cmd {
	if os.Geteuid() == 0 {
		command := exec.Command(layout.OpenVPN, arguments...)
		command.Env = append(os.Environ(), "OPENSSL_MODULES="+layout.ProviderDir)
		return command
	}
	sudoArguments := []string{
		"/usr/bin/env",
		"OPENSSL_MODULES=" + layout.ProviderDir,
		layout.OpenVPN,
	}
	sudoArguments = append(sudoArguments, arguments...)
	return exec.Command("sudo", sudoArguments...)
}
