//go:build linux

package store

import (
	"bytes"
	"os"
	"testing"
)

func TestProtectedFileRoundTrip(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	backend, err := newPlatformStore()
	if err != nil {
		t.Fatal(err)
	}
	files, ok := backend.(*fileStore)
	if !ok {
		t.Fatalf("unexpected store type %T", backend)
	}
	account := "https://test.invalid"
	want := []byte("synthetic Secuway profile; no real credentials")

	if err := backend.Save(account, want); err != nil {
		t.Fatal(err)
	}
	info, err := os.Lstat(files.path(account))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("profile mode = %o, want 600", info.Mode().Perm())
	}
	got, found, err := backend.Load(account)
	if err != nil {
		t.Fatal(err)
	}
	if !found || !bytes.Equal(got, want) {
		t.Fatalf("protected file round trip mismatch: found=%v", found)
	}
}
