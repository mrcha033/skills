package store

import (
	"encoding/base64"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type Status struct {
	Schema          string     `json:"schema"`
	Status          string     `json:"status"`
	Server          string     `json:"server"`
	CredentialStore string     `json:"credential_store"`
	ValidationScope string     `json:"validation_scope"`
	CreatedAt       *time.Time `json:"created_at,omitempty"`
	ModifiedAt      *time.Time `json:"modified_at,omitempty"`
	SecretsPrinted  bool       `json:"secrets_printed"`
}

type Store interface {
	Kind() string
	Status(account string) (Status, error)
	Load(account string) ([]byte, bool, error)
	Save(account string, data []byte) error
	Delete(account string) (bool, error)
}

func New() (Store, error) {
	return newPlatformStore()
}

func Account(server *url.URL) string {
	origin := &url.URL{Scheme: strings.ToLower(server.Scheme), Host: strings.ToLower(server.Host)}
	return origin.String()
}

func EmptyStatus(account, kind string) Status {
	return Status{
		Schema:          "secuway-auth-status/v1",
		Status:          "NEEDS_ENROLLMENT",
		Server:          account,
		CredentialStore: kind,
		ValidationScope: "local-cache",
		SecretsPrinted:  false,
	}
}

func CachedStatus(account, kind string, created, modified *time.Time) Status {
	return Status{
		Schema:          "secuway-auth-status/v1",
		Status:          "CACHED",
		Server:          account,
		CredentialStore: kind,
		ValidationScope: "local-cache",
		CreatedAt:       created,
		ModifiedAt:      modified,
		SecretsPrinted:  false,
	}
}

func ConfigRoot() (string, error) {
	root, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	root = filepath.Join(root, "mrcha-skills", "secuway")
	if err := os.MkdirAll(root, 0o700); err != nil {
		return "", err
	}
	if err := os.Chmod(root, 0o700); err != nil && !errors.Is(err, os.ErrPermission) {
		return "", err
	}
	return root, nil
}

func Filename(account string) string {
	name := base64.RawURLEncoding.EncodeToString([]byte(account))
	return name + ".profile"
}
