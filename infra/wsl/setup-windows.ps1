<#
.SYNOPSIS
Chuẩn bị Windows của một laptop để chạy node minicloud trong WSL2 (ADR-0006).

.DESCRIPTION
Chạy trong PowerShell mở bằng "Run as administrator". Việc nào đã làm rồi thì bỏ qua:
  1. %UserProfile%\.wslconfig: bật mạng mirrored, để WSL dùng chung IP LAN với Windows.
  2. Tường lửa Hyper-V của WSL: mở cổng 8443 (cụm Incus) và 20000-29999 (cổng công khai của
     app). Hai cổng này chỉ mở khi Windows coi mạng đang nối là Private.
  3. Khi cắm sạc: không ngủ, không ngủ đông, gập máy không làm gì. Giá trị cũ được lưu lại.
  4. Tác vụ "minicloud-wsl": tự bật WSL khi Windows khởi động, không cần đăng nhập.
  5. Tuỳ chọn -MoveDistroTo: chuyển đĩa ảo của distro sang thư mục khác (ổ D:, SSD di động).

Thêm -WhatIf để xem trước mà không đổi gì. Thêm -Undo để hoàn tác các việc 1-4.
Sau khi chạy phải "wsl --shutdown" để WSL nhận cấu hình mạng mới; mọi distro WSL sẽ dừng.
Thêm -SkipNetwork để bỏ qua việc 1 và 2, cho máy chỉ chạy staging một node (laptop-1 tạm), không
vào cụm trong mạng LAN. Khi đó không phải "wsl --shutdown", và staging chạy liền mạch.
Hướng dẫn đầy đủ: docs/buoi-thu-wsl.md. File phải lưu UTF-8 có BOM cho Windows PowerShell 5.1.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File infra\wsl\setup-windows.ps1 -Distro Ubuntu-24.04 -WhatIf

.EXAMPLE
powershell -ExecutionPolicy Bypass -File infra\wsl\setup-windows.ps1 -Distro Ubuntu -SkipNetwork

.EXAMPLE
powershell -ExecutionPolicy Bypass -File infra\wsl\setup-windows.ps1 -Distro Ubuntu-24.04 -MoveDistroTo D:\WSL\Ubuntu-24.04
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    # Tên distro WSL chạy node (xem bằng: wsl -l -v).
    [string]$Distro = 'Ubuntu-24.04',
    # RAM tối đa cho WSL, tính bằng GB; 0 giữ mặc định của WSL (một nửa RAM của máy).
    [ValidateRange(0, 256)][int]$WslMemoryGB = 0,
    # Thư mục mới cho đĩa ảo của distro, ví dụ D:\WSL\Ubuntu-24.04.
    [string]$MoveDistroTo,
    # Bỏ qua mạng mirrored và tường lửa: máy chỉ chạy staging, không vào cụm LAN.
    [switch]$SkipNetwork,
    [switch]$Undo
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$WslCreator = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'   # ID của WSL trong tường lửa Hyper-V
$Rules = @(
    @{ Name = 'minicloud-incus-cluster'; Ports = '8443'; Display = 'minicloud: cụm Incus' },
    @{ Name = 'minicloud-public-ports'; Ports = '20000-29999'; Display = 'minicloud: cổng công khai của app' }
)
$TaskName = 'minicloud-wsl'
$StateDir = Join-Path $env:LOCALAPPDATA 'minicloud'
$PowerFile = Join-Path $StateDir 'power-before.json'
$WslConfig = Join-Path $env:USERPROFILE '.wslconfig'
$WslConfigBackup = "$WslConfig.minicloud-backup"
$SubButtons = '4f971e89-eebd-4455-a8de-9e59040e7347'
$LidAction = '5ca83367-6e45-459f-a27b-476b1d01c936'   # bị ẩn trên nhiều máy Modern Standby
$User = "$env:USERDOMAIN\$env:USERNAME"

function Write-Step([string]$Text) { Write-Output "==> $Text" }

function Set-IniValue([string[]]$Lines, [string]$Section, [string]$Key, [string]$Value) {
    # Đặt key=value trong [Section], giữ nguyên các dòng khác của file.
    $out = New-Object System.Collections.Generic.List[string]
    $inSection = $false
    $sectionSeen = $false
    $done = $false
    foreach ($line in $Lines) {
        if ($line -match '^\s*\[(.+?)\]\s*$') {
            if ($inSection -and -not $done) { $out.Add("$Key=$Value"); $done = $true }
            $inSection = ($Matches[1] -ieq $Section)
            if ($inSection) { $sectionSeen = $true }
            $out.Add($line)
            continue
        }
        if ($inSection -and $line -match ('^\s*' + [regex]::Escape($Key) + '\s*=')) {
            if (-not $done) { $out.Add("$Key=$Value"); $done = $true }
            continue
        }
        $out.Add($line)
    }
    if ($inSection -and -not $done) { $out.Add("$Key=$Value"); $done = $true }
    if (-not $sectionSeen) {
        if ($out.Count -gt 0 -and $out[$out.Count - 1] -ne '') { $out.Add('') }
        $out.Add("[$Section]")
        $out.Add("$Key=$Value")
    }
    return , $out.ToArray()
}

function Get-AcIndex([string]$Sub, [string]$Setting) {
    # Giá trị lúc cắm sạc: dòng hex áp chót của "powercfg /query" (dòng cuối là lúc chạy pin).
    # Không dựa vào chữ, vì chữ của powercfg đổi theo ngôn ngữ Windows.
    $hex = @(& powercfg.exe /query SCHEME_CURRENT $Sub $Setting 2>$null |
            Where-Object { $_ -match ':\s*0x([0-9a-fA-F]+)\s*$' } |
            ForEach-Object { [Convert]::ToInt32(($_ -replace '^.*0x', ''), 16) })
    if ($hex.Count -ge 2) { return $hex[$hex.Count - 2] }
    return $null
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin) -and -not $WhatIfPreference) {
    throw 'Cần mở PowerShell bằng "Run as administrator" (hoặc thêm -WhatIf để chỉ xem trước).'
}
if (-not $SkipNetwork -and [Environment]::OSVersion.Version.Build -lt 22621) {
    throw 'Mạng mirrored của WSL cần Windows 11 22H2 trở lên.'
}

# ---------------------------------------------------------------- Hoàn tác

if ($Undo) {
    Write-Step 'Gỡ luật tường lửa Hyper-V'
    foreach ($rule in $Rules) {
        $existing = Get-NetFirewallHyperVRule -Name $rule.Name -ErrorAction SilentlyContinue
        if ($existing -and $PSCmdlet.ShouldProcess($rule.Name, 'Xoá luật tường lửa Hyper-V')) {
            Remove-NetFirewallHyperVRule -Name $rule.Name
        }
    }
    Write-Step "Gỡ tác vụ $TaskName"
    if ((Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) -and
        $PSCmdlet.ShouldProcess($TaskName, 'Xoá tác vụ')) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Write-Step 'Trả lại .wslconfig'
    if (Test-Path -LiteralPath $WslConfigBackup) {
        if ($PSCmdlet.ShouldProcess($WslConfig, 'Chép lại bản trước khi sửa')) {
            Move-Item -LiteralPath $WslConfigBackup -Destination $WslConfig -Force
        }
    } elseif (Test-Path -LiteralPath $WslConfig) {
        $kept = @(Get-Content -LiteralPath $WslConfig | Where-Object { $_ -notmatch '^\s*networkingMode\s*=' })
        if ($PSCmdlet.ShouldProcess($WslConfig, 'Bỏ dòng networkingMode')) {
            [IO.File]::WriteAllLines($WslConfig, [string[]]$kept)
        }
    }
    Write-Step 'Trả lại cài đặt nguồn điện lúc cắm sạc'
    if (Test-Path -LiteralPath $PowerFile) {
        $before = Get-Content -LiteralPath $PowerFile -Raw | ConvertFrom-Json
        if ($PSCmdlet.ShouldProcess('powercfg', 'Đặt lại thời gian ngủ, ngủ đông và hành động gập máy')) {
            if ($null -ne $before.StandbySeconds) { & powercfg.exe /change standby-timeout-ac ([int]($before.StandbySeconds / 60)) }
            if ($null -ne $before.HibernateSeconds) { & powercfg.exe /change hibernate-timeout-ac ([int]($before.HibernateSeconds / 60)) }
            $lid = if ($null -ne $before.LidAction) { [int]$before.LidAction } else { 1 }   # 1 = ngủ, mặc định của Windows
            & powercfg.exe /setacvalueindex SCHEME_CURRENT $SubButtons $LidAction $lid
            & powercfg.exe /setactive SCHEME_CURRENT
            Remove-Item -LiteralPath $PowerFile
        }
    } else {
        Write-Output '    Không có file lưu giá trị cũ, bỏ qua.'
    }
    Write-Output 'Xong. Chạy "wsl --shutdown" để WSL quay lại mạng NAT.'
    return
}

# ---------------------------------------------------------------- 1. .wslconfig

if ($SkipNetwork) {
    Write-Step 'Bỏ qua mạng mirrored và tường lửa (-SkipNetwork): WSL giữ mạng hiện tại'
} else {
Write-Step "Bật mạng mirrored trong $WslConfig"
$lines = @()
if (Test-Path -LiteralPath $WslConfig) { $lines = @(Get-Content -LiteralPath $WslConfig) }
$new = Set-IniValue $lines 'wsl2' 'networkingMode' 'mirrored'
if ($WslMemoryGB -gt 0) { $new = Set-IniValue $new 'wsl2' 'memory' "$($WslMemoryGB)GB" }
if ((($lines -join "`n").Trim()) -eq (($new -join "`n").Trim())) {
    Write-Output '    Đã đúng từ trước.'
} elseif ($PSCmdlet.ShouldProcess($WslConfig, 'Ghi networkingMode=mirrored')) {
    if ((Test-Path -LiteralPath $WslConfig) -and -not (Test-Path -LiteralPath $WslConfigBackup)) {
        Copy-Item -LiteralPath $WslConfig -Destination $WslConfigBackup
    }
    [IO.File]::WriteAllLines($WslConfig, [string[]]$new)
    Write-Output '    Đã ghi.'
}

# ---------------------------------------------------------------- 2. Tường lửa Hyper-V

Write-Step 'Mở cổng cho WSL trên tường lửa Hyper-V (chỉ với mạng Private)'
foreach ($rule in $Rules) {
    if (Get-NetFirewallHyperVRule -Name $rule.Name -ErrorAction SilentlyContinue) {
        Write-Output "    $($rule.Name): đã có."
    } elseif ($PSCmdlet.ShouldProcess("$($rule.Name) (TCP $($rule.Ports))", 'Tạo luật cho phép đi vào')) {
        $null = New-NetFirewallHyperVRule -Name $rule.Name -DisplayName $rule.Display -Direction Inbound `
            -VMCreatorId $WslCreator -Protocol TCP -LocalPorts $rule.Ports -Action Allow -Profiles Private
        Write-Output "    $($rule.Name): đã tạo."
    }
}
foreach ($netProfile in @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)) {
    Write-Output "    Mạng đang nối: $($netProfile.Name) ($($netProfile.InterfaceAlias)) là $($netProfile.NetworkCategory)."
    if ("$($netProfile.NetworkCategory)" -eq 'Public') {
        Write-Output '    Cần chú ý: mạng này là Public nên cổng vẫn đóng. Ở mạng LAN của buổi thử, chuyển sang Private.'
    }
}
}   # hết phần mạng (bỏ qua khi -SkipNetwork)

# ---------------------------------------------------------------- 3. Nguồn điện khi cắm sạc

Write-Step 'Khi cắm sạc: không ngủ, không ngủ đông, gập máy không làm gì'
$current = [ordered]@{
    StandbySeconds   = Get-AcIndex 'SUB_SLEEP' 'STANDBYIDLE'
    HibernateSeconds = Get-AcIndex 'SUB_SLEEP' 'HIBERNATEIDLE'
    LidAction        = Get-AcIndex $SubButtons $LidAction
}
Write-Output ("    Hiện tại: ngủ sau {0} s, ngủ đông sau {1} s, gập máy = {2} (0 là không làm gì)." -f `
        $(if ($null -ne $current.StandbySeconds) { $current.StandbySeconds } else { '?' }),
    $(if ($null -ne $current.HibernateSeconds) { $current.HibernateSeconds } else { '?' }),
    $(if ($null -ne $current.LidAction) { $current.LidAction } else { '? (bị ẩn)' }))
if ($current.StandbySeconds -eq 0 -and $current.HibernateSeconds -eq 0 -and $current.LidAction -eq 0) {
    Write-Output '    Đã đúng từ trước.'
} elseif ($PSCmdlet.ShouldProcess('powercfg (lúc cắm sạc)', 'Tắt ngủ, ngủ đông và hành động gập máy')) {
    if (-not (Test-Path -LiteralPath $PowerFile)) {   # chỉ lưu lần đầu, để -Undo trả về đúng giá trị gốc
        $null = New-Item -ItemType Directory -Force -Path $StateDir
        ($current | ConvertTo-Json) | Set-Content -LiteralPath $PowerFile -Encoding UTF8
    }
    & powercfg.exe /change standby-timeout-ac 0
    & powercfg.exe /change hibernate-timeout-ac 0
    & powercfg.exe /setacvalueindex SCHEME_CURRENT $SubButtons $LidAction 0
    & powercfg.exe /setactive SCHEME_CURRENT
    Write-Output "    Đã đặt. Giá trị cũ lưu ở $PowerFile."
}

# ---------------------------------------------------------------- 4. Tự bật WSL khi khởi động

Write-Step "Tác vụ $TaskName chạy WSL ($Distro) lúc Windows khởi động"
$wslExe = Join-Path $env:SystemRoot 'System32\wsl.exe'
$argument = "-d $Distro -u root --exec sleep infinity"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task -and "$($task.Actions[0].Arguments)" -eq $argument) {
    Write-Output '    Đã có.'
} elseif ($PSCmdlet.ShouldProcess($TaskName, "Đăng ký tác vụ chạy: wsl.exe $argument")) {
    $action = New-ScheduledTaskAction -Execute $wslExe -Argument $argument
    $triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $User))
    # S4U: chạy được khi chưa ai đăng nhập mà không phải lưu mật khẩu Windows.
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    $null = Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal `
        -Settings $settings -Description 'minicloud: giữ WSL chạy để node Incus luôn sẵn sàng (ADR-0006)' -Force
    Write-Output '    Đã đăng ký.'
}

# ---------------------------------------------------------------- 5. Chuyển đĩa ảo (tuỳ chọn)

if ($MoveDistroTo) {
    Write-Step "Chuyển đĩa ảo của $Distro sang $MoveDistroTo"
    if ($PSCmdlet.ShouldProcess($Distro, "Dừng distro rồi chuyển đĩa ảo sang $MoveDistroTo")) {
        & $wslExe --terminate $Distro
        & $wslExe --manage $Distro --move $MoveDistroTo
        if ($LASTEXITCODE -ne 0) { throw "Không chuyển được đĩa ảo (wsl.exe trả về $LASTEXITCODE)." }
        Write-Output '    Đã chuyển.'
    }
}

Write-Output ''
if ($SkipNetwork) {
    Write-Output 'Việc tiếp theo: không cần wsl --shutdown. Tác vụ có hiệu lực từ lần Windows khởi động sau.'
    Write-Output "  Xem tác vụ: Get-ScheduledTask -TaskName $TaskName"
    Write-Output '  Thử: khởi động lại Windows, chờ 3 phút rồi mở app từ điện thoại (chưa đăng nhập cũng phải chạy).'
    return
}
Write-Output 'Việc tiếp theo:'
Write-Output '  1. wsl --shutdown   (mọi distro WSL dừng, kể cả staging nếu đây là laptop-1)'
Write-Output "  2. wsl -d $Distro -- ip -4 -br addr   phải thấy IP LAN của Windows"
Write-Output '  3. Ở mạng LAN của buổi thử: Settings > Network & internet > (mạng đang nối) > Private network'
