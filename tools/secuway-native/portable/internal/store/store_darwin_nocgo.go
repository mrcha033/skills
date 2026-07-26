//go:build darwin && !cgo

package store

import "errors"

func newPlatformStore() (Store, error) {
	return nil, errors.New("macOS Keychain 지원에는 CGO_ENABLED=1 빌드가 필요합니다")
}
