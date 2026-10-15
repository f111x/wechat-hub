#!/usr/bin/env python3
"""
收紧授权材料目录的 Windows ACL，使其满足 rion-wechat-reader 的安全要求：
  - 属主 = 当前用户
  - 仅当前用户 和 SYSTEM 有 Allow 规则
  - 关闭权限继承

用法：
  python secure_acl.py <文件或目录>
  python secure_acl.py --check <文件或目录>   # 只检查不修改
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$target = $env:RION_ACL_TARGET
$checkOnly = $env:RION_ACL_CHECK -eq '1'

$item = Get-Item -LiteralPath $target -Force
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')

function Get-State {
    param([string]$Path)
    $acl = Get-Acl -LiteralPath $Path
    $ownerOk = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq $sid.Value
    $selfOk = $false
    $badRules = @()
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow') {
            if ($rule.IdentityReference.Value -notin @($sid.Value, 'S-1-5-18')) {
                $badRules += $rule.IdentityReference.Value
            }
            if ($rule.IdentityReference.Value -eq $sid.Value) { $selfOk = $true }
        }
    }
    return @{ ownerOk = $ownerOk; selfOk = $selfOk; bad = $badRules }
}

$before = Get-State $target
Write-Output ("CHECK owner={0} self={1} bad_count={2}" -f $before.ownerOk, $before.selfOk, $before.bad.Count)

# 幂等：已经 SAFE 就不动。
# 原因：对已收紧过的对象再次调用 SetOwner 会要求 SeSecurityPrivilege，
# 普通用户没有该权限会直接失败——而权限本身其实已经是对的。
$alreadySafe = ($before.ownerOk -and $before.selfOk -and $before.bad.Count -eq 0)

if (-not $checkOnly -and -not $alreadySafe) {
    if ((Get-Item -LiteralPath $target -Force).PSIsContainer) {
        $new = [Security.AccessControl.DirectorySecurity]::new()
        $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    } else {
        $new = [Security.AccessControl.FileSecurity]::new()
        $inherit = [Security.AccessControl.InheritanceFlags]::new()
    }
    $new.SetOwner($sid)
    $new.SetAccessRuleProtection($true, $false)
    foreach ($identity in @($sid, $system)) {
        $new.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $identity, 'FullControl', $inherit, 'None', 'Allow'))
    }
    Set-Acl -LiteralPath $target -AclObject $new
    Write-Output 'FIXED'
}
elseif ($alreadySafe) {
    Write-Output 'ALREADY_SAFE'
}

$after = Get-State $target
$state = if ($after.ownerOk -and $after.selfOk -and $after.bad.Count -eq 0) { 'SAFE' } else { 'UNSAFE' }
Write-Output ("RESULT {0}" -f $state)
if ($state -ne 'SAFE') { exit 1 }
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="收紧/检查授权材料的 Windows ACL")
    parser.add_argument("target", help="文件或目录路径")
    parser.add_argument("--check", action="store_true", help="只检查不修改")
    args = parser.parse_args()

    path = Path(args.target).expanduser()
    if not path.exists():
        print(f"路径不存在: {path}", file=sys.stderr)
        return 2

    env = {**os.environ, "RION_ACL_TARGET": str(path), "RION_ACL_CHECK": "1" if args.check else "0"}
    shell = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", PS_SCRIPT],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
        env=env,
    )
    out = (shell.stdout or "").strip()
    err = (shell.stderr or "").strip()
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return 0 if "RESULT SAFE" in out else 1


if __name__ == "__main__":
    raise SystemExit(main())