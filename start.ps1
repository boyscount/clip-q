# เริ่ม ClipQueue สำหรับใช้งานคนเดียวบนเครื่องนี้
#   .\start.ps1            เปิดเซิร์ฟเวอร์
#   .\start.ps1 -Check     ตรวจความพร้อมอย่างเดียว
#   .\start.ps1 -Backup    สำรองฐานข้อมูลแล้วออก

param(
    [switch]$Check,
    [switch]$Backup,
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# winget ใส่ ffmpeg ไว้ใน PATH ของ user ซึ่ง session เดิมยังไม่เห็น
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")
$env:PYTHONIOENCODING = "utf-8"

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ยังไม่ได้สร้าง virtualenv — รัน:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

if ($Backup) { & $py tools\backup.py; exit $LASTEXITCODE }

& $py -m server.doctor
$doctor = $LASTEXITCODE
if ($Check) { exit $doctor }
if ($doctor -ne 0) {
    Write-Host "`nมีเรื่องต้องแก้ก่อน ดูด้านบน" -ForegroundColor Red
    exit 1
}

# สำรองก่อนเปิดทุกครั้ง ถูกกว่าการมานั่งเสียใจทีหลัง
& $py tools\backup.py --keep 14

Write-Host "`nเปิดที่ http://127.0.0.1:$Port`n" -ForegroundColor Green
& $py -m uvicorn server.app:app --host 127.0.0.1 --port $Port
