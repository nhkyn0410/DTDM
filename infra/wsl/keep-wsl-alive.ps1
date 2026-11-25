# Keep the WSL distro (and Incus, minicloud-api, Traefik, cloudflared inside it) running while
# this Windows PC acts as a temporary laptop-1. WSL otherwise shuts down when no window uses it.
#   Start:  powershell -ExecutionPolicy Bypass -File infra\wsl\keep-wsl-alive.ps1
#   Stop:   wsl --terminate Ubuntu
param([string]$Distro = "Ubuntu")

$running = Get-CimInstance Win32_Process -Filter "Name = 'wsl.exe'" |
    Where-Object { $_.CommandLine -like "*sleep infinity*" }
if ($running) {
    Write-Host "Keep-alive is already running for WSL."
    exit 0
}
Start-Process -WindowStyle Hidden -FilePath "wsl.exe" `
    -ArgumentList @("-d", $Distro, "-u", "root", "--exec", "sleep", "infinity")
Write-Host "WSL $Distro now stays up until: wsl --terminate $Distro"
Write-Host "The PC must stay awake: Windows sleep also pauses WSL."
