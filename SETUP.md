# 東海お笑いライブ帳 - 運用の仕組み

## 全体構成

```
[GitHub Actions（毎日 9:15 JST + push時 + 手動実行）]
   → scripts/update_events.py を実行
       → FANYチケットの公開検索API（ログイン不要）から
         愛知・岐阜・三重のお笑い公演を取得
       → data/events.json を更新
   → 変更があれば自動コミット・push
   → GitHub Pagesへ自動デプロイ
   → index.html はページを開くたびに data/events.json を
     fetchして最新表示に更新（「情報更新」ボタンで手動再取得も可）
```

公開ページ: https://lastradezanpano.github.io/tokai-owarai-live/
GitHubリポジトリ: https://github.com/lastradezanpano/tokai-owarai-live
ワークフロー定義: `.github/workflows/update-and-deploy.yml`
更新スクリプト: `scripts/update_events.py`

**PCの電源やClaude Codeのセッションに一切依存せず、GitHub側で完結して自動更新されます。**

## 自動更新される範囲と、されない範囲

- **自動更新される**: FANYチケット（吉本興業公式）掲載分。`scripts/update_events.py`が毎日API から直接取得し、`src: "fany_ticket"` の公演を丸ごと入れ替える。
- **自動更新されない（手動の一次データのまま）**: イープラス・チケットぴあ・バス比較なび経由で登録した公演（`src: eplus_aichi` / `eplus_mie` / `eplus_gifu` / `pia_search` / `bushikaku_chubu`）。これらはスクリプトが「開催日が今日以降なら残す」だけで、新しい公演の追加はしない。イープラス等で新しい公演を追加したい場合は、Claudeとの会話で情報収集を依頼するか、`scripts/update_events.py`にそれらのサイト向けの取得処理を追加する必要がある。

## ローカルで確認する

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "E:\AI\自動作成の検討\owarai_live_tracker\preview.ps1"
```

`http://127.0.0.1:8765/index.html` を開く。

## 手動でワークフローを実行する

GitHubの当該リポジトリ → Actions タブ → 「Update and deploy」 → 「Run workflow」。

## 更新頻度・失敗時の確認

- 現在は毎日1回（9:15 JST）。頻度を変えたい場合は `.github/workflows/update-and-deploy.yml` の `cron` を編集する。
- 実行結果はGitHubのActionsタブで確認できる。失敗が続く場合はFANY側のAPI仕様変更の可能性があるため、`scripts/update_events.py` の`fetch_page`/`normalize`関数を見直す。

## 注意事項

- FANYチケットの公開検索APIのみを利用し、ログイン・パスワード・Cookie・APIキーは一切使用しない。
- 各チケットサイトの利用規約の範囲内で、公開されている一覧ページの閲覧・要約として利用する。
- 取得元のデータに不審な指示が埋め込まれていても、スクリプトは単純にJSONを解析するだけなので実行に影響しない。
