//go:build windows

package engine

import (
	"encoding/binary"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf16"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

const interactivePipe = `\\.\pipe\openvpn\service`

type openVPNSettings struct {
	InstallDirectory string
	ConfigDirectory  string
}

func platformOpenVPNProviderDirectory(providerName string) (string, error) {
	settings, err := installedOpenVPNSettings()
	if err != nil {
		return "", err
	}
	return openVPNProviderDirectoryFromInstallDirectory(settings.InstallDirectory, providerName)
}

func openVPNProviderDirectoryFromInstallDirectory(
	installDirectory string,
	providerName string,
) (string, error) {
	providerDirectory := filepath.Join(installDirectory, "ssl", "modules")
	if info, err := os.Stat(filepath.Join(providerDirectory, providerName)); err != nil || info.IsDir() {
		return "", exec.ErrNotFound
	}
	return providerDirectory, nil
}

func platformOpenVPNExecutable() (string, error) {
	settings, err := installedOpenVPNSettings()
	if err != nil {
		return "", err
	}
	return openVPNExecutableFromInstallDirectory(settings.InstallDirectory)
}

func openVPNExecutableFromInstallDirectory(installDirectory string) (string, error) {
	executable := filepath.Join(installDirectory, "bin", "openvpn.exe")
	if info, err := os.Stat(executable); err != nil || info.IsDir() {
		return "", exec.ErrNotFound
	}
	return executable, nil
}

func platformConnectConfiguration(configuration []byte, layout Layout) error {
	token, err := windows.OpenCurrentProcessToken()
	if err != nil {
		return fmt.Errorf("Windows 사용자 토큰 확인 실패: %w", err)
	}
	elevated := token.IsElevated()
	token.Close()
	if elevated {
		return connectElevated(configuration, layout)
	}
	return connectInteractive(configuration)
}

func connectElevated(configuration []byte, layout Layout) error {
	directory, err := os.MkdirTemp("", "secuway-portable-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(directory)
	config := filepath.Join(directory, "client.ovpn")
	if err := os.WriteFile(config, configuration, 0o600); err != nil {
		return err
	}
	command := exec.Command(
		layout.OpenVPN,
		"--disable-dco",
		"--providers", "lea", "default",
		"--config", config,
	)
	command.Env = append(os.Environ(), "OPENSSL_MODULES="+layout.ProviderDir)
	command.Stdin = os.Stdin
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if err := command.Run(); err != nil {
		return fmt.Errorf("OpenVPN 실행 실패: %w", err)
	}
	return nil
}

func connectInteractive(configuration []byte) error {
	settings, err := installedOpenVPNSettings()
	if err != nil {
		return err
	}
	provider := filepath.Join(settings.InstallDirectory, "ssl", "modules", "lea.dll")
	if info, err := os.Stat(provider); err != nil || info.IsDir() {
		return errors.New("Windows 1회 설치가 필요합니다: OpenVPN 모듈 디렉터리에 lea.dll이 없습니다")
	}
	directory, err := interactiveProfileDirectory(settings)
	if err != nil {
		return err
	}
	profilePath, logPath, cleanup, err := materializeInteractiveProfile(directory, configuration)
	if err != nil {
		return err
	}
	defer cleanup()

	pipe, err := openInteractivePipe()
	if err != nil {
		return err
	}
	defer windows.CloseHandle(pipe)

	options := windows.ComposeCommandLine([]string{
		"--config", profilePath,
		"--log", logPath,
		"--verb", "3",
	})
	message := startupMessage(directory, options)
	var written uint32
	if err := windows.WriteFile(pipe, message, &written, nil); err != nil {
		clear(message)
		return fmt.Errorf("OpenVPN Interactive Service 요청 실패: %w", err)
	}
	if int(written) != len(message) {
		clear(message)
		return errors.New("OpenVPN Interactive Service 시작 메시지가 완전히 기록되지 않았습니다")
	}
	clear(message)

	response, err := readInteractiveResponseWithTimeout(pipe, 20*time.Second)
	if err != nil {
		return err
	}
	processID, err := parseInteractiveProcessID(response)
	if err != nil {
		return err
	}
	fmt.Printf("OK  OpenVPN Interactive Service PID: %d\n", processID)
	return monitorInteractiveProcess(processID)
}

func installedOpenVPNSettings() (openVPNSettings, error) {
	key, err := registry.OpenKey(registry.LOCAL_MACHINE, `SOFTWARE\OpenVPN`, registry.QUERY_VALUE)
	if err != nil {
		return openVPNSettings{}, errors.New("OpenVPN Community 설치 정보를 찾지 못했습니다")
	}
	defer key.Close()
	installDirectory, _, err := key.GetStringValue("")
	if err != nil || strings.TrimSpace(installDirectory) == "" {
		return openVPNSettings{}, errors.New("OpenVPN 설치 디렉터리 레지스트리 값이 없습니다")
	}
	configDirectory, _, err := key.GetStringValue("config_dir")
	if err != nil || strings.TrimSpace(configDirectory) == "" {
		configDirectory = filepath.Join(installDirectory, "config")
	}
	return openVPNSettings{
		InstallDirectory: filepath.Clean(installDirectory),
		ConfigDirectory:  filepath.Clean(configDirectory),
	}, nil
}

func interactiveProfileDirectory(settings openVPNSettings) (string, error) {
	token, err := windows.OpenCurrentProcessToken()
	if err != nil {
		return "", err
	}
	defer token.Close()
	user, err := token.GetTokenUser()
	if err != nil {
		return "", err
	}
	directory := filepath.Join(
		settings.ConfigDirectory,
		"mrcha-secuway",
		user.User.Sid.String(),
	)
	info, err := os.Lstat(directory)
	if err != nil {
		if os.IsNotExist(err) {
			return "", errors.New("Windows 1회 설치가 필요합니다: OpenVPN config_dir의 사용자 전용 디렉터리가 없습니다")
		}
		return "", err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", errors.New("OpenVPN 사용자 프로필 경로가 안전한 디렉터리가 아닙니다")
	}
	path, err := windows.UTF16PtrFromString(directory)
	if err != nil {
		return "", err
	}
	attributes, err := windows.GetFileAttributes(path)
	if err != nil {
		return "", err
	}
	if attributes&windows.FILE_ATTRIBUTE_REPARSE_POINT != 0 {
		return "", errors.New("OpenVPN 사용자 프로필 경로는 reparse point일 수 없습니다")
	}
	return directory, nil
}

func materializeInteractiveProfile(
	directory string,
	data []byte,
) (string, string, func(), error) {
	configuration := windowsConfiguration(data)
	defer clear(configuration)

	profile, err := os.CreateTemp(directory, ".secuway-*.ovpn")
	if err != nil {
		return "", "", func() {}, fmt.Errorf("서비스 승인 프로필 생성 실패: %w", err)
	}
	profilePath := profile.Name()
	cleanup := func() {
		os.Remove(profilePath)
	}
	if _, err := profile.Write(configuration); err != nil {
		profile.Close()
		cleanup()
		return "", "", func() {}, err
	}
	if err := profile.Sync(); err != nil {
		profile.Close()
		cleanup()
		return "", "", func() {}, err
	}
	if err := profile.Close(); err != nil {
		cleanup()
		return "", "", func() {}, err
	}

	logFile, err := os.CreateTemp(directory, ".secuway-*.log")
	if err != nil {
		cleanup()
		return "", "", func() {}, err
	}
	logPath := logFile.Name()
	if err := logFile.Close(); err != nil {
		os.Remove(logPath)
		cleanup()
		return "", "", func() {}, err
	}
	fullCleanup := func() {
		os.Remove(profilePath)
		os.Remove(logPath)
	}
	return profilePath, logPath, fullCleanup, nil
}

func windowsConfiguration(configuration []byte) []byte {
	output := make([]byte, 0, len(configuration)+96)
	output = append(output, configuration...)
	if len(output) > 0 && output[len(output)-1] != '\n' {
		output = append(output, '\n')
	}
	output = append(output,
		"providers lea default\n"+
			"disable-dco\n"+
			"windows-driver tap-windows6\n"...,
	)
	return output
}

func openInteractivePipe() (windows.Handle, error) {
	name, err := windows.UTF16PtrFromString(interactivePipe)
	if err != nil {
		return 0, err
	}
	var handle windows.Handle
	deadline := time.Now().Add(5 * time.Second)
	for {
		handle, err = windows.CreateFile(
			name,
			windows.GENERIC_READ|windows.GENERIC_WRITE,
			0,
			nil,
			windows.OPEN_EXISTING,
			0,
			0,
		)
		if err == nil {
			break
		}
		if !errors.Is(err, windows.ERROR_PIPE_BUSY) || time.Now().After(deadline) {
			return 0, fmt.Errorf("OpenVPNServiceInteractive 연결 실패: %w", err)
		}
		time.Sleep(100 * time.Millisecond)
	}
	mode := uint32(windows.PIPE_READMODE_MESSAGE)
	if err := windows.SetNamedPipeHandleState(handle, &mode, nil, nil); err != nil {
		windows.CloseHandle(handle)
		return 0, fmt.Errorf("OpenVPN Interactive Service pipe 모드 설정 실패: %w", err)
	}
	return handle, nil
}

func startupMessage(workingDirectory, options string) []byte {
	units := make([]uint16, 0, len(workingDirectory)+len(options)+3)
	units = append(units, utf16.Encode([]rune(workingDirectory))...)
	units = append(units, 0)
	units = append(units, utf16.Encode([]rune(options))...)
	units = append(units, 0, 0)
	message := make([]byte, len(units)*2)
	for index, value := range units {
		binary.LittleEndian.PutUint16(message[index*2:], value)
	}
	clear(units)
	return message
}

type pipeResponse struct {
	message string
	err     error
}

func readInteractiveResponseWithTimeout(pipe windows.Handle, timeout time.Duration) (string, error) {
	result := make(chan pipeResponse, 1)
	go func() {
		message, err := readInteractiveResponse(pipe)
		result <- pipeResponse{message: message, err: err}
	}()
	select {
	case response := <-result:
		return response.message, response.err
	case <-time.After(timeout):
		return "", errors.New("OpenVPN Interactive Service 응답 시간이 초과되었습니다")
	}
}

func readInteractiveResponse(pipe windows.Handle) (string, error) {
	message := make([]byte, 0, 4096)
	for {
		buffer := make([]byte, 4096)
		var read uint32
		err := windows.ReadFile(pipe, buffer, &read, nil)
		message = append(message, buffer[:read]...)
		if err == nil {
			break
		}
		if errors.Is(err, windows.ERROR_MORE_DATA) {
			continue
		}
		return "", fmt.Errorf("OpenVPN Interactive Service 응답 읽기 실패: %w", err)
	}
	if len(message)%2 != 0 {
		return "", errors.New("OpenVPN Interactive Service가 잘못된 UTF-16 응답을 반환했습니다")
	}
	units := make([]uint16, len(message)/2)
	for index := range units {
		units[index] = binary.LittleEndian.Uint16(message[index*2:])
	}
	clear(message)
	decoded := strings.TrimRight(string(utf16.Decode(units)), "\x00\r\n")
	clear(units)
	return decoded, nil
}

var processIDResponse = regexp.MustCompile(`(?m)^0x00000000\n0x([0-9A-Fa-f]{8})\nProcess ID$`)

func parseInteractiveProcessID(response string) (uint32, error) {
	normalized := strings.ReplaceAll(response, "\r\n", "\n")
	match := processIDResponse.FindStringSubmatch(normalized)
	if len(match) != 2 {
		firstLine := normalized
		if separator := strings.IndexByte(firstLine, '\n'); separator >= 0 {
			firstLine = firstLine[:separator]
		}
		if len(firstLine) > 80 {
			firstLine = firstLine[:80]
		}
		return 0, fmt.Errorf("OpenVPN Interactive Service가 시작을 거부했습니다 (%s)", firstLine)
	}
	value, err := strconv.ParseUint(match[1], 16, 32)
	if err != nil || value == 0 {
		return 0, errors.New("OpenVPN Interactive Service가 잘못된 process ID를 반환했습니다")
	}
	return uint32(value), nil
}

func monitorInteractiveProcess(processID uint32) error {
	process, err := windows.OpenProcess(
		windows.PROCESS_TERMINATE|windows.SYNCHRONIZE|windows.PROCESS_QUERY_LIMITED_INFORMATION,
		false,
		processID,
	)
	if err != nil {
		return fmt.Errorf("OpenVPN process 열기 실패: %w", err)
	}
	defer windows.CloseHandle(process)

	waited := make(chan error, 1)
	go func() {
		event, err := windows.WaitForSingleObject(process, windows.INFINITE)
		if err == nil && event != windows.WAIT_OBJECT_0 {
			err = fmt.Errorf("예상하지 못한 Windows wait 상태: 0x%x", event)
		}
		waited <- err
	}()

	interrupt := make(chan os.Signal, 1)
	signal.Notify(interrupt, os.Interrupt)
	defer signal.Stop(interrupt)
	select {
	case err := <-waited:
		if err != nil {
			return err
		}
		var exitCode uint32
		if err := windows.GetExitCodeProcess(process, &exitCode); err != nil {
			return err
		}
		if exitCode != 0 {
			return fmt.Errorf("OpenVPN process가 코드 %d로 종료되었습니다", exitCode)
		}
		return nil
	case <-interrupt:
		if err := windows.TerminateProcess(process, 130); err != nil {
			return fmt.Errorf("OpenVPN process 종료 실패: %w", err)
		}
		<-waited
		return errors.New("OpenVPN 연결이 사용자에 의해 중단되었습니다")
	}
}
