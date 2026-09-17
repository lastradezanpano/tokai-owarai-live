# 東海お笑いライブ帳

愛知・岐阜・三重のお笑いライブとチケット発売状況をまとめる静的Webサイトです。

## 公開・更新

- GitHub Pagesで一般公開
- GitHub Actionsが毎日9:15ごろ（日本時間）に自動更新
- Actions画面の Update and deploy から手動更新可能
- ページの「情報更新」ボタンは公開済みの最新 data/events.json を再取得

FANYチケットはログイン不要の公開検索APIだけを利用します。パスワード、Cookie、APIキーは保存しません。

## ローカル確認

preview.ps1 をPowerShellから実行し、http://127.0.0.1:8765/index.html を開きます。
