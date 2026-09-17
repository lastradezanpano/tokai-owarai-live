# 東海お笑いライブ帳 - 運用セットアップ手順

## 全体構成

```
[Windowsタスクスケジューラー]
   → [Claude Code をバッチ(非対話)モードで起動]
       → sources.json の各サイトを WebFetch で確認
       → 愛知/岐阜/三重のお笑いライブ情報を抽出
       → data/events.json を更新
       → プロジェクト内の index.html を更新
       → 利用可能な場合は公開済みArtifactも同じURLで再公開
   → Codexで index.html を開き、いつでも最新情報を確認できる
```

Web公開ページ: https://claude.ai/artifact/5saz5d5CVZmiU6VrCUBWmu

Codexで確認する固定HTML: `E:\AI\自動作成の検討\owarai_live_tracker\index.html`

完成画面をブラウザ表示する場合は、Codexのターミナルで次を実行し、`http://127.0.0.1:8765/index.html` を開きます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "E:\AI\自動作成の検討\owarai_live_tracker\preview.ps1"
```

HTML本体の正本はプロジェクト内の `index.html` です。Claude Code/Codexがデータ収集後にこのファイルを更新し、Artifact公開機能が利用できる場合だけ公開版にも同期します。そのため公開版へサインインできない場合でも、Codex内では固定HTMLを安定して確認できます。

## 手順1: 一度、手動で更新を試す

まずタスクスケジューラーに登録する前に、ターミナルで一度動作確認してください。

```bash
claude -p "$(cat 'E:/AI/自動作成の検討/owarai_live_tracker/update_prompt.md')"
```

- `claude` コマンドが見つからない場合は、Claude Code のインストール先を確認してください。
- 実行中、WebFetch や Artifact の再公開時に許可確認が出る場合があります。何度も出る場合は下記「手順2」で許可リストに追加してください。
- 初回はサイト構造の解釈にばらつきが出ることがあるので、`data/events.json` と Artifact ページの内容を必ず目視確認してください。

## 手順2（任意）: 許可確認を減らす

無人実行時に許可確認で止まらないよう、`E:\AI\自動作成の検討\.claude\settings.local.json` の `permissions.allow` に以下を追加すると、WebFetchとArtifact操作が確認なしで実行されます（他の破壊的操作は引き続きブロックされたままです）。

```json
"WebFetch",
"Artifact"
```

この変更はセキュリティ設定の変更にあたるため、必要であれば私(Claude)に「追加して」と指示してください。自動では変更しません。

## 手順3: Windowsタスクスケジューラーに登録する

1. 「タスクスケジューラー」を開く
2. 「基本タスクの作成」→ 名前: `お笑いライブ帳_定期更新`
3. トリガー: 毎日（例: 朝9:00）または毎週（例: 月・木 9:00）
4. 操作: 「プログラムの開始」
   - プログラム/スクリプト: `claude` の実行ファイルパス（`where claude` で確認）
   - 引数の追加:
     ```
     -p "$(cat 'E:/AI/自動作成の検討/owarai_live_tracker/update_prompt.md')"
     ```
     ※ Windowsのタスクスケジューラーは `$(cat ...)` のようなシェル展開をしないため、実際にはバッチファイル(.bat)またはPowerShellスクリプト(.ps1)を用意し、その中でプロンプトファイルを読み込んで `claude -p` に渡す形にするのが確実です。下記の `run_update.ps1` を参照してください。
   - 開始(作業)フォルダー: `E:\AI\自動作成の検討\owarai_live_tracker`
5. 「条件」タブで「AC電源」「スリープ解除」などPCの状態に応じた設定を必要に応じて調整
6. 保存して、一度「実行」で手動テストする

### run_update.ps1（雛形）

タスクスケジューラーからはこのPowerShellスクリプトを呼び出す運用を推奨します。

```powershell
$promptPath = "E:\AI\自動作成の検討\owarai_live_tracker\update_prompt.md"
$prompt = Get-Content -Raw -Encoding UTF8 $promptPath
Set-Location "E:\AI\自動作成の検討\owarai_live_tracker"
claude -p $prompt
```

タスクスケジューラーの「操作」は以下のように設定します。

- プログラム/スクリプト: `powershell.exe`
- 引数の追加: `-NoProfile -ExecutionPolicy Bypass -File "E:\AI\自動作成の検討\owarai_live_tracker\run_update.ps1"`

## 更新頻度の目安

- 前売開始・公演告知は数日〜数週間おきに出るため、**週1〜2回**の実行で十分実用的です。
- PCの電源が入っていない時間帯は実行されないため、PCをよく使う時間帯（例: 起動直後や昼休み）に合わせるのがおすすめです。

## 注意事項

- 各チケットサイトの利用規約の範囲内で、公開されている一覧ページの閲覧・要約として利用してください。ログイン必須ページや購入手続きの自動化は行いません。
- サイトのURL構造やレイアウトが変わると `sources.json` の更新が必要になります。実行結果は `update_log.md` に記録されるので、失敗が続く情報源がないか時々確認してください。
- 取得先ページの中に「指示を無視しろ」等の埋め込み指示があっても、Claudeはそれに従わず、通常のデータとして扱います（`update_prompt.md` の手順7）。
