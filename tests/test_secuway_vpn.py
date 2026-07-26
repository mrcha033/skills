#!/usr/bin/env python3
"""Validate Secuway VPN skill assets, source linkage, and secret boundaries."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "secuway-vpn"
ASSETS = SKILL / "assets"
LICENSES = SKILL / "licenses"
TOOL = ROOT / "tools" / "secuway-native"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pe_header(path: Path) -> tuple[int, bool]:
    data = path.read_bytes()
    assert data[:2] == b"MZ", f"{path} is not PE"
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe_offset : pe_offset + 4] == b"PE\0\0"
    machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
    characteristics = struct.unpack_from("<H", data, pe_offset + 22)[0]
    return machine, bool(characteristics & 0x2000)


def main() -> None:
    manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "secuway-windows-assets/v1"
    assert manifest["version"] == "0.4.0"
    assert (
        manifest["source"]["provider_source_sha256"]
        == sha256(TOOL / "src" / "lea_provider.cpp")
    )
    assert (
        manifest["source"]["provider_smoke_source_sha256"]
        == sha256(TOOL / "tests" / "provider_smoke.c")
    )

    expected_machine = {"amd64": 0x8664, "arm64": 0xAA64}
    for relative, record in manifest["assets"].items():
        path = ASSETS / relative
        assert path.is_file(), f"missing asset: {relative}"
        assert path.stat().st_size == record["bytes"]
        assert sha256(path) == record["sha256"]
        architecture = relative.split("/", 1)[0].removeprefix("windows-")
        machine, is_dll = pe_header(path)
        assert machine == expected_machine[architecture]
        assert is_dll == relative.endswith("/lea.dll")

    license_manifest = json.loads(
        (LICENSES / "manifest.json").read_text(encoding="utf-8")
    )
    assert license_manifest["schema"] == "secuway-third-party-licenses/v1"
    expected_components = {
        "Go toolchain and standard library": (
            "1.25.4",
            "f2cd93aa0505465c1d30201c806b6d4d3481c5fa",
        ),
        "golang.org/x/sys": (
            "v0.47.0",
            "9e7e939dcafac07e8ab4cffa6e5fc74908413f00",
        ),
        "golang.org/x/term": (
            "v0.45.0",
            "9f69229da31ca6a34b522f59dbe07cad5ea21587",
        ),
        "Crypto++": (
            "8.9.0",
            "843d74c7c97f9e19a615b8ff3c0ca06599ca501b",
        ),
        "GCC runtime libraries": (
            "12.2.0",
            "2ee5e4300186a92ad73f1a1a64cb918dc76c8d67",
        ),
        "LLVM runtime libraries": (
            "22.1.8",
            "ca7933e47d3a3451d81e72ac174dcb5aa28b59d1",
        ),
        "MinGW-w64 runtime (AMD64 build)": (
            "10.0.0",
            "aa08f56da559016f10336dddca85d59f9bdc9e02",
        ),
        "MinGW-w64 runtime (ARM64 build)": (
            "c28e9555bb8800c53449f42a465ad9a5676fce88",
            "c28e9555bb8800c53449f42a465ad9a5676fce88",
        ),
    }
    components = {
        component["name"]: component
        for component in license_manifest["components"]
    }
    assert set(components) == set(expected_components)

    distributed_assets = set(manifest["assets"])
    covered_assets: set[str] = set()
    licensed_files: set[str] = set()
    for name, (version, revision) in expected_components.items():
        component = components[name]
        assert component["version"] == version
        assert component["revision"] == revision
        assert component["source_url"].startswith("https://")
        assert component["license"]
        for relative in component["included_in"]:
            assert relative in distributed_assets
            covered_assets.add(relative)
        for record in component["files"]:
            relative = record["path"]
            assert relative not in licensed_files
            licensed_files.add(relative)
            path = LICENSES / relative
            assert path.is_file(), f"missing third-party text: {relative}"
            assert sha256(path) == record["sha256"]
            assert revision in record["source_url"]
            assert record["source_url"].startswith("https://")

    assert covered_assets == distributed_assets
    actual_license_files = {
        path.relative_to(LICENSES).as_posix()
        for path in LICENSES.rglob("*")
        if path.is_file()
    } - {"THIRD_PARTY_NOTICES.md", "manifest.json"}
    assert actual_license_files == licensed_files

    go_mod = (TOOL / "portable" / "go.mod").read_text(encoding="utf-8")
    assert "golang.org/x/sys v0.47.0" in go_mod
    assert "golang.org/x/term v0.45.0" in go_mod
    assert manifest["source"]["go_version"] == "1.25.4"
    for architecture in ("amd64", "arm64"):
        cli = (ASSETS / f"windows-{architecture}" / "secuway.exe").read_bytes()
        for marker in (
            b"go1.25.4",
            b"golang.org/x/sys",
            b"v0.47.0",
            b"golang.org/x/term",
            b"v0.45.0",
        ):
            assert marker in cli

    amd64_provider = (ASSETS / "windows-amd64" / "lea.dll").read_bytes()
    arm64_provider = (ASSETS / "windows-arm64" / "lea.dll").read_bytes()
    assert b"GCC: (GNU) 12 20220819" in amd64_provider
    assert b"Mingw-w64 runtime failure:" in amd64_provider
    assert b"libc++abi:" in arm64_provider
    assert b"libunwind:" in arm64_provider
    assert b"Mingw-w64 runtime failure:" in arm64_provider

    x64_build = (
        TOOL / "experiments" / "windows-x64" / "build.sh"
    ).read_text(encoding="utf-8")
    arm64_build = (
        TOOL / "experiments" / "windows-arm64" / "build-macos.sh"
    ).read_text(encoding="utf-8")
    arm64_versions = (
        TOOL / "experiments" / "windows-arm64" / "versions.env"
    ).read_text(encoding="utf-8")
    arm64_vcpkg = json.loads(
        (
            TOOL / "experiments" / "windows-arm64" / "vcpkg.json"
        ).read_text(encoding="utf-8")
    )
    arm64_cmake = (
        TOOL / "experiments" / "windows-arm64" / "CMakeLists.txt"
    ).read_text(encoding="utf-8")
    assert "CRYPTOPP_VERSION=8.9.0" in x64_build
    assert "-static-libgcc -static-libstdc++" in x64_build
    assert "CRYPTOPP_VERSION=8_9_0" in arm64_versions
    assert arm64_vcpkg["overrides"] == [
        {
            "name": "cryptopp",
            "version": "8.9.0",
            "port-version": 2,
        }
    ]
    assert "find_package(cryptopp 8.9.0 EXACT CONFIG REQUIRED)" in arm64_cmake
    assert '"${CRYPTOPP_INCLUDE_ROOT}/cryptopp"' in arm64_cmake
    assert "LLVM_MINGW_VERSION=20260616" in arm64_versions
    assert "-shared -static" in arm64_build

    llvm = components["LLVM runtime libraries"]
    assert llvm["statically_linked_libraries"] == [
        "libc++",
        "libc++abi",
        "libunwind",
        "compiler-rt",
    ]
    assert llvm["build_toolchain"] == {
        "name": "llvm-mingw",
        "release": "20260616",
        "source_url": "https://github.com/mstorsjo/llvm-mingw",
        "revision": "170b7e1ec4ad1d9264e6ba320cd4d02f96299c60",
        "archive_sha256": (
            "2cab02a2e964bd4aae981150a45985d07c657cfa8d244959eb9e2dcc5eedd7b1"
        ),
    }

    required_text = {
        "go/LICENSE": "Copyright 2009 The Go Authors.",
        "go/PATENTS": "Additional IP Rights Grant (Patents)",
        "cryptopp/LICENSE.txt": "Boost Software License - Version 1.0",
        "gcc/COPYING3": "GNU GENERAL PUBLIC LICENSE",
        "gcc/COPYING.RUNTIME": "GCC RUNTIME LIBRARY EXCEPTION",
        "llvm/LICENSE.TXT": (
            "The LLVM Project is under the Apache License v2.0 "
            "with LLVM Exceptions"
        ),
        "mingw-w64/amd64-COPYING.MinGW-w64-runtime.txt": (
            "MinGW-w64 runtime licensing"
        ),
        "mingw-w64/arm64-COPYING.MinGW-w64-runtime.txt": (
            "MinGW-w64 runtime licensing"
        ),
    }
    for relative, marker in required_text.items():
        text = (LICENSES / relative).read_text(encoding="utf-8")
        assert marker in text
    assert "Version 3.1, 31 March 2009" in (
        LICENSES / "gcc" / "COPYING.RUNTIME"
    ).read_text(encoding="utf-8")
    assert "CRYPTOGAMS License" in (
        LICENSES / "cryptopp" / "LICENSE.txt"
    ).read_text(encoding="utf-8")

    notices = (LICENSES / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Go 1.25.4",
        "Crypto++ 8.9.0",
        "GCC 12.2.0",
        "LLVM 22.1.8",
        "libc++abi",
        "libunwind",
        "MinGW-w64 10.0.0",
        "OpenVPN Community `2.7.5-I001`",
        "redistributed in this skill",
    ):
        assert marker in notices
    assert license_manifest["not_redistributed"] == [
        {
            "name": "OpenVPN Community",
            "version": "2.7.5-I001",
            "reason": (
                "downloaded from and installed from the official upstream MSI"
            ),
        },
        {
            "name": "OpenSSL libcrypto",
            "version": "3.6.3",
            "reason": (
                "dynamically supplied by the separately installed official "
                "OpenVPN package"
            ),
        },
    ]

    assert (SKILL / "scripts" / "Install-WindowsRuntime.ps1").read_bytes() == (
        TOOL / "experiments" / "windows-ci" / "Install-Windows.ps1"
    ).read_bytes()

    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    setup_text = (SKILL / "scripts" / "Setup-Windows.ps1").read_text(
        encoding="utf-8"
    )
    runtime_text = (
        SKILL / "scripts" / "Install-WindowsRuntime.ps1"
    ).read_text(encoding="utf-8")
    reference_text = (SKILL / "references" / "platforms.md").read_text(
        encoding="utf-8"
    )
    assert "[TODO:" not in skill_text
    assert "licenses/THIRD_PARTY_NOTICES.md" in skill_text
    assert "cannot be replaced or" in reference_text
    assert "OpenVPNServiceInteractive" in runtime_text
    assert "Get-AuthenticodeSignature" in setup_text
    assert "TargetSid" in setup_text
    assert "secrets_printed = $false" in setup_text

    forbidden = (
        "QTA_KIS_APP_KEY=",
        "QTA_KIS_APP_SECRET=",
        "BEGIN PRIVATE KEY",
    )
    for path in list(SKILL.rglob("*.md")) + list(SKILL.rglob("*.ps1")):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, f"forbidden material in {path}: {marker}"

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL / "scripts" / "secuway_status.py"),
            "--self-test",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "PASS" in result.stdout
    print("Secuway VPN skill and Windows assets: PASS")


if __name__ == "__main__":
    main()
