<#
.SYNOPSIS
Kiểm tra một laptop Windows trước khi đưa vào cụm minicloud.

.DESCRIPTION
In ra dòng cho bảng phần cứng trong docs/hardware.md, ghi chú cho máy đó, và những điểm cần
chú ý khi cài Ubuntu, dùng Wake-on-LAN và đo điện cho Green cloud. Script chỉ đọc thông tin:
không cần quyền admin và không đổi cấu hình nào. Báo cáo được lưu thêm thành file Markdown
trong thư mục %TEMP%.

Thêm -MeasurePower sau khi đã rút sạc để đo điện của cả máy qua pin: một quãng lúc nhàn, rồi
một quãng chạy hết mọi luồng CPU. Kết quả là hai ô "rút sạc" của bảng số đo điện năng.

Chạy được trên Windows PowerShell 5.1 và PowerShell 7. Hướng dẫn: docs/kiem-tra-laptop.md.
File phải lưu dạng UTF-8 có BOM: thiếu BOM, Windows PowerShell 5.1 đọc sai chữ tiếng Việt.

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File infra\check\check-laptop.ps1 -Node laptop-2

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File infra\check\check-laptop.ps1 -Node laptop-2 -MeasurePower
#>
[CmdletBinding()]
param(
    # Tên node trong cụm: laptop-1, laptop-2 hoặc laptop-3.
    [string]$Node = 'laptop-x',
    # Đo điện qua pin (phải rút sạc). Mất khoảng 30 giây + IdleSeconds + LoadSeconds.
    [switch]$MeasurePower,
    [ValidateRange(30, 1800)][int]$IdleSeconds = 120,
    [ValidateRange(30, 1800)][int]$LoadSeconds = 120,
    # Nơi lưu báo cáo. Mặc định: %TEMP%\minicloud-kiem-tra-<node>.md
    [string]$OutFile
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
$inv = [Globalization.CultureInfo]::InvariantCulture
if (-not $OutFile) {
    $OutFile = Join-Path $env:TEMP ('minicloud-kiem-tra-{0}.md' -f ($Node -replace '[^\w.-]', '_'))
}
$today = Get-Date -Format 'yyyy-MM-dd'

# ---------------------------------------------------------------- Hàm phụ

function Invoke-Safely([scriptblock]$Block) {
    # Chạy một lệnh đọc thông tin; lỗi thì bỏ qua để các mục khác vẫn được kiểm tra.
    $ErrorActionPreference = 'Stop'
    try { & $Block } catch { }
}

function Format-Number([double]$Value, [string]$Format = '0.#') {
    return $Value.ToString($Format, $script:inv)
}

function Format-Size([double]$Bytes) {
    # Dung lượng ổ theo cách hãng ghi trên hộp (1 TB = 10^12 byte).
    if ($Bytes -ge 0.95e12) { return (Format-Number ($Bytes / 1e12)) + ' TB' }
    return (Format-Number ($Bytes / 1e9) '0') + ' GB'
}

function Format-VolumeSize([double]$Bytes) {
    # Dung lượng phân vùng theo cách File Explorer hiện (1 GB = 2^30 byte).
    return (Format-Number ($Bytes / 1GB) '0') + ' GB'
}

function Get-StatusText([string]$Status) {
    switch ($Status) {
        'Up' { return 'đang nối' }
        'Disconnected' { return 'chưa nối' }
        'Disabled' { return 'đang tắt trong Windows' }
        default { return $Status }
    }
}

function Format-Speed([double]$Mbps) {
    if ($Mbps -ge 1000) { return (Format-Number ($Mbps / 1000)) + ' Gbps' }
    return (Format-Number $Mbps '0') + ' Mbps'
}

function Format-Watts($Value) {
    if ($null -eq $Value) { return '?' }
    return Format-Number $Value '0.0'
}

function Format-Mac([string]$Mac) { return ($Mac -replace '-', ':').ToUpperInvariant() }

function Test-RandomMac([string]$Mac) {
    # Bit thứ hai của byte đầu bật nghĩa là MAC tự đặt, như MAC ngẫu nhiên Windows dùng cho Wi-Fi.
    $hex = $Mac -replace '[-:]', ''
    if ($hex.Length -lt 2) { return $false }
    return [bool]([Convert]::ToInt32($hex.Substring(0, 2), 16) -band 2)
}

function Get-Average($Values) {
    if ($null -eq $Values -or $Values.Count -eq 0) { return $null }
    return ($Values | Measure-Object -Average).Average
}

function Get-Maximum($Values) {
    if ($null -eq $Values -or $Values.Count -eq 0) { return $null }
    return ($Values | Measure-Object -Maximum).Maximum
}

function Get-NicMaxSpeed($Adapter) {
    # Tốc độ cao nhất card hỗ trợ, đọc từ các lựa chọn "Speed & Duplex" của driver.
    $best = 0.0
    $prop = Invoke-Safely { Get-NetAdapterAdvancedProperty -Name $Adapter.Name -RegistryKeyword '*SpeedDuplex' }
    foreach ($value in @($prop.ValidDisplayValues)) {
        if ("$value" -match '([\d.]+)\s*(G|M)bps') {
            $mbps = [double]::Parse($Matches[1], $script:inv)
            if ($Matches[2] -eq 'G') { $mbps *= 1000 }
            if ($mbps -gt $best) { $best = $mbps }
        }
    }
    if ($best -le 0 -and "$($Adapter.InterfaceDescription)" -match '2\.5\s*G') { $best = 2500 }
    elseif ($best -le 0 -and "$($Adapter.InterfaceDescription)" -match 'GbE|Gigabit') { $best = 1000 }
    return $best
}

function Get-Ipv4Info([int]$Index) {
    $address = Invoke-Safely { Get-NetIPAddress -InterfaceIndex $Index -AddressFamily IPv4 } |
        Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
    if (-not $address) { return $null }
    $dhcp = Invoke-Safely { (Get-NetIPInterface -InterfaceIndex $Index -AddressFamily IPv4).Dhcp }
    $route = Invoke-Safely { Get-NetRoute -InterfaceIndex $Index -DestinationPrefix '0.0.0.0/0' } |
        Sort-Object RouteMetric | Select-Object -First 1
    return [pscustomobject]@{
        Address = $address.IPAddress
        Prefix  = $address.PrefixLength
        Dhcp    = ("$dhcp" -eq 'Enabled')
        Gateway = $route.NextHop
    }
}

function Get-BitLockerText($Value) {
    if ($null -eq $Value -or "$Value" -eq '') { return 'không rõ' }
    switch ([int]$Value) {
        1 { return 'bật' }
        3 { return 'đang mã hoá' }
        4 { return 'đang giải mã' }
        5 { return 'tạm dừng' }
        6 { return 'bật, đang khoá' }
        8 { return 'chờ kích hoạt' }
        default { return 'tắt' }
    }
}

function Find-RaplCounter {
    # Windows đọc RAPL (điện của gói CPU) qua bộ đếm "Energy Meter" trên nhiều máy Intel đời mới.
    # Tên bộ đếm có thể bị dịch theo ngôn ngữ Windows, nên tìm theo tên instance RAPL_Package0_PKG.
    $set = Invoke-Safely { Get-Counter -ListSet 'Energy Meter' }
    if (-not $set) {
        $set = Invoke-Safely { Get-Counter -ListSet * -ErrorAction SilentlyContinue } |
            Where-Object { "$($_.PathsWithInstances)" -match 'RAPL_Package0_PKG' } | Select-Object -First 1
    }
    $paths = @($set.PathsWithInstances | Where-Object { $_ -match 'RAPL_Package0_PKG' })
    if ($paths.Count -eq 0) { return $null }
    $power = @($paths | Where-Object { $_ -match '\\Power$' })
    if ($power.Count -gt 0) { return $power[0] }
    return $paths[0]
}

function Read-RaplWatts([string]$Path) {
    if (-not $Path) { return $null }
    $value = Invoke-Safely { (Get-Counter -Counter $Path).CounterSamples[0].CookedValue }
    if ($null -eq $value) { return $null }
    return [double]$value / 1000.0   # bộ đếm tính bằng mW
}

function Initialize-Native {
    # Vài hàm Windows mà PowerShell không có sẵn. Máy nào chặn Add-Type thì script vẫn chạy,
    # chỉ thiếu phần ngủ/thức, mức dùng CPU và phần đo điện.
    if ('MinicloudNative' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;

public static class MinicloudNative
{
    [DllImport("powrprof.dll")]
    [return: MarshalAs(UnmanagedType.U1)]
    private static extern bool GetPwrCapabilities([Out] byte[] capabilities);

    [DllImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetSystemTimes(out long idle, out long kernel, out long user);

    public static volatile int Sink;

    // SYSTEM_POWER_CAPABILITIES (76 byte): byte 5 = SystemS3, byte 6 = SystemS4,
    // byte 20 = AoAc (Modern Standby), byte 23 = AoAcConnectivitySupported.
    public static byte[] PowerCapabilities()
    {
        var buffer = new byte[76];
        return GetPwrCapabilities(buffer) ? buffer : null;
    }

    // { thời gian CPU nhàn, tổng thời gian CPU } tính bằng 100 ns; kernel time đã gồm thời gian nhàn.
    public static long[] CpuTimes()
    {
        long idle, kernel, user;
        return GetSystemTimes(out idle, out kernel, out user) ? new long[] { idle, kernel + user } : null;
    }

    // Chạy hết mọi luồng CPU bằng phép tính số thực trong 'seconds' giây, rồi tự dừng.
    public static int BurnCpu(int seconds)
    {
        long until = Stopwatch.GetTimestamp() + (long)seconds * Stopwatch.Frequency;
        int count = Environment.ProcessorCount;
        for (int i = 0; i < count; i++)
        {
            var worker = new Thread(() =>
            {
                double x = 1.0;
                while (Stopwatch.GetTimestamp() < until)
                {
                    for (int k = 0; k < 100000; k++) { x = Math.Sqrt(x + k); }
                }
                Sink = (int)x;
            });
            worker.IsBackground = true;
            worker.Priority = ThreadPriority.BelowNormal;
            worker.Start();
        }
        return count;
    }
}
'@
}

function Read-CpuTimes {
    if (-not $script:nativeReady) { return $null }
    return Invoke-Safely { , [MinicloudNative]::CpuTimes() }
}

function Get-CpuPercent($Before, $After) {
    # Phần trăm thời gian CPU bận giữa hai lần đọc Read-CpuTimes.
    if (-not $Before -or -not $After) { return $null }
    $total = [double]($After[1] - $Before[1])
    if ($total -le 0) { return $null }
    return 100.0 * (1 - [double]($After[0] - $Before[0]) / $total)
}

function Read-BatteryStatus {
    $status = @(Invoke-Safely { Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus })
    if ($status.Count -eq 0) { return $null }
    return [pscustomobject]@{
        Online      = [bool]($status | Where-Object { $_.PowerOnline } | Select-Object -First 1)
        RateW       = [double](($status | Measure-Object -Property DischargeRate -Sum).Sum) / 1000.0
        RemainingWh = [double](($status | Measure-Object -Property RemainingCapacity -Sum).Sum) / 1000.0
    }
}

function Read-ChargePercent {
    $batteries = @(Invoke-Safely { Get-CimInstance Win32_Battery })
    if ($batteries.Count -eq 0) { return $null }
    return [int](($batteries | Measure-Object -Property EstimatedChargeRemaining -Average).Average)
}

function Get-BatteryCapacity {
    # Dung lượng thiết kế và dung lượng khi sạc đầy hiện nay, tính bằng Wh.
    $design = 0.0
    $full = 0.0
    $xmlPath = Join-Path $env:TEMP 'minicloud-battery-report.xml'
    $null = & powercfg.exe /batteryreport /xml /output $xmlPath 2>$null
    if (Test-Path -LiteralPath $xmlPath) {
        $xml = Invoke-Safely { [xml](Get-Content -LiteralPath $xmlPath -Raw) }
        foreach ($battery in @($xml.BatteryReport.Batteries.Battery)) {
            if ($null -eq $battery) { continue }
            $design += [double]$battery.DesignCapacity
            $full += [double]$battery.FullChargeCapacity
        }
        Remove-Item -LiteralPath $xmlPath -ErrorAction SilentlyContinue
    }
    if ($full -le 0) {
        $full = [double]((Invoke-Safely { Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity }) |
                Measure-Object -Property FullChargedCapacity -Sum).Sum
        $design = [double]((Invoke-Safely { Get-CimInstance -Namespace root\wmi -ClassName BatteryStaticData }) |
                Measure-Object -Property DesignedCapacity -Sum).Sum
    }
    return [pscustomobject]@{ DesignWh = $design / 1000.0; FullWh = $full / 1000.0 }
}

function Get-PowerCapabilities {
    if (-not $script:nativeReady) { return $null }
    $bytes = [MinicloudNative]::PowerCapabilities()
    if (-not $bytes) { return $null }
    return [pscustomobject]@{
        S3                  = [bool]$bytes[5]
        S4                  = [bool]$bytes[6]
        ModernStandby       = [bool]$bytes[20]
        StandbyKeepsNetwork = [bool]$bytes[23]
    }
}

function Get-WslDistros {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { return @() }
    $previous = $env:WSL_UTF8
    $env:WSL_UTF8 = '1'   # nếu không, wsl.exe in ra UTF-16
    $lines = & wsl.exe --list --verbose 2>$null
    $code = $LASTEXITCODE
    $env:WSL_UTF8 = $previous
    if ($code -ne 0) { return @() }
    $distros = foreach ($line in @($lines)) {
        if ("$line" -match '^\s*(\*?)\s*(\S+)\s+(\S+)\s+(\d)\s*$') {
            [pscustomobject]@{ Name = $Matches[2]; State = $Matches[3]; Version = $Matches[4] }
        }
    }
    return @($distros)
}

function Get-ToolVersion([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { return $null }
    $line = "$(& $Name --version 2>$null | Select-Object -First 1)"
    if ($line -match '(\d+\.\d+[\w.]*)') { return $Matches[1] }
    return $line.Trim()
}

$report = New-Object System.Collections.Generic.List[string]
function Add-Line([string]$Text = '') { $script:report.Add($Text) }

$nativeReady = $false
try { Initialize-Native; $nativeReady = $true } catch { }

# ---------------------------------------------------------------- Máy, CPU, RAM

$cs = Invoke-Safely { Get-CimInstance Win32_ComputerSystem }
$product = Invoke-Safely { Get-CimInstance Win32_ComputerSystemProduct }
$bios = Invoke-Safely { Get-CimInstance Win32_BIOS }
$os = Invoke-Safely { Get-CimInstance Win32_OperatingSystem }
$cpus = @(Invoke-Safely { Get-CimInstance Win32_Processor })

$vendor = "$($cs.Manufacturer)".Trim()
$vendorShort = switch -Regex ($vendor) {
    'Micro-Star' { 'MSI'; break }
    '^ASUS' { 'ASUS'; break }
    '^Dell' { 'Dell'; break }
    '^HP$|Hewlett' { 'HP'; break }
    '^LENOVO' { 'Lenovo'; break }
    '^Acer' { 'Acer'; break }
    '^GIGABYTE' { 'Gigabyte'; break }
    default { $vendor }
}
$model = "$($cs.Model)".Trim()
$friendly = "$($product.Version)".Trim()
if ($vendorShort -eq 'Lenovo' -and $friendly -and $friendly -notmatch '^(None|Lenovo|To be filled)') {
    $model = "$friendly ($model)"   # Lenovo ghi mã máy ở Model, tên dễ đọc ở Version
}
$modelCell = if (-not $model) { 'không rõ' } elseif ($model -like "$vendorShort *") { $model } else { "$vendorShort $model".Trim() }

$cpuFull = if ($cpus.Count -gt 0) { ("$($cpus[0].Name)" -replace '\s+', ' ').Trim() } else { '' }
$cpuShort = $cpuFull -replace '\((R|TM)\)', '' -replace '^\d+(st|nd|rd|th) Gen\s+', '' -replace '\s+CPU\b', ''
$cpuShort = ($cpuShort -replace '\s+@.*$', '' -replace '\s+with Radeon.*$', '' -replace '\s+\d+-Core Processor$', '' -replace '\s+', ' ').Trim()
$cores = [int](($cpus | Measure-Object -Property NumberOfCores -Sum).Sum)
$threads = [int](($cpus | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
$cpuCell = if ($cpuShort) { "$cpuShort, $cores/$threads" } else { 'không rõ' }

$modules = @(Invoke-Safely { Get-CimInstance Win32_PhysicalMemory })
$ramBytes = [double](($modules | Measure-Object -Property Capacity -Sum).Sum)
if ($ramBytes -le 0 -and $cs) { $ramBytes = [double]$cs.TotalPhysicalMemory }
$ramGB = [int][math]::Round($ramBytes / 1GB)
$memTypeNames = @{ 18 = 'DDR'; 19 = 'DDR2'; 20 = 'DDR2'; 24 = 'DDR3'; 26 = 'DDR4'; 27 = 'LPDDR'; 28 = 'LPDDR2'; 29 = 'LPDDR3'; 30 = 'LPDDR4'; 34 = 'DDR5'; 35 = 'LPDDR5' }
$memType = ''
$memSpeed = 0
if ($modules.Count -gt 0) {
    $memType = "$($memTypeNames[[int]$modules[0].SMBIOSMemoryType])"
    $memSpeed = [int]$modules[0].ConfiguredClockSpeed
    if ($memSpeed -le 0) { $memSpeed = [int]$modules[0].Speed }
}
$ramCell = "$ramGB GB"
if ($memType) { $ramCell += " $memType" }
if ($memType -and $memSpeed -gt 0) { $ramCell += "-$memSpeed" }
$memArrays = @(Invoke-Safely { Get-CimInstance Win32_PhysicalMemoryArray } | Where-Object { $_.Use -eq 3 })
$slots = [int](($memArrays | Measure-Object -Property MemoryDevices -Sum).Sum)
$maxRamGB = [int][math]::Round([double](($memArrays | Measure-Object -Property MaxCapacityEx -Sum).Sum) / 1MB)
$moduleText = ($modules | Group-Object { [int][math]::Round($_.Capacity / 1GB) } |
        ForEach-Object { "$($_.Count) thanh $($_.Name) GB" }) -join ' + '

# ---------------------------------------------------------------- Ổ đĩa

$physicalDisks = @(Invoke-Safely { Get-PhysicalDisk } | Sort-Object { [int]"$($_.DeviceId)" })
$disks = @(Invoke-Safely { Get-Disk })
function Get-DiskRank($Disk) {
    # Liệt kê SSD NVMe trước, rồi SSD khác, rồi HDD.
    if ("$($Disk.BusType)" -eq 'NVMe') { return 0 }
    if ("$($Disk.MediaType)" -eq 'SSD') { return 1 }
    return 2
}
$internalDisks = @($physicalDisks | Where-Object { "$($_.BusType)" -ne 'USB' } |
        Sort-Object @{ Expression = { Get-DiskRank $_ } }, @{ Expression = { [int]"$($_.DeviceId)" } })

function Get-DiskKind($Disk) {
    $media = "$($Disk.MediaType)"
    $bus = "$($Disk.BusType)"
    if ($media -ne 'SSD' -and $media -ne 'HDD') { $media = if ($bus -eq 'NVMe') { 'SSD' } else { 'Ổ' } }
    return "$media $bus"
}

function Get-UnallocatedBytes($Disk) {
    $match = $disks | Where-Object { "$($_.Number)" -eq "$($Disk.DeviceId)" } | Select-Object -First 1
    if (-not $match) { return 0.0 }
    return [double]$match.Size - [double]$match.AllocatedSize
}

$diskCell = ($internalDisks | ForEach-Object { "$(Get-DiskKind $_) $(Format-Size $_.Size)" }) -join ' + '
if (-not $diskCell) { $diskCell = 'không rõ' }
$unallocatedBytes = [double](($internalDisks | ForEach-Object { Get-UnallocatedBytes $_ } | Measure-Object -Sum).Sum)

# Get-Partition trả về rỗng khi không có quyền admin, nên đi từ ổ logic (C:, D:…) sang ổ vật lý.
$shell = Invoke-Safely { New-Object -ComObject Shell.Application }
$volumes = foreach ($logical in @(Invoke-Safely { Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' })) {
    $letter = "$($logical.DeviceID)".TrimEnd(':').ToUpperInvariant()
    $partition = Invoke-Safely { Get-CimAssociatedInstance -InputObject $logical -ResultClassName Win32_DiskPartition } |
        Select-Object -First 1
    $drive = $null
    if ($partition) {
        $drive = Invoke-Safely { Get-CimAssociatedInstance -InputObject $partition -ResultClassName Win32_DiskDrive } |
            Select-Object -First 1
    }
    $bitlocker = $null
    if ($shell) {
        $bitlocker = Invoke-Safely { $shell.NameSpace("$($letter):").Self.ExtendedProperty('System.Volume.BitLockerProtection') }
    }
    [pscustomobject]@{
        Letter    = $letter
        Disk      = if ($drive) { "$($drive.Index)" } else { '' }
        Label     = "$($logical.VolumeName)"
        Size      = [double]$logical.Size
        Free      = [double]$logical.FreeSpace
        BitLocker = $bitlocker
    }
}
$volumes = @($volumes | Sort-Object Letter)
$encrypted = @($volumes | Where-Object { @(1, 3, 5, 6, 8) -contains [int]"$($_.BitLocker)" })
$roomiest = $volumes | Sort-Object Free -Descending | Select-Object -First 1
$raidDisks = @($physicalDisks | Where-Object { "$($_.BusType)" -eq 'RAID' })
$vmdControllers = @(Invoke-Safely { Get-CimInstance Win32_SCSIController } | Where-Object { "$($_.Name)" -match 'VMD|RST|Rapid Storage' })

# ---------------------------------------------------------------- Card đồ hoạ

$gpus = @(Invoke-Safely { Get-CimInstance Win32_VideoController } |
        Where-Object { "$($_.Name)" -notmatch 'Microsoft|Remote|Virtual|Parsec|Meta|DisplayLink|Mirage|Citrix' })
$dgpus = @($gpus | Where-Object { "$($_.Name)" -match 'NVIDIA|Radeon RX|Radeon Pro|Arc\S*\s+A\d' })

# ---------------------------------------------------------------- Mạng

$adapters = @(Invoke-Safely { Get-NetAdapter -Physical })
$ethernet = @($adapters |
        Where-Object { "$($_.PhysicalMediaType)" -eq '802.3' -and "$($_.InterfaceDescription)" -notmatch 'Remote NDIS|Bluetooth' } |
        Sort-Object @{ Expression = { if ("$($_.PnPDeviceID)" -like 'PCI*') { 0 } else { 1 } } }, Name)
$wifi = @($adapters | Where-Object { "$($_.PhysicalMediaType)" -match '802\.11' })
$lan = if ($ethernet.Count -gt 0) { $ethernet[0] } else { $null }

$ipInfo = $null
$ipAdapter = $null
foreach ($adapter in $ethernet) {
    if ("$($adapter.Status)" -ne 'Up') { continue }
    $ipInfo = Get-Ipv4Info $adapter.ifIndex
    if ($ipInfo) { $ipAdapter = $adapter; break }
}
if (-not $ipInfo) {
    $route = Invoke-Safely { Get-NetRoute -DestinationPrefix '0.0.0.0/0' } |
        Where-Object { @($adapters.ifIndex) -contains $_.InterfaceIndex } | Sort-Object RouteMetric | Select-Object -First 1
    if ($route) {
        $ipAdapter = $adapters | Where-Object { $_.ifIndex -eq $route.InterfaceIndex } | Select-Object -First 1
        $ipInfo = Get-Ipv4Info $route.InterfaceIndex
    }
}

$lanMax = 0.0
$lanNow = 0.0
$lanWol = @()
if ($lan) {
    $lanMax = Get-NicMaxSpeed $lan
    if ("$($lan.Status)" -eq 'Up') { $lanNow = [double]$lan.Speed / 1e6 }
    $lanCell = if ("$($lan.PnPDeviceID)" -like 'USB*') { 'USB-LAN' } else { 'Có' }
    if ($lanMax -gt 0) { $lanCell += ', ' + (Format-Speed $lanMax) }
    $lanCell += switch ("$($lan.Status)") {
        'Up' { " (đang nối $(Format-Speed $lanNow))" }
        'Disabled' { ' (đang tắt trong Windows)' }
        default { ' (chưa cắm dây)' }
    }
    $macCell = Format-Mac "$($lan.MacAddress)"
    # Get-NetAdapterPowerManagement cần quyền admin; thuộc tính của driver thì ai cũng đọc được.
    # *WakeOnMagicPacket và *ModernStandbyWoLMagicPacket là tên chuẩn; WoL khi tắt máy thì mỗi hãng đặt tên một kiểu.
    $lanWol = @(Invoke-Safely { Get-NetAdapterAdvancedProperty -Name $lan.Name } |
            Where-Object {
                @('*WakeOnMagicPacket', '*ModernStandbyWoLMagicPacket') -contains "$($_.RegistryKeyword)" -or
                "$($_.DisplayName)" -match 'Shutdown Wake|power off|from S5'
            } |
            ForEach-Object { "$($_.DisplayName): $($_.DisplayValue)" })
} else {
    $lanCell = 'Không'
    $macCell = '—'
}

$ipCell = '—'
if ($ipInfo) {
    $how = if ($ipInfo.Dhcp) { 'DHCP' } else { 'IP tĩnh' }
    if ($ipAdapter -and "$($ipAdapter.PhysicalMediaType)" -match '802\.11') { $how = "Wi-Fi, $how" }
    $ipCell = "$($ipInfo.Address) ($how)"
}

# ---------------------------------------------------------------- Ảo hoá, RAPL, pin, ngủ

$hypervisor = [bool]$cs.HypervisorPresent
$virtFirmware = [bool]($cpus | Where-Object { $_.VirtualizationFirmwareEnabled } | Select-Object -First 1)
$virtCpu = [bool]($cpus | Where-Object { $_.VMMonitorModeExtensions } | Select-Object -First 1)
# Khi Hyper-V hoặc WSL2 đang chạy, Windows báo VirtualizationFirmwareEnabled = False dù đã bật.
$virtCell = if ($hypervisor -or $virtFirmware) { 'Có, đã bật' } elseif ($virtCpu) { 'Có, chưa bật trong BIOS' } else { 'Không thấy' }

$raplPath = Find-RaplCounter
$raplNow = $null
$cpuBefore = Read-CpuTimes
if ($raplPath) {
    $readings = @(1..3 | ForEach-Object { Read-RaplWatts $raplPath } | Where-Object { $null -ne $_ })
    $raplNow = Get-Average $readings
}
$cpuNow = Get-CpuPercent $cpuBefore (Read-CpuTimes)
$raplCell = if ($raplPath) { 'Có' } else { 'Chưa thấy trên Windows' }

$hasBattery = $null -ne (Read-ChargePercent)
$capacity = if ($hasBattery) { Get-BatteryCapacity } else { $null }
$batteryHealth = $null
if ($capacity -and $capacity.DesignWh -gt 0 -and $capacity.FullWh -gt 0) {
    $batteryHealth = [int][math]::Round(100 * $capacity.FullWh / $capacity.DesignWh)
}
$batteryNow = Read-BatteryStatus
$chargeNow = Read-ChargePercent

$powerCaps = Invoke-Safely { Get-PowerCapabilities }
$sleepText = 'không rõ'
if ($powerCaps) {
    if ($powerCaps.ModernStandby) {
        $sleepText = 'Modern Standby (S0ix)'
        if ($powerCaps.StandbyKeepsNetwork) { $sleepText += ', giữ mạng khi ngủ' }
        $sleepText += '; Windows không dùng S3'
    } elseif ($powerCaps.S3) {
        $sleepText = 'có S3 (ngủ kiểu truyền thống)'
    } else {
        $sleepText = 'không có S3 lẫn Modern Standby'
    }
}

$firmware = "$env:firmware_type"
$secureBoot = Invoke-Safely { (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State').UEFISecureBootEnabled }
$secureBootText = if ($null -eq $secureBoot) { 'không rõ' } elseif ([int]$secureBoot -eq 1) { 'bật' } else { 'tắt' }

$planLine = "$(& powercfg.exe /getactivescheme 2>$null)"
$plan = if ($planLine -match '\(([^)]+)\)\s*$') { $Matches[1] } else { 'không rõ' }
$wslDistros = @(Get-WslDistros)
$tools = [ordered]@{
    git    = Get-ToolVersion 'git'
    uv     = Get-ToolVersion 'uv'
    winget = Get-ToolVersion 'winget'
    docker = Get-ToolVersion 'docker'
}

# ---------------------------------------------------------------- Cần chú ý

$warnings = New-Object System.Collections.Generic.List[string]
if ($Node -eq 'laptop-x') {
    $warnings.Add('Chưa đặt tên node: chạy lại với -Node laptop-2 hoặc -Node laptop-3, tuỳ trưởng nhóm phân.')
}
if (-not $lan) {
    $warnings.Add('Không có cổng LAN: cần adapter USB-LAN để vào cụm (Incus không bridge được qua Wi-Fi) và để dùng Wake-on-LAN.')
} elseif ($lanNow -gt 0 -and $lanMax -gt $lanNow) {
    $warnings.Add("Cổng LAN chỉ nối ở $(Format-Speed $lanNow) dù card chạy được $(Format-Speed $lanMax): thử dây Cat5e/Cat6 khác hoặc cổng khác của router/switch.")
}
if (-not ($hypervisor -or $virtFirmware)) {
    $warnings.Add('Virtualization chưa bật: chủ máy vào BIOS bật Intel VT-x hoặc AMD SVM. Thiếu nó thì không chạy được WSL2 lẫn VM.')
}
if ($ramGB -gt 0 -and $ramGB -lt 8) {
    $warnings.Add("RAM chỉ $ramGB GB: node chỉ chạy được vài container nhỏ.")
}
if ($encrypted.Count -gt 0) {
    $letters = ($encrypted | ForEach-Object { "$($_.Letter):" }) -join ', '
    $warnings.Add("BitLocker hoặc Device Encryption đang dùng ở $($letters): lưu khoá khôi phục (https://aka.ms/myrecoverykey) trước khi đổi cài đặt BIOS hoặc cài Ubuntu song song.")
}
if ($raidDisks.Count -gt 0 -or $vmdControllers.Count -gt 0) {
    $warnings.Add('Ổ cứng chạy qua Intel RST/VMD: trình cài Ubuntu có thể không thấy ổ. Muốn cài Ubuntu thì phải tắt VMD trong BIOS, và phải chuẩn bị Windows trước để Windows vẫn khởi động được.')
}
if ($powerCaps -and $powerCaps.ModernStandby) {
    $warnings.Add('Windows chỉ dùng Modern Standby (S0ix), không dùng S3: suspend trên Ubuntu có thể tốn điện hơn và không giữ được Wake-on-LAN. Sau khi cài Ubuntu, vai trò 6 xem `cat /sys/power/mem_sleep` có `deep` không.')
}
foreach ($gpu in $dgpus) {
    $warnings.Add("Có card đồ hoạ rời ($($gpu.Name)): trên Ubuntu Server cần kiểm tra card rời tự tắt khi nhàn, nếu không P_idle sẽ cao.")
}
if (-not $hasBattery) {
    $warnings.Add('Không thấy pin: không đo được điện khi rút sạc, cần ổ cắm đo điện.')
} elseif ($batteryHealth -and $batteryHealth -lt 70) {
    $warnings.Add("Pin chỉ còn $batteryHealth% dung lượng thiết kế: khi đo điện lúc rút sạc, pin tụt nhanh.")
}

# ---------------------------------------------------------------- Báo cáo

$osText = "$($os.Caption)".Trim()
if ($os) { $osText += " ($($os.Version))" }
$row = @($Node, $modelCell, $cpuCell, $ramCell, $diskCell, $lanCell, $macCell, $ipCell, $virtCell, $raplCell) |
    ForEach-Object { ("$_" -replace '\|', '/').Trim() }

Add-Line "# Kiểm tra $($Node): $modelCell"
Add-Line
Add-Line "Chạy lúc $(Get-Date -Format 'yyyy-MM-dd HH:mm') trên $osText, bằng infra/check/check-laptop.ps1."
Add-Line
Add-Line '## Dòng cho bảng phần cứng'
Add-Line
Add-Line "Thay dòng ``$Node`` trong bảng đầu của docs/hardware.md bằng dòng này:"
Add-Line
Add-Line ('| ' + ($row -join ' | ') + ' |')
Add-Line
Add-Line '## Ghi chú cho docs/hardware.md'
Add-Line
Add-Line "Chép vào mục ``### $Node`` trong phần ""Ghi chú từng máy"":"
Add-Line

$ramNote = "- RAM: $(if ($moduleText) { $moduleText } else { "$ramGB GB" })"
if ($slots -gt 0) { $ramNote += ", dùng $($modules.Count)/$slots khe" }
if ($maxRamGB -gt 0) { $ramNote += ", tối đa $maxRamGB GB" }
if ($memType -like 'LPDDR*') { $ramNote += ', RAM hàn trên mainboard nên không nâng được' }
Add-Line "$ramNote."

$diskNames = ($internalDisks | ForEach-Object { "$($_.FriendlyName) ($(Get-DiskKind $_))" }) -join ', '
$diskNote = "- Ổ đĩa: $diskNames"
if ($unallocatedBytes -ge 1GB) { $diskNote += "; còn $(Format-VolumeSize $unallocatedBytes) chưa phân vùng" } else { $diskNote += '; không còn vùng chưa phân vùng' }
if ($roomiest) { $diskNote += "; phân vùng trống nhiều nhất là $($roomiest.Letter): (trống $(Format-VolumeSize $roomiest.Free))" }
Add-Line "$diskNote."

if ($gpus.Count -gt 0) {
    $gpuNames = ($gpus | ForEach-Object { if ($dgpus -contains $_) { "$($_.Name) (card rời)" } else { "$($_.Name)" } }) -join ', '
    Add-Line "- Card đồ hoạ: $gpuNames."
}
$netNote = '- Mạng: '
if ($lan) { $netNote += "$($lan.InterfaceDescription)" } else { $netNote += 'không có cổng LAN' }
if ($wifi.Count -gt 0) { $netNote += "; Wi-Fi $($wifi[0].InterfaceDescription)" }
Add-Line "$netNote."
if ($lanWol.Count -gt 0) { Add-Line "- Wake-on-LAN trong driver Windows: $($lanWol -join '; ')." }
if ($capacity -and $batteryHealth) {
    Add-Line "- Pin: còn $batteryHealth% dung lượng thiết kế ($(Format-Number $capacity.FullWh '0.0')/$(Format-Number $capacity.DesignWh '0.0') Wh)."
} elseif (-not $hasBattery) {
    Add-Line '- Pin: không có.'
}
Add-Line "- Ngủ: $sleepText."
$bitlockerKnown = @($volumes | Where-Object { $null -ne $_.BitLocker -and "$($_.BitLocker)" -ne '' })
$bitlockerText = if ($encrypted.Count -gt 0) {
    'BitLocker bật ở ' + (($encrypted | ForEach-Object { "$($_.Letter):" }) -join ', ')
} elseif ($bitlockerKnown.Count -gt 0) { 'BitLocker tắt' } else { 'BitLocker không rõ' }
Add-Line "- Firmware $(if ($firmware) { $firmware } else { 'không rõ' }), Secure Boot $secureBootText, $bitlockerText."
Add-Line "- Kiểm tra ngày $today trên $osText bằng ``infra/check/check-laptop.ps1``."
Add-Line
Add-Line '## Cần chú ý'
Add-Line
if ($warnings.Count -eq 0) { Add-Line '- Không có gì đặc biệt.' }
foreach ($warning in $warnings) { Add-Line "- $warning" }
Add-Line
Add-Line '## Chi tiết'
Add-Line
Add-Line "- Máy: $vendor $("$($cs.Model)".Trim()), BIOS $($bios.SMBIOSBIOSVersion)$(if ($bios.ReleaseDate) { ' (' + $bios.ReleaseDate.ToString('yyyy-MM-dd') + ')' })."
Add-Line "- CPU: $cpuFull, $cores nhân, $threads luồng."
Add-Line "- RAM: $ramGB GB$(if ($memType) { " $memType" })$(if ($memSpeed -gt 0) { ", $memSpeed MT/s" }); $(if ($moduleText) { $moduleText } else { 'không đọc được từng thanh' })."
foreach ($disk in @($physicalDisks | Sort-Object @{ Expression = { Get-DiskRank $_ } }, @{ Expression = { [int]"$($_.DeviceId)" } })) {
    $line = "- Ổ $($disk.DeviceId): $($disk.FriendlyName), $(Get-DiskKind $disk), $(Format-Size $disk.Size), sức khoẻ $($disk.HealthStatus)"
    $unallocated = Get-UnallocatedBytes $disk
    if ($unallocated -ge 1GB) { $line += ", chưa phân vùng $(Format-VolumeSize $unallocated)" }
    $onDisk = @($volumes | Where-Object { $_.Disk -eq "$($disk.DeviceId)" } |
            ForEach-Object { "$($_.Letter): $(if ($_.Label) { $_.Label + ' ' })$(Format-VolumeSize $_.Size), trống $(Format-VolumeSize $_.Free), BitLocker $(Get-BitLockerText $_.BitLocker)" })
    if ($onDisk.Count -gt 0) { $line += '. Phân vùng: ' + ($onDisk -join '; ') }
    Add-Line "$line."
}
foreach ($adapter in $adapters) {
    $line = "- Card mạng $($adapter.Name): $($adapter.InterfaceDescription), $(Get-StatusText "$($adapter.Status)")"
    if ("$($adapter.Status)" -eq 'Up') { $line += ", $(Format-Speed ([double]$adapter.Speed / 1e6))" }
    $line += ", MAC $(Format-Mac "$($adapter.MacAddress)")"
    if (Test-RandomMac "$($adapter.MacAddress)") { $line += ' (MAC ngẫu nhiên, không dùng được cho Wake-on-LAN)' }
    Add-Line "$line."
}
if ($ipInfo) {
    Add-Line "- IP: $($ipInfo.Address)/$($ipInfo.Prefix) qua $($ipAdapter.Name), gateway $($ipInfo.Gateway), $(if ($ipInfo.Dhcp) { 'DHCP' } else { 'IP tĩnh' })."
}
Add-Line "- Ảo hoá: hypervisor đang chạy: $(if ($hypervisor) { 'có' } else { 'không' }); VT-x/AMD-V trong CPU: $(if ($virtCpu -or $hypervisor) { 'có' } else { 'không thấy' })."
if ($raplPath) {
    Add-Line "- RAPL: đọc qua bộ đếm ``$raplPath``; gói CPU $(Format-Watts $raplNow) W lúc kiểm tra (CPU dùng $(if ($null -ne $cpuNow) { Format-Number $cpuNow '0' } else { '?' })%)."
}
if ($hasBattery) {
    $source = if ($batteryNow -and $batteryNow.Online) { 'đang cắm sạc' } else { 'đang chạy pin' }
    Add-Line "- Pin: $source, $chargeNow%."
}
Add-Line "- Gói điện Windows: $plan."
if ($wslDistros.Count -gt 0) {
    Add-Line "- WSL: $(($wslDistros | ForEach-Object { "$($_.Name) (WSL$($_.Version), $($_.State))" }) -join ', ')."
} else {
    Add-Line '- WSL: chưa có distro nào.'
}
$toolText = ($tools.Keys | ForEach-Object { if ($tools[$_]) { "$_ $($tools[$_])" } else { "$_ chưa cài" } }) -join ', '
Add-Line "- Công cụ: $toolText."

$text = ($report -join "`n") + "`n"
Write-Output $text
[IO.File]::WriteAllText($OutFile, $text, (New-Object System.Text.UTF8Encoding $false))
Write-Output "Đã lưu báo cáo vào $OutFile"

if (-not $MeasurePower) { exit 0 }

# ---------------------------------------------------------------- Đo điện qua pin

$status = Read-BatteryStatus
if (-not $hasBattery -or -not $status) {
    Write-Output 'Không đo được: máy không có pin, hoặc Windows không đọc được pin.'
    exit 2
}
if ($status.Online) {
    Write-Output 'Chưa đo điện: máy đang cắm sạc. Rút sạc rồi chạy lại lệnh với -MeasurePower.'
    exit 2
}
$startPercent = Read-ChargePercent
if ($null -ne $startPercent -and $startPercent -lt 40) {
    Write-Output "Pin còn $startPercent%: sạc lên trên 40% rồi đo, để Windows không bật tiết kiệm pin giữa chừng."
    exit 2
}

if (-not $nativeReady) {
    Write-Output 'Không đo được: PowerShell trên máy này chặn Add-Type, nên script không tạo được tải CPU.'
    exit 2
}

function Measure-Phase([string]$Label, [int]$Seconds, [int]$SkipSeconds) {
    # Đọc pin và RAPL mỗi 5 giây, bỏ qua $SkipSeconds giây đầu; mức dùng CPU tính cho cả quãng đo.
    $battery = New-Object System.Collections.Generic.List[double]
    $rapl = New-Object System.Collections.Generic.List[double]
    $startWh = $null
    $startAt = 0.0
    $cpuStart = $null
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $tick = 0
    while ($clock.Elapsed.TotalSeconds -lt $Seconds) {
        $now = Read-BatteryStatus
        if ($now -and $now.Online) { throw 'máy vừa được cắm sạc nên số đo không còn đúng' }
        $elapsed = $clock.Elapsed.TotalSeconds
        if ($elapsed -ge $SkipSeconds -and $now) {
            if ($null -eq $startWh) { $startWh = $now.RemainingWh; $startAt = $elapsed; $cpuStart = Read-CpuTimes }
            if ($now.RateW -gt 0) { $battery.Add($now.RateW) }
            $watts = Read-RaplWatts $script:raplPath
            if ($null -ne $watts) { $rapl.Add($watts) }
        }
        if ($tick % 6 -eq 0) { Write-Host ('  {0}: {1:0} s / {2} s' -f $Label, $elapsed, $Seconds) }
        $tick++
        $wait = $tick * 5 - $clock.Elapsed.TotalSeconds
        if ($wait -gt 0) { Start-Sleep -Milliseconds ([int]($wait * 1000)) }
    }
    $end = Read-BatteryStatus
    $cpuPercent = Get-CpuPercent $cpuStart (Read-CpuTimes)
    $energyW = $null
    if ($null -ne $startWh -and $end -and $end.RemainingWh -lt $startWh) {
        $energyW = ($startWh - $end.RemainingWh) * 3600 / ($clock.Elapsed.TotalSeconds - $startAt)
    }
    return [pscustomobject]@{
        BatteryW     = Get-Average $battery
        BatteryPeakW = Get-Maximum $battery
        EnergyW      = $energyW
        RaplW        = Get-Average $rapl
        RaplPeakW    = Get-Maximum $rapl
        CpuPercent   = $cpuPercent
        Samples      = $battery.Count
    }
}

$warmUp = [math]::Min(30, [int]($LoadSeconds / 4))
$brightness = Invoke-Safely { (Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorBrightness | Select-Object -First 1).CurrentBrightness }
Write-Host "Đo điện trên $($Node): chờ 30 s cho máy ổn định, đo $IdleSeconds s lúc nhàn, rồi $LoadSeconds s chạy hết $threads luồng CPU."
Write-Host 'Đừng dùng máy và đừng cắm sạc cho tới khi xong.'
try {
    $null = Measure-Phase -Label 'ổn định' -Seconds 30 -SkipSeconds 30
    $idle = Measure-Phase -Label 'nhàn' -Seconds $IdleSeconds -SkipSeconds 0
    $workers = [MinicloudNative]::BurnCpu($LoadSeconds)
    $load = Measure-Phase -Label "chạy hết $workers luồng" -Seconds $LoadSeconds -SkipSeconds $warmUp
} catch {
    Write-Output "Dừng đo: $($_.Exception.Message). Rút sạc rồi chạy lại."
    exit 2
}
$endPercent = Read-ChargePercent

function Get-SystemWatts($Phase) {
    # Ưu tiên tốc độ xả pin; máy nào không báo thì tính từ lượng pin tụt.
    if ($null -ne $Phase.BatteryW) { return $Phase.BatteryW }
    return $Phase.EnergyW
}

$measure = New-Object System.Collections.Generic.List[string]
$measure.Add('')
$measure.Add('## Đo điện khi rút sạc')
$measure.Add('')
$measure.Add('Dòng cho bảng số đo điện năng. Script chỉ đo được hai cột rút sạc; ô nào đã có số thì giữ nguyên:')
$measure.Add('')
$measure.Add("| $Node |  |  | $(Format-Watts (Get-SystemWatts $idle)) | $(Format-Watts (Get-SystemWatts $load)) |  |  |")
$measure.Add('')
$measure.Add("- Lúc nhàn ($IdleSeconds s, $($idle.Samples) lần đọc): pin $(Format-Watts $idle.BatteryW) W; tính theo lượng pin tụt $(Format-Watts $idle.EnergyW) W; gói CPU (RAPL) $(Format-Watts $idle.RaplW) W; CPU dùng $(if ($null -ne $idle.CpuPercent) { Format-Number $idle.CpuPercent '0' } else { '?' })%.")
$measure.Add("- Chạy hết $threads luồng ($LoadSeconds s, bỏ $warmUp s đầu): pin $(Format-Watts $load.BatteryW) W, cao nhất $(Format-Watts $load.BatteryPeakW) W; tính theo lượng pin tụt $(Format-Watts $load.EnergyW) W; gói CPU $(Format-Watts $load.RaplW) W, cao nhất $(Format-Watts $load.RaplPeakW) W; CPU dùng $(if ($null -ne $load.CpuPercent) { Format-Number $load.CpuPercent '0' } else { '?' })%.")
$measure.Add("- Điều kiện: $osText, màn hình bật$(if ($null -ne $brightness) { " (độ sáng $brightness%)" }), gói điện $plan, pin $startPercent% → $endPercent%.")
if ($null -ne $load.CpuPercent -and $load.CpuPercent -lt 90) {
    $measure.Add("- Cần chú ý: CPU chỉ dùng $(Format-Number $load.CpuPercent '0')% lúc chạy tải, nên P_max có thể thấp hơn thật.")
}
if ($null -eq (Get-SystemWatts $idle)) {
    $measure.Add('- Cần chú ý: Windows không báo tốc độ xả pin trên máy này; cần ổ cắm đo điện.')
}
$measureText = ($measure -join "`n") + "`n"
Write-Output $measureText
[IO.File]::AppendAllText($OutFile, $measureText, (New-Object System.Text.UTF8Encoding $false))
Write-Output "Đã thêm số đo vào $OutFile"
exit 0
