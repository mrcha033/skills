//go:build windows

package store

import (
	"fmt"
	"os"
	"path/filepath"
	"unsafe"

	"golang.org/x/sys/windows"
)

type dpapiStore struct {
	root string
}

func newPlatformStore() (Store, error) {
	root, err := ConfigRoot()
	if err != nil {
		return nil, err
	}
	root = filepath.Join(root, "profiles")
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, err
	}
	return &dpapiStore{root: root}, nil
}

func (s *dpapiStore) Kind() string { return "windows-dpapi" }

func (s *dpapiStore) Status(account string) (Status, error) {
	info, err := os.Stat(s.path(account))
	if os.IsNotExist(err) {
		return EmptyStatus(account, s.Kind()), nil
	}
	if err != nil || !info.Mode().IsRegular() {
		return Status{}, fmt.Errorf("DPAPI 프로필 파일 상태가 올바르지 않습니다")
	}
	encrypted, err := os.ReadFile(s.path(account))
	if err != nil {
		return Status{}, err
	}
	plain, err := unprotect(encrypted)
	if err != nil {
		return Status{}, err
	}
	clear(plain)
	created, modified := info.ModTime(), info.ModTime()
	return CachedStatus(account, s.Kind(), &created, &modified), nil
}

func (s *dpapiStore) Load(account string) ([]byte, bool, error) {
	encrypted, err := os.ReadFile(s.path(account))
	if os.IsNotExist(err) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	plain, err := unprotect(encrypted)
	return plain, err == nil, err
}

func (s *dpapiStore) Save(account string, data []byte) error {
	encrypted, err := protect(data)
	if err != nil {
		return err
	}
	temp, err := os.CreateTemp(s.root, ".profile-")
	if err != nil {
		return err
	}
	name := temp.Name()
	defer os.Remove(name)
	if _, err := temp.Write(encrypted); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return windows.Rename(name, s.path(account))
}

func (s *dpapiStore) Delete(account string) (bool, error) {
	err := os.Remove(s.path(account))
	if os.IsNotExist(err) {
		return false, nil
	}
	return err == nil, err
}

func (s *dpapiStore) path(account string) string {
	return filepath.Join(s.root, Filename(account))
}

func protect(input []byte) ([]byte, error) {
	in, err := inputBlob(input)
	if err != nil {
		return nil, err
	}
	var out windows.DataBlob
	if err := windows.CryptProtectData(
		&in,
		nil,
		nil,
		0,
		nil,
		windows.CRYPTPROTECT_UI_FORBIDDEN,
		&out,
	); err != nil {
		return nil, fmt.Errorf("DPAPI 프로필 암호화 실패: %w", err)
	}
	return copyAndFree(out)
}

func unprotect(input []byte) ([]byte, error) {
	in, err := inputBlob(input)
	if err != nil {
		return nil, err
	}
	var description *uint16
	var out windows.DataBlob
	if err := windows.CryptUnprotectData(
		&in,
		&description,
		nil,
		0,
		nil,
		windows.CRYPTPROTECT_UI_FORBIDDEN,
		&out,
	); err != nil {
		return nil, fmt.Errorf("DPAPI 프로필 복호화 실패: %w", err)
	}
	if description != nil {
		defer windows.LocalFree(windows.Handle(unsafe.Pointer(description)))
	}
	return copyAndFree(out)
}

func inputBlob(input []byte) (windows.DataBlob, error) {
	if len(input) == 0 {
		return windows.DataBlob{}, fmt.Errorf("빈 프로필은 저장할 수 없습니다")
	}
	return windows.DataBlob{Size: uint32(len(input)), Data: &input[0]}, nil
}

func copyAndFree(out windows.DataBlob) ([]byte, error) {
	if out.Data == nil || out.Size == 0 {
		return nil, fmt.Errorf("DPAPI가 빈 결과를 반환했습니다")
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	return append([]byte(nil), unsafe.Slice(out.Data, int(out.Size))...), nil
}
