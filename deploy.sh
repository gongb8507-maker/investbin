#!/usr/bin/env bash
# Vercel 배포 스크립트 - data.json 업데이트 후 자동 배포

set -e

echo "🔄 데이터 업데이트 중..."
cd "$(dirname "$0")"
python backend.py

echo "📦 Vercel 배포 중..."
# Vercel CLI가 설치되어 있다면
if command -v vercel &> /dev/null; then
    vercel --prod --token="$VERCEL_TOKEN"
else
    echo "⚠️  Vercel CLI 미설치. 수동 배포 필요:"
    echo "   npm i -g vercel && vercel --prod"
fi

echo "✅ 완료!"