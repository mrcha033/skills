//go:build linux

package store

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"
)

type fileStore struct {
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
	if err := os.Chmod(root, 0o700); err != nil {
		return nil, err
	}
	return &fileStore{root: root}, nil
}

func (s *fileStore) Kind() string { return "protected-file" }

func (s *fileStore) Status(account string) (Status, error) {
	info, err := s.validate(account)
	if os.IsNotExist(err) {
		return EmptyStatus(account, s.Kind()), nil
	}
	if err != nil {
		return Status{}, err
	}
	created, modified := info.ModTime(), info.ModTime()
	return CachedStatus(account, s.Kind(), &created, &modified), nil
}

func (s *fileStore) Load(account string) ([]byte, bool, error) {
	if _, err := s.validate(account); os.IsNotExist(err) {
		return nil, false, nil
	} else if err != nil {
		return nil, false, err
	}
	data, err := os.ReadFile(s.path(account))
	return data, err == nil, err
}

func (s *fileStore) Save(account string, data []byte) error {
	target := s.path(account)
	if info, err := os.Lstat(target); err == nil {
		if !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 {
			return fmt.Errorf("기존 프로필이 안전한 mode-0600 일반 파일이 아닙니다")
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	temp, err := os.CreateTemp(s.root, ".profile-")
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
	if err := temp.Sync(); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return os.Rename(name, target)
}

func (s *fileStore) Delete(account string) (bool, error) {
	if _, err := s.validate(account); os.IsNotExist(err) {
		return false, nil
	} else if err != nil {
		return false, err
	}
	return true, os.Remove(s.path(account))
}

func (s *fileStore) path(account string) string {
	return filepath.Join(s.root, Filename(account))
}

func (s *fileStore) validate(account string) (os.FileInfo, error) {
	info, err := os.Lstat(s.path(account))
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 {
		return nil, fmt.Errorf("VPN 프로필은 mode-0600 일반 파일이어야 합니다")
	}
	if stat, ok := info.Sys().(*syscall.Stat_t); !ok || stat.Uid != uint32(os.Geteuid()) {
		return nil, fmt.Errorf("VPN 프로필은 현재 사용자 소유여야 합니다")
	}
	return info, nil
}
