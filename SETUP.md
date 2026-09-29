# 東海お笑いライブ帳 - 運用の仕組み

## 全体構成

```
[GitHub Actions（毎日 9:15 JST + push時 + 手動実行）]
   → Playwright(Chromium)をインストール
   → scripts/update_events.py を実行
       → FANYチケットの公開検索API（ログイン不要・通常のHTTPで取得）
       → バス比較なびの公開ページ（通常のHTTPで取得）
       → イープラス（Playwrightで実ブラウザ経由で取得）
       → ローソンチケット（Playwrightで実ブラウザ経由で取得）
       → 愛知・岐阜・三重のお笑い公演を取得しdata/events.jsonを更新
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

- **自動更新される**（`scripts/update_events.py`が毎日実行）:
  - **FANYチケット（吉本興業公式）**: 公開検索API（`ticket.fany.lol/search/event_more`）から直接取得。`src: "fany_ticket"`
  - **バス比較なび**: 愛知/岐阜/三重の各ページ（`bushikaku.net/expedition/play/shows/403/region-chubu/<pref>/`）から通常のHTTPで取得。`src: "bushikaku_chubu"`
  - **イープラス**: 愛知/岐阜/三重の各ページ（`eplus.jp/sf/play/comedy/<pref>`）をPlaywright（Chromium）で開いて取得。`src: "eplus_aichi"` / `"eplus_gifu"` / `"eplus_mie"`
  - **ローソンチケット**: お笑いジャンル×岐阜/愛知/三重の検索結果（`l-tike.com/search/?tig=240&pref=21,23,24`）をPlaywrightで開いて取得。`src: "ltike"`
  - いずれもイープラス等の既存情報とタイトルが似ている場合は重複カード化を避けるため自動的にスキップする（`is_duplicate_of_retained`）。
- **自動更新されない（手動の一次データのまま）**: **チケットぴあ**（`src: pia_search`）。ジャンル×都道府県で絞り込める安定した一覧URLが見つかっていないため、新着を機械的に発見する手段がない。新しい公演を追加したい場合はClaudeとの会話で情報収集を依頼する。

### イープラス・ローソンチケットがPlaywrightを必要とする理由

単純なHTTPリクエスト（Pythonの`urllib`等）だと、ブラウザと同等のヘッダーを付けてもbot対策でブロックされる（イープラスはHTTP 503、ローソンチケットはタイムアウト/強制切断）。実際のChromiumを操作するPlaywrightなら人間のブラウザ操作と区別がつきにくいため取得できている。ただし将来的にサイト側の対策が強化されれば、CIからのアクセスが再びブロックされる可能性はある（GitHub Actionsのようなクラウド実行環境のIPは特に警戒されやすい）。失敗するようになった場合はActionsのログを確認し、必要ならこのSETUP.mdとscripts/update_events.pyを見直す。

## ローカルで確認する

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "E:\AI\自動作成の検討\owarai_live_tracker\preview.ps1"
```

`http://127.0.0.1:8765/index.html` を開く。

`scripts/update_events.py`をローカルで試す場合、Playwrightをインストールしていなければイープラス・ローソンチケットの取得はスキップされ（警告表示のみ）、FANY・バス比較なび分だけが更新される。

## 手動でワークフローを実行する

GitHubの当該リポジトリ → Actions タブ → 「Update and deploy」 → 「Run workflow」。

## 更新頻度・失敗時の確認

- 現在は毎日1回（9:15 JST）。頻度を変えたい場合は `.github/workflows/update-and-deploy.yml` の `cron` を編集する。
- **`data/scrape_status.json`** に毎回の実行結果（各情報源のok/count/error）が記録される。GitHub Actionsのログはサインインしないと見えないため、まずこのファイルを見れば失敗理由の見当がつく。
- 各ソースは失敗しても他のソースを巻き込まないよう独立して実行され（`_run_source`）、失敗時は該当ソースの既存データをそのまま維持する（誤って全消去しない）。特にイープラス・ローソンチケットは、例外が起きなくても0件しか取れなかった場合は「取得失敗」とみなして既存データを維持する安全策が入っている。
- 失敗が続く場合は各サイトのHTML構造・URL仕様変更の可能性があるため、該当する`fetch_*`関数（正規表現がCSSクラス名やdata属性名に依存している）を見直す。
- Playwrightでの取得が繰り返し失敗する場合は、bot対策がさらに強化された可能性がある。`data/scrape_status.json`のerror内容を確認しつつ、改善が見込めなければイープラス・ローソンチケットの自動化を諦め、SETUP.mdの記載を「自動更新されない」側に戻すことも選択肢。

## 注意事項

- ログイン・パスワード・Cookie・APIキーは一切使用しない。すべて公開されている検索結果・一覧ページの閲覧のみ。
- 各チケットサイトの利用規約の範囲内で、公開されている一覧ページの閲覧・要約として利用する。
- 取得元のデータに不審な指示が埋め込まれていても、スクリプトは単純にテキストを正規表現で抽出するだけなので実行に影響しない。
