# ตั้ง named tunnel ให้ ClipQueue — ทำครั้งเดียว
#
#   .\setup-tunnel.ps1 -Hostname clip.โดเมนคุณ.com
#   .\setup-tunnel.ps1 -Hostname clip.โดเมนคุณ.com -Name clipqueue
#
# ต้อง cloudflared tunnel login ให้ผ่านก่อน เพราะขั้นนั้นเปิดเบราว์เซอร์ให้
# เลือกโดเมนด้วยบัญชีของคุณ สคริปต์ทำแทนไม่ได้
#
# สคริปต์นี้ทำสามอย่างที่เหลือ: สร้าง tunnel, ผูก DNS, เขียน config.yml
# แล้วตั้ง CLIPQUEUE_BASE_URL ใน .env ให้ตรงกับโฮสต์ที่ผูกไว้

param(
    [Parameter(Mandatory = $true)][string]$Hostname,
    [string]$Name = "clipqueue",
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "ยังไม่ได้ติดตั้ง cloudflared — winget install Cloudflare.cloudflared" -ForegroundColor Red
    exit 1
}

$cfDir = Join-Path $env:USERPROFILE ".cloudflared"
$cert  = Join-Path $cfDir "cert.pem"

# cert.pem คือหลักฐานว่าล็อกอินแล้วและเลือกโดเมนไว้ ไม่มีไฟล์นี้ขั้นต่อไปพังหมด
if (-not (Test-Path $cert)) {
    Write-Host ""
    Write-Host "ยังไม่ได้ล็อกอิน Cloudflare" -ForegroundColor Yellow
    Write-Host "รันคำสั่งนี้ก่อน แล้วเลือกโดเมนในเบราว์เซอร์ที่เปิดขึ้นมา:"
    Write-Host ""
    Write-Host "  cloudflared tunnel login" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "ถ้ายังไม่มีโดเมนในบัญชี Cloudflare ต้องเพิ่มโดเมนที่ dash.cloudflare.com ก่อน"
    exit 1
}

# สร้าง tunnel — มีอยู่แล้วก็ใช้ตัวเดิม ไม่ต้องสร้างซ้ำ
$existing = (cloudflared tunnel list 2>&1 | Select-String "\s$([regex]::Escape($Name))\s")
if ($existing) {
    Write-Host "มี tunnel '$Name' อยู่แล้ว ใช้ตัวเดิม" -ForegroundColor Yellow
} else {
    Write-Host "สร้าง tunnel '$Name'…" -ForegroundColor Cyan
    cloudflared tunnel create $Name
    if ($LASTEXITCODE -ne 0) { Write-Host "สร้าง tunnel ไม่สำเร็จ" -ForegroundColor Red; exit 1 }
}

# หา UUID จากชื่อ เพื่อชี้ credentials-file ให้ถูกไฟล์
$row = cloudflared tunnel list 2>&1 | Select-String "\s$([regex]::Escape($Name))\s" | Select-Object -First 1
$uuid = ($row -split "\s+" | Where-Object { $_ -match "^[0-9a-f-]{36}$" } | Select-Object -First 1)
if (-not $uuid) { Write-Host "หา UUID ของ tunnel ไม่เจอ" -ForegroundColor Red; exit 1 }

$credentials = Join-Path $cfDir "$uuid.json"
if (-not (Test-Path $credentials)) {
    Write-Host "ไม่พบไฟล์ credential $credentials" -ForegroundColor Red
    exit 1
}

Write-Host "ผูก DNS $Hostname → $Name…" -ForegroundColor Cyan
cloudflared tunnel route dns $Name $Hostname
if ($LASTEXITCODE -ne 0) {
    Write-Host "ผูก DNS ไม่สำเร็จ — โดเมนนี้อยู่ในบัญชี Cloudflare ที่ล็อกอินไว้หรือเปล่า" -ForegroundColor Red
    exit 1
}

$config = Join-Path $cfDir "config.yml"
@"
tunnel: $Name
credentials-file: $credentials

ingress:
  - hostname: $Hostname
    service: http://127.0.0.1:$Port
  - service: http_status:404
"@ | Set-Content -Path $config -Encoding utf8
Write-Host "เขียน $config แล้ว" -ForegroundColor Green

# ลิงก์ในแจ้งเตือนต้องเป็นโฮสต์นี้ ไม่งั้นมือถือกดแล้วเปิดไม่ได้
$envFile = Join-Path $PSScriptRoot ".env"
$url = "https://$Hostname"
if (Test-Path $envFile) {
    $lines = Get-Content $envFile
    if ($lines | Select-String '^\s*CLIPQUEUE_BASE_URL\s*=') {
        $lines = $lines -replace '^\s*CLIPQUEUE_BASE_URL\s*=.*', "CLIPQUEUE_BASE_URL=$url"
    } else {
        $lines += "CLIPQUEUE_BASE_URL=$url"
    }
    $lines | Set-Content -Path $envFile -Encoding utf8
    Write-Host "ตั้ง CLIPQUEUE_BASE_URL=$url ใน .env แล้ว" -ForegroundColor Green
}

Write-Host ""
Write-Host "  เสร็จแล้ว เปิดใช้งานด้วย" -ForegroundColor Green
Write-Host "  .\tunnel.ps1 -Name $Name" -ForegroundColor Cyan
Write-Host ""
Write-Host "  URL คงที่: $url"
Write-Host "  หน้าโพสต์ในมือถือ: $url/post"
Write-Host ""
