//go:build darwin && cgo

package store

/*
#cgo LDFLAGS: -framework Security -framework CoreFoundation

#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static CFMutableDictionaryRef secuway_keychain_query(
	const char *service,
	const char *account
) {
	CFMutableDictionaryRef query = CFDictionaryCreateMutable(
		kCFAllocatorDefault,
		0,
		&kCFTypeDictionaryKeyCallBacks,
		&kCFTypeDictionaryValueCallBacks
	);
	CFStringRef serviceValue = CFStringCreateWithCString(
		kCFAllocatorDefault,
		service,
		kCFStringEncodingUTF8
	);
	CFStringRef accountValue = CFStringCreateWithCString(
		kCFAllocatorDefault,
		account,
		kCFStringEncodingUTF8
	);
	if (query == NULL || serviceValue == NULL || accountValue == NULL) {
		if (serviceValue != NULL) CFRelease(serviceValue);
		if (accountValue != NULL) CFRelease(accountValue);
		if (query != NULL) CFRelease(query);
		return NULL;
	}
	CFDictionarySetValue(query, kSecClass, kSecClassGenericPassword);
	CFDictionarySetValue(query, kSecAttrService, serviceValue);
	CFDictionarySetValue(query, kSecAttrAccount, accountValue);
	CFRelease(serviceValue);
	CFRelease(accountValue);
	return query;
}

static int32_t secuway_keychain_status(
	const char *service,
	const char *account
) {
	CFMutableDictionaryRef query = secuway_keychain_query(service, account);
	if (query == NULL) return errSecAllocate;
	CFDictionarySetValue(query, kSecMatchLimit, kSecMatchLimitOne);
	CFDictionarySetValue(query, kSecReturnAttributes, kCFBooleanTrue);
	CFTypeRef result = NULL;
	OSStatus status = SecItemCopyMatching(query, &result);
	if (result != NULL) CFRelease(result);
	CFRelease(query);
	return status;
}

static int32_t secuway_keychain_load(
	const char *service,
	const char *account,
	unsigned char **output,
	size_t *outputLength
) {
	*output = NULL;
	*outputLength = 0;
	CFMutableDictionaryRef query = secuway_keychain_query(service, account);
	if (query == NULL) return errSecAllocate;
	CFDictionarySetValue(query, kSecMatchLimit, kSecMatchLimitOne);
	CFDictionarySetValue(query, kSecReturnData, kCFBooleanTrue);
	CFTypeRef result = NULL;
	OSStatus status = SecItemCopyMatching(query, &result);
	CFRelease(query);
	if (status != errSecSuccess) return status;
	if (result == NULL || CFGetTypeID(result) != CFDataGetTypeID()) {
		if (result != NULL) CFRelease(result);
		return errSecDecode;
	}
	CFDataRef data = (CFDataRef)result;
	CFIndex length = CFDataGetLength(data);
	if (length <= 0) {
		CFRelease(result);
		return errSecDecode;
	}
	unsigned char *copy = malloc((size_t)length);
	if (copy == NULL) {
		CFRelease(result);
		return errSecAllocate;
	}
	CFDataGetBytes(data, CFRangeMake(0, length), copy);
	*output = copy;
	*outputLength = (size_t)length;
	CFRelease(result);
	return errSecSuccess;
}

static int32_t secuway_keychain_save(
	const char *service,
	const char *account,
	const unsigned char *input,
	size_t inputLength
) {
	CFDataRef data = CFDataCreate(
		kCFAllocatorDefault,
		input,
		(CFIndex)inputLength
	);
	if (data == NULL) return errSecAllocate;
	CFMutableDictionaryRef query = secuway_keychain_query(service, account);
	if (query == NULL) {
		CFRelease(data);
		return errSecAllocate;
	}
	CFMutableDictionaryRef updates = CFDictionaryCreateMutable(
		kCFAllocatorDefault,
		0,
		&kCFTypeDictionaryKeyCallBacks,
		&kCFTypeDictionaryValueCallBacks
	);
	if (updates == NULL) {
		CFRelease(query);
		CFRelease(data);
		return errSecAllocate;
	}
	CFDictionarySetValue(updates, kSecValueData, data);
	OSStatus status = SecItemUpdate(query, updates);
	CFRelease(updates);
	CFRelease(query);
	if (status == errSecItemNotFound) {
		CFMutableDictionaryRef item = secuway_keychain_query(service, account);
		if (item == NULL) {
			CFRelease(data);
			return errSecAllocate;
		}
		CFDictionarySetValue(item, kSecValueData, data);
		CFDictionarySetValue(
			item,
			kSecAttrAccessible,
			kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
		);
		status = SecItemAdd(item, NULL);
		CFRelease(item);
	}
	CFRelease(data);
	return status;
}

static int32_t secuway_keychain_delete(
	const char *service,
	const char *account
) {
	CFMutableDictionaryRef query = secuway_keychain_query(service, account);
	if (query == NULL) return errSecAllocate;
	OSStatus status = SecItemDelete(query);
	CFRelease(query);
	return status;
}
*/
import "C"

import (
	"fmt"
	"unsafe"
)

const (
	keychainService      = "io.mrcha.secuway-portable.profile"
	keychainItemNotFound = -25300
)

type keychainStore struct{}

func newPlatformStore() (Store, error) {
	return &keychainStore{}, nil
}

func (s *keychainStore) Kind() string { return "macos-keychain" }

func (s *keychainStore) Status(account string) (Status, error) {
	status := withKeychainStrings(account, func(service, item *C.char) C.int32_t {
		return C.secuway_keychain_status(service, item)
	})
	if int32(status) == keychainItemNotFound {
		return EmptyStatus(account, s.Kind()), nil
	}
	if status != 0 {
		return Status{}, keychainError("키체인 프로필 상태 확인", status)
	}
	return CachedStatus(account, s.Kind(), nil, nil), nil
}

func (s *keychainStore) Load(account string) ([]byte, bool, error) {
	service := C.CString(keychainService)
	item := C.CString(account)
	defer C.free(unsafe.Pointer(service))
	defer C.free(unsafe.Pointer(item))
	var output *C.uchar
	var outputLength C.size_t
	status := C.secuway_keychain_load(service, item, &output, &outputLength)
	if int32(status) == keychainItemNotFound {
		return nil, false, nil
	}
	if status != 0 {
		return nil, false, keychainError("키체인 프로필 읽기", status)
	}
	defer C.free(unsafe.Pointer(output))
	return C.GoBytes(unsafe.Pointer(output), C.int(outputLength)), true, nil
}

func (s *keychainStore) Save(account string, data []byte) error {
	if len(data) == 0 {
		return fmt.Errorf("빈 프로필은 저장할 수 없습니다")
	}
	service := C.CString(keychainService)
	item := C.CString(account)
	input := C.CBytes(data)
	defer C.free(unsafe.Pointer(service))
	defer C.free(unsafe.Pointer(item))
	defer C.free(input)
	status := C.secuway_keychain_save(
		service,
		item,
		(*C.uchar)(input),
		C.size_t(len(data)),
	)
	if status != 0 {
		return keychainError("키체인 프로필 저장", status)
	}
	return nil
}

func (s *keychainStore) Delete(account string) (bool, error) {
	status := withKeychainStrings(account, func(service, item *C.char) C.int32_t {
		return C.secuway_keychain_delete(service, item)
	})
	if int32(status) == keychainItemNotFound {
		return false, nil
	}
	if status != 0 {
		return false, keychainError("키체인 프로필 삭제", status)
	}
	return true, nil
}

func withKeychainStrings(account string, body func(*C.char, *C.char) C.int32_t) C.int32_t {
	service := C.CString(keychainService)
	item := C.CString(account)
	defer C.free(unsafe.Pointer(service))
	defer C.free(unsafe.Pointer(item))
	return body(service, item)
}

func keychainError(operation string, status C.int32_t) error {
	return fmt.Errorf("%s 실패 (OSStatus %d)", operation, int32(status))
}
