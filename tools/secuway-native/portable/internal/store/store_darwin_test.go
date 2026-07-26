//go:build darwin

package store

import (
	"bytes"
	"fmt"
	"os"
	"testing"
	"time"
)

func TestKeychainRoundTrip(t *testing.T) {
	backend, err := newPlatformStore()
	if err != nil {
		t.Fatal(err)
	}
	account := fmt.Sprintf("https://test.invalid/%d-%d", os.Getpid(), time.Now().UnixNano())
	t.Cleanup(func() {
		if _, err := backend.Delete(account); err != nil {
			t.Errorf("cleanup keychain item: %v", err)
		}
	})

	before, err := backend.Status(account)
	if err != nil {
		t.Fatal(err)
	}
	if before.Status != "NEEDS_ENROLLMENT" {
		t.Fatalf("unexpected initial status: %s", before.Status)
	}

	want := []byte("synthetic Secuway profile; no real credentials")
	if err := backend.Save(account, want); err != nil {
		t.Fatal(err)
	}
	got, found, err := backend.Load(account)
	if err != nil {
		t.Fatal(err)
	}
	if !found || !bytes.Equal(got, want) {
		t.Fatalf("keychain round trip mismatch: found=%v got-bytes=%d want-bytes=%d", found, len(got), len(want))
	}

	after, err := backend.Status(account)
	if err != nil {
		t.Fatal(err)
	}
	if after.Status != "CACHED" || after.SecretsPrinted {
		t.Fatalf("unexpected cached status: %+v", after)
	}
}
