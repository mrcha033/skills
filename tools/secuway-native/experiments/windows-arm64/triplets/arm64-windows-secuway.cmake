set(VCPKG_TARGET_ARCHITECTURE arm64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE dynamic)

# Crypto++ is used only inside lea.dll and its vcpkg port supports static builds.
if(PORT STREQUAL "cryptopp")
    set(VCPKG_LIBRARY_LINKAGE static)
endif()
