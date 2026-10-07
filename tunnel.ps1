# เปิด ClipQueue ออกอินเทอร์เน็ตผ่าน Cloudflare Tunnel
#
#   .\tunnel.ps1              เปิดด้วย quick tunnel (URL สุ่มใหม่ทุกครั้ง)
#   .\tunnel.ps1 -Name myclip ใช้ named tunnel ที่ตั้งไว้แล้ว (URL คงที่)
#
# สคริปต์นี้เปิด tunnel ก่อน อ่าน URL ที่ได้จริง แล้วค่อยสตาร์ตเซิร์ฟเวอร์
# ด้วย CLIPQUEUE_BASE_URL = URL นั้น ลิงก์ในแจ้งเตือนจึงเปิดได้จากมือถือเสมอ

param(
    [string]$Name,
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")
$env:PYTHONIOENCODING = "utf-8"

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "ยังไม่ได้สร้าง virtualenv" -ForegroundColor Red; exit 1 }

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "ยังไม่ได้ติดตั้ง cloudflared — ติดตั้งด้วย:" -ForegroundColor Yellow
    Write-Host "  winget install Cloudflare.cloudflared"
    exit 1
}

# โทเคนตัวอย่างเดาได้ ห้ามเปิดออกเน็ตพร้อมกับมัน
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    $demo = Select-String -Path $envFile -Pattern '^\s*CLIPQUEUE_DEMO_TOKEN\s*=\s*\S' -Quiet
    if ($demo) {
        Write-Host "พบ CLIPQUEUE_DEMO_TOKEN ใน .env — ลบออกก่อนเปิดออกอินเทอร์เน็ต" -ForegroundColor Red
        exit 1
    }
}

& $py -m server.doctor
if ($LASTEXITCODE -ne 0) { Write-Host "`nแก้ที่ doctor บอกก่อน" -ForegroundColor Red; exit 1 }
& $py tools\backup.py --keep 14

$log = Join-Path $env:TEMP "clipqueue-tunnel.log"
Remove-Item $log -ErrorAction SilentlyContinue

if ($Name) {
    $args = @("tunnel", "--no-autoupdate", "run", "--url", "http://127.0.0.1:$Port", $Name)
} else {
    $args = @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Port")
}

Write-Host "`nกำลังเปิด Cloudflare Tunnel…" -ForegroundColor Cyan
$tunnel = Start-Process cloudflared -ArgumentList $args -PassThru -NoNewWindow `
          -RedirectStandardError $log -RedirectStandardOutput "$log.out"

# cloudflared พิมพ์ URL ออกทาง stderr หลังต่อสำเร็จ รออ่านจนกว่าจะเจอ
$publicUrl = $null
foreach ($i in 1..40) {
    Start-Sleep -Milliseconds 750
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches |
             Select-Object -First 1
        if ($m) { $publicUrl = $m.Matches[0].Value; break }
    }
    if ($tunnel.HasExited) { break }
}

if (-not $publicUrl) {
    if ($Name) {
        Write-Host "ใช้ named tunnel — ใส่โดเมนของคุณเองใน .env ที่ CLIPQUEUE_BASE_URL" -ForegroundColor Yellow
        $publicUrl = $env:CLIPQUEUE_BASE_URL
        if (-not $publicUrl -or $publicUrl -match "127\.0\.0\.1|localhost") {
            Write-Host "CLIPQUEUE_BASE_URL ยังเป็น localhost — ลิงก์ในแจ้งเตือนจะเปิดไม่ได้" -ForegroundColor Red
            Stop-Process -Id $tunnel.Id -ErrorAction SilentlyContinue
            exit 1
        }
    } else {
        Write-Host "อ่าน URL จาก cloudflared ไม่ได้ ดู log: $log" -ForegroundColor Red
        Stop-Process -Id $tunnel.Id -ErrorAction SilentlyContinue
        exit 1
    }
}

# ค่านี้ต้องตั้งก่อนสตาร์ตเซิร์ฟเวอร์ เพราะโมดูลแจ้งเตือนอ่านตอน import
$env:CLIPQUEUE_BASE_URL = $publicUrl
$env:CLIPQUEUE_PUBLIC = "1"

Write-Host ""
Write-Host "  เปิดออกอินเทอร์เน็ตแล้ว" -ForegroundColor Green
Write-Host "  $publicUrl" -ForegroundColor Green
Write-Host "  หน้าโพสต์ในมือถือ: $publicUrl/post"
Write-Host ""
Write-Host "  ครั้งแรกให้เปิด $publicUrl/post?token=<token ของคุณ>" -ForegroundColor Yellow
Write-Host "  กด Ctrl+C เพื่อปิดทั้งเซิร์ฟเวอร์และ tunnel"
Write-Host ""

try {
    & $py -m uvicorn server.app:app --host 127.0.0.1 --port $Port --log-level warning
} finally {
    Write-Host "`nกำลังปิด tunnel…"
    Stop-Process -Id $tunnel.Id -ErrorAction SilentlyContinue
}
