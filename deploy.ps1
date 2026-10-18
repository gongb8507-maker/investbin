# Vercel 배포 스크립트 (PowerShell) - data.json 업데이트 후 자동 배포

$ErrorActionPreference = "Stop"

Write-Host "🔄 데이터 업데이트 중..." -ForegroundColor Cyan
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
python backend.py

Write-Host "📦 Vercel 배포 중..." -ForegroundColor Cyan
# Vercel CLI 확인
$vercel = Get-Command vercel -ErrorAction SilentlyContinue
if ($vercel) {
    $env:VERCEL_TOKEN = $env:VERCEL_TOKEN
    vercel --prod --token=$env:VERCEL_TOKEN
} else {
    Write-Warning "⚠️  Vercel CLI 미설치. 수동 배포 필요:"
    Write-Host "   npm i -g vercel && vercel --prod" -ForegroundColor Yellow
}

Write-Host "✅ 완료!" -ForegroundColor Green