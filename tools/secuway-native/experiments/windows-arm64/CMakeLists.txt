cmake_minimum_required(VERSION 3.25)
project(secuway_lea_provider_arm64 LANGUAGES C CXX)

if(NOT WIN32)
    message(FATAL_ERROR "This project builds the Windows ARM64 provider")
endif()
if(CMAKE_GENERATOR_PLATFORM)
    if(NOT CMAKE_GENERATOR_PLATFORM MATCHES "^(ARM64|arm64|aarch64)$")
        message(FATAL_ERROR
            "Expected Windows ARM64 generator platform, got ${CMAKE_GENERATOR_PLATFORM}"
        )
    endif()
elseif(NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(ARM64|arm64|aarch64)$")
    message(FATAL_ERROR
        "Expected Windows ARM64 system processor, got ${CMAKE_SYSTEM_PROCESSOR}"
    )
endif()

get_filename_component(SECUWAY_NATIVE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(PROVIDER_SOURCE "${SECUWAY_NATIVE_ROOT}/src/lea_provider.cpp")
set(PROVIDER_SMOKE_SOURCE "${SECUWAY_NATIVE_ROOT}/tests/provider_smoke.c")
if(NOT EXISTS "${PROVIDER_SOURCE}")
    message(FATAL_ERROR "Provider source is missing: ${PROVIDER_SOURCE}")
endif()
if(NOT EXISTS "${PROVIDER_SMOKE_SOURCE}")
    message(FATAL_ERROR "Provider smoke source is missing: ${PROVIDER_SMOKE_SOURCE}")
endif()

find_package(OpenSSL 3.6.3 EXACT REQUIRED COMPONENTS Crypto)
find_package(cryptopp 8.9.0 EXACT CONFIG REQUIRED)
find_path(
    CRYPTOPP_INCLUDE_ROOT
    NAMES cryptopp/lea.h
    REQUIRED
)

add_library(lea MODULE "${PROVIDER_SOURCE}" "${CMAKE_CURRENT_LIST_DIR}/lea.def")
target_compile_features(lea PRIVATE cxx_std_17)
target_include_directories(lea PRIVATE "${CRYPTOPP_INCLUDE_ROOT}/cryptopp")
target_compile_definitions(lea PRIVATE
    CRYPTOPP_DISABLE_ASM
    WIN32_LEAN_AND_MEAN
    _WIN32_WINNT=0x0A00
)
target_link_libraries(lea PRIVATE OpenSSL::Crypto cryptopp::cryptopp)
set_target_properties(lea PROPERTIES
    PREFIX ""
    OUTPUT_NAME "lea"
)

add_executable(provider_smoke "${PROVIDER_SMOKE_SOURCE}")
target_compile_features(provider_smoke PRIVATE c_std_11)
target_link_libraries(provider_smoke PRIVATE OpenSSL::Crypto)

if(MSVC)
    set_target_properties(lea provider_smoke PROPERTIES
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
    )
    target_compile_options(lea PRIVATE /O2 /guard:cf /W4 /Brepro)
    target_link_options(lea PRIVATE /DYNAMICBASE /NXCOMPAT /GUARD:CF /Brepro)
    target_compile_options(provider_smoke PRIVATE /O2 /guard:cf /W4 /Brepro)
    target_link_options(
        provider_smoke PRIVATE /DYNAMICBASE /NXCOMPAT /GUARD:CF /Brepro
    )
endif()
