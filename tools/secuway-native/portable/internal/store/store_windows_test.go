//go:build windows

package store

import (
	"bytes"
	"os"
	"testing"
)

func TestDPAPIRoundTrip(t *testing.T) {
	t.Setenv("AppData", t.TempDir())
	backend, err := newPlatformStore()
	if err != nil {
		t.Fatal(err)
	}
	files, ok := backend.(*dpapiStore)
	if !ok {
		t.Fatalf("unexpected store type %T", backend)
	}
	account := "https://test.invalid"
	want := []byte("synthetic Secuway profile; no real credentials")

	if err := backend.Save(account, want); err != nil {
		t.Fatal(err)
	}
	encrypted, err := os.ReadFile(files.path(account))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(encrypted, want) {
		t.Fatal("DPAPI file contains the plaintext profile")
	}
	got, found, err := backend.Load(account)
	if err != nil {
		t.Fatal(err)
	}
	if !found || !bytes.Equal(got, want) {
		t.Fatalf("DPAPI round trip mismatch: found=%v", found)
	}
}
