# 東海お笑いライブ帳 - 運用の仕組み

## 全体構成

```
[GitHub Actions（毎日 9:15 JST + push時 + 手動実行）]
   → Playwright(Chromium)をインストール
   → scripts/update_events.py を実行
       → FANYチケットの公開検索API（ログイン不要・通常のHTTPで取得）
       → バス比較なびの公開ページ（通常のHTTPで取得）
       → イープラス（Playwrightで実ブラウザ経由で取得）
       → 名古屋よしもと公式スケジュール（通常のHTTPで取得）
       → よしもとライブ公式の都道府県検索＋各公演ページ（通常のHTTPで取得）
       → 3日以上続けて失敗した情報源があれば GitHub の Issue を自動作成（メール通知）
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
  - **FANYチケット（吉本興業公式）**: 公開検索API（`ticket.fany.lol/search/event_more`）から直接取得。`src: "fany_ticket"`。**2026-10-22以降はHTTP 403（アクセス拒否）で失敗し続けている。** 同サイトのFAQページは見られるのに検索部分だけが拒否されるため、FANY側が意図的に機械的な取得を止めたと判断し、ブラウザを装って回避することはしない方針（2026-11-25）。代わりに下の名古屋よしもと・よしもとライブで補う。
  - **名古屋よしもと（公式）**: `nagoya.yoshimoto.co.jp/schedule/` のHTMLに当月〜翌月の公演（公演日・公演名・[会場]・[時間]）が全件入っている。`src: "nagoya_yoshimoto"`。販売状況は載っていないため `status: unknown`。会場名から三重・岐阜を判定し、当てはまらなければ愛知とする。同日同名の公演は開演時刻を公演名に付けて区別する。
  - **よしもとライブ（公式）**: `live.yoshimoto.co.jp/?pref=23&ym=&s=`（21=岐阜、24=三重）の検索結果から公演ページを集め、各ページの「公演日程」「会場」欄を読む。`src: "yoshimoto_live"`。単独公演ページは確実に読めるが、全国ツアーのページは書き方が公演ごとに違うため、【愛知】＜愛知公演＞●…愛知…のような見出しの直後に日付があるものだけを採用し、読み取れないものは掲載しない（日付を推測で載せないため）。読み取れなかったページ数は `scrape_status.json` の `unparsed_pages` に記録。
  - 両サイトとも robots.txt で対象ページは制限されておらず、吉本興業サイトのチケット約款等にも自動取得の禁止規定は見当たらなかった（2026-11-25確認）。正直なUser-Agentを名乗り、リクエスト間隔を1.5秒空けている。
  - **バス比較なび**: 愛知/岐阜/三重の各ページ（`bushikaku.net/expedition/play/shows/403/region-chubu/<pref>/`）から通常のHTTPで取得。`src: "bushikaku_chubu"`
  - **イープラス**: 愛知/岐阜/三重の各ページ（`eplus.jp/sf/play/comedy/<pref>`）をPlaywright（Chromium）で開いて取得。`src: "eplus_aichi"` / `"eplus_gifu"` / `"eplus_mie"`
  - いずれもチケットぴあ等の既存情報とタイトルが似ている場合は重複カード化を避けるため自動的にスキップする（`is_duplicate_of_retained`）。
- **自動更新されない（手動の一次データのまま）**:
  - **チケットぴあ**（`src: pia_search`）: ジャンル×都道府県で絞り込める安定した一覧URLが見つかっていないため、新着を機械的に発見する手段がない。
  - **ローソンチケット**（`src: ltike`。2026-09-29に自動化を断念）: Playwrightで実ブラウザ経由でアクセスしても`net::ERR_HTTP2_PROTOCOL_ERROR`で毎回失敗する。同じ方式でイープラスは成功しているため、ブラウザの精巧さの問題ではなく、GitHub ActionsのクラウドIPアドレス自体をローソンチケット側がネットワークレベルでブロックしていると考えられる。`scripts/update_events.py`にコードは残っている（`fetch_ltike`）が、`main()`の呼び出しから外してあるので実行されない。有料の住宅用プロキシ等を使えば回避できる可能性はあるが、無料運用のこのプロジェクトの範囲を超えるため対応しない方針。
  - 新しい公演を追加したい場合はClaudeとの会話で情報収集を依頼する。

### イープラスがPlaywrightを必要とする理由

単純なHTTPリクエスト（Pythonの`urllib`等）だと、ブラウザと同等のヘッダーを付けてもbot対策でHTTP 503になる。実際のChromiumを操作するPlaywrightなら人間のブラウザ操作と区別がつきにくいため取得できている。ただし将来的にサイト側の対策が強化されれば、CIからのアクセスが再びブロックされる可能性はある。失敗するようになった場合は`data/scrape_status.json`とこのSETUP.mdを見直す。

## ローカルで確認する

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "E:\AI\owarai_live_tracker\preview.ps1"
```

`http://127.0.0.1:8765/index.html` を開く。

`scripts/update_events.py`をローカルで試す場合、Playwrightをインストールしていなければイープラスの取得はスキップされ（警告表示のみ）、FANY・バス比較なび分だけが更新される。

## 手動でワークフローを実行する

GitHubの当該リポジトリ → Actions タブ → 「Update and deploy」 → 「Run workflow」。

## 更新頻度・失敗時の確認

- 現在は毎日1回（9:15 JST）。頻度を変えたい場合は `.github/workflows/update-and-deploy.yml` の `cron` を編集する。
- **`data/scrape_status.json`** に毎回の実行結果（各情報源のok/count/error）が記録される。GitHub Actionsのログはサインインしないと見えないため、まずこのファイルを見れば失敗理由の見当がつく。
- 各ソースは失敗しても他のソースを巻き込まないよう独立して実行され（`_run_source`）、失敗時は該当ソースの既存データをそのまま維持する（誤って全消去しない）。特にイープラスは、例外が起きなくても0件しか取れなかった場合は「取得失敗」とみなして既存データを維持する安全策が入っている。
- 取得「成功」でも、件数が前回（未来日分）の半分未満に急減した情報源は入れ替えずに既存データと統合する（2026-11-25追加。イープラスで20件→1件のような取りこぼしが実際に起きていたため）。統合した場合は `scrape_status.json` の `merged_due_to_drop` に記録。
- **停止の検知（2026-11-25追加）**: `scrape_status.json` の `history` に情報源ごとの「最後に成功した日（last_ok）」と「連続失敗日数（fail_streak）」を残す。3日以上続けて失敗した情報源があると `alert.md` が作られ、ワークフローが「自動更新：情報源の取得失敗が続いています」というIssueを作成（2日目以降は本文を更新、全て復旧したら自動で閉じる）。サイトの「収集している情報源」欄にも取得状況と最後に取得できた日を表示する。
- Issueのメール通知が届かない場合は、GitHubの Settings → Notifications と、リポジトリの Watch 設定を確認する。
- 失敗が続く場合は各サイトのHTML構造・URL仕様変更の可能性があるため、該当する`fetch_*`関数（正規表現がCSSクラス名やdata属性名に依存している）を見直す。
- Playwrightでの取得が繰り返し失敗する場合は、bot対策がさらに強化された可能性がある。`data/scrape_status.json`のerror内容を確認しつつ、改善が見込めなければイープラスの自動化も諦め、SETUP.mdの記載を「自動更新されない」側に戻すことも選択肢。

## 注意事項

- ログイン・パスワード・Cookie・APIキーは一切使用しない。すべて公開されている検索結果・一覧ページの閲覧のみ。
- 各チケットサイトの利用規約の範囲内で、公開されている一覧ページの閲覧・要約として利用する。
- 取得元のデータに不審な指示が埋め込まれていても、スクリプトは単純にテキストを正規表現で抽出するだけなので実行に影響しない。
