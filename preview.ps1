# 東海お笑いライブ帳をローカルWebサーバーで確認するためのスクリプト
param(
    [int]$Port = 8765
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pageUrl = "http://127.0.0.1:$Port/index.html"

Write-Host "東海お笑いライブ帳を配信しています: $pageUrl"
Write-Host "終了するには Ctrl+C を押してください。"

Set-Location -LiteralPath $root
python -m http.server $Port --bind 127.0.0.1
