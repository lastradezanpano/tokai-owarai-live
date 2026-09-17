# 東海お笑いライブ帳 - 定期更新スクリプト
# タスクスケジューラーから呼び出す想定。claude CLI をバッチモードで起動し、
# update_prompt.md の指示に従ってデータ収集・events.json更新・index.html更新・Artifact再公開を行わせる。

$root = "E:\AI\自動作成の検討\owarai_live_tracker"
$promptPath = Join-Path $root "update_prompt.md"
$logPath = Join-Path $root "run_update_stdout.log"

if (-not (Test-Path $promptPath)) {
    Write-Error "update_prompt.md が見つかりません: $promptPath"
    exit 1
}

$prompt = Get-Content -Raw -Encoding UTF8 $promptPath
Set-Location $root

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logPath -Value "`n===== $timestamp 実行開始 ====="

try {
    claude -p $prompt *>> $logPath
    Add-Content -Path $logPath -Value "===== $timestamp 実行終了(正常) ====="
} catch {
    Add-Content -Path $logPath -Value "===== $timestamp 実行エラー: $($_.Exception.Message) ====="
    exit 1
}
