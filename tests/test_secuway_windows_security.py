#!/usr/bin/env python3
"""Guard the Windows privilege, rollback, and native-CI boundaries."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "secuway-vpn"
TOOLS = ROOT / "tools" / "secuway-native"
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "secuway-windows-portable.yml",
    ROOT / ".github" / "workflows" / "secuway-windows-x64-provider.yml",
    ROOT / ".github" / "workflows" / "secuway-windows-arm64-provider.yml",
    ROOT / ".github" / "workflows" / "secuway-windows-tunnel.yml",
)


def main() -> None:
    setup = (SKILL / "scripts" / "Setup-Windows.ps1").read_text()
    transaction = setup.index("$runtimeStateBefore = Get-ExistingRuntimeState")
    doctor = setup.index("& $change.Destination doctor", transaction)
    commit = setup.index("Complete-UserCliInstall", transaction)
    assert doctor < commit, "CLI state was committed before doctor"
    assert "Windows runtime rollback failed" in setup
    assert "Write-AtomicJson -Path $Change.StatePath -Value $Change.ExistingState" in setup

    runtime = (
        SKILL / "scripts" / "Install-WindowsRuntime.ps1"
    ).read_text()
    assert "if (-not [bool]$state.service_was_running)" in runtime
    assert "Stop-Service -Name $serviceName" in runtime
    assert "Start-Service -Name $serviceName -ErrorAction SilentlyContinue" in runtime
    assert (
        "\n            (Get-ChildItem -LiteralPath $baseDirectory -Force).Count"
        not in runtime
    )
    assert (
        "\n    (Get-ChildItem -LiteralPath $TargetBin -Force).Count"
        not in setup
    )

    engine = (TOOLS / "portable" / "internal" / "engine" / "engine.go").read_text()
    windows_engine = (
        TOOLS / "portable" / "internal" / "engine" / "command_windows.go"
    ).read_text()
    assert "if goos == \"windows\"" in engine
    assert "never execute a sibling or PATH-provided binary" in engine
    assert "never from the invoking user's" in engine
    assert 'filepath.Join(installDirectory, "bin", "openvpn.exe")' in windows_engine
    assert 'filepath.Join(installDirectory, "ssl", "modules")' in windows_engine
    assert 'GetStringValue("exe_path")' not in windows_engine

    main_go = (TOOLS / "portable" / "cmd" / "secuway" / "main.go").read_text()
    connect_case = main_go.index('case "connect":')
    reject = main_go.index("validateConnectConfig", connect_case)
    discover = main_go.index("engine.Discover", connect_case)
    assert reject < discover
    assert "Windows에서는 login --output을 지원하지 않습니다" in main_go
    assert "Windows에서는 connect --config를 지원하지 않습니다" in main_go

    action_pin = re.compile(
        r"(?:-\s+)?uses:\s+[^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$"
    )
    workflow_text = {}
    for path in WORKFLOWS:
        text = path.read_text()
        workflow_text[path.name] = text
        uses = [line.strip() for line in text.splitlines() if "uses:" in line]
        assert uses and all(action_pin.fullmatch(line) for line in uses), path

    portable = workflow_text["secuway-windows-portable.yml"]
    assert '"skills/secuway-vpn/**"' in portable
    assert "Setup-Windows.ps1" in portable
    assert "windows-11-arm" in portable

    x64 = workflow_text["secuway-windows-x64-provider.yml"]
    assert '"skills/secuway-vpn/**"' in x64
    assert "-Action Install" in x64 and "-Action Uninstall" in x64

    arm64 = workflow_text["secuway-windows-arm64-provider.yml"]
    assert '"skills/secuway-vpn/assets/**"' in arm64

    tunnel = workflow_text["secuway-windows-tunnel.yml"]
    assert "github.ref == 'refs/heads/main'" in tunnel
    assert "persist-credentials: false" in tunnel

    print("Secuway Windows security and CI boundaries: PASS")


if __name__ == "__main__":
    main()
