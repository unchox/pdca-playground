# IMPLEMENTATION.md — PDCA 自律ループ 実装指示書（Claude Code 実行用）

このファイルは、`pdca-scaffold` を**実リポジトリに導入して初回ループを回すまで**を、
Claude Code が順に実行するための指示書です。スキャフォールド本体とは別のオペレーション
文書であり、ランタイム構成物には含めません。

> 使い方: 対象リポジトリのルートでこのファイルを Claude Code に読ませ、「IMPLEMENTATION.md の
> Phase 0 から順に実行して。各 STOP では必ず確認を取って」と指示してください。

---

## 実行者（Claude Code）への厳守ルール

1. **秘密情報は絶対に自分で入力しない。** `PDCA_PAT` / `ANTHROPIC_API_KEY` /
   `CLAUDE_CODE_OAUTH_TOKEN` の値の登録は**人間が行う**。`gh secret set` を実行者が走らせて
   値を打ち込むこともしない。手順を提示して人間に委ねる（= STOP-SECRET）。
2. **リポジトリ設定の変更は事前承認制。** branch protection / required checks / Actions 有効化
   などは、コマンド案を提示し、人間の「やって」を得てから実行する（= STOP-SETTINGS）。
3. **公開・不可逆操作は事前承認制。** ブランチ初回 push、PR 作成、Issue 作成の自動化を回し始める
   操作は、内容を要約して確認を取る（= STOP-PUBLISH）。
4. **guard のテストが緑でなければ先へ進まない。** Phase 1 のゲートを越えられなければ停止して報告。
5. **曖昧なら推測せず質問する。** タスクのゴール・受け入れ基準が固まらないうちにループを起動しない。

---

## Phase 0 — 前提確認（読み取りのみ）

確認し、欠けていれば人間に報告:

- [ ] 対象リポジトリが GitHub 上にある（`git remote -v` で origin が GitHub）。
- [ ] `gh auth status` が認証済み。
- [ ] GitHub Actions がこのリポジトリで有効。
- [ ] Python 3.12 が使える（`python --version`）。
- [ ] 人間が用意できるトークン: `PDCA_PAT`（fine-grained PAT か GitHub App、`contents:write` と
      `actions:write` 相当）、および `ANTHROPIC_API_KEY` **または** `CLAUDE_CODE_OAUTH_TOKEN`。

---

## Phase 1 — スキャフォールド導入と自己検証（ゲート）

1. `pdca-scaffold` の中身を対象リポジトリのルートに展開する。既存ファイルがあれば**上書きせず diff
   を提示**して確認を取る（特に `CLAUDE.md` がある場合）。
2. 依存導入と guard 自己検証:
   ```bash
   pip install -e ".[dev]" 2>/dev/null || pip install pytest pytest-json-report ruff jsonschema
   pytest tests/ -q          # 期待: 22 passed（guard 16 + act 6）
   ruff check .pdca tests    # 期待: All checks passed
   ```
   **緑でなければここで STOP。**（停止判定を、まだ検証できていない停止判定の上に積まない）
3. オフライン・ドライラン（モデルもGitHubも使わず、制御コアだけ確認）:
   ```bash
   # 緑CIの模擬 → completed に遷移し、maker は呼ばれないこと
   echo '{"outcome":"pass","failing_checks":[],"error_kinds":[],"summary":"dry"}' > /tmp/ci_pass.json
   PDCA_STATE=/tmp/dry.json PDCA_CI_RESULT=/tmp/ci_pass.json python .pdca/act.py
   # 期待: decision=completed
   ```
   `decision=completed` を確認できたら制御コアは健全。確認できなければ STOP。

---

## Phase 2 — GitHub 制御基盤の設定

### 2a. ラベル作成（実行者が実施可）
```bash
gh label create pdca-escalation -c "#B60205" -d "PDCA loop halted, needs human" || true
gh label create pdca-replan     -c "#5319E7" -d "PDCA contract needs replanning" || true
```

### 2b. シークレット登録 — **STOP-SECRET（人間が実施）**
次を人間に提示し、人間自身に実行してもらう（実行者は値に触れない）:
```bash
gh secret set PDCA_PAT                  # サイクル間で CI を再発火させる PAT/App token
gh secret set ANTHROPIC_API_KEY         # 従量APIを使う場合
#   または
gh secret set CLAUDE_CODE_OAUTH_TOKEN   # Max/Team シートを使う場合
```

### 2c. ブランチ保護と必須チェック — **STOP-SETTINGS（承認後に実施）**
完了判定（全ゲート緑）と人間ゲートを GitHub ネイティブに執行する。`main` 等の保護ブランチに対し、
必須ステータスチェックの context は CI ジョブ名 **`check`**:
```bash
gh api -X PUT repos/{owner}/{repo}/branches/main/protection \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=check' \
  -F 'enforce_admins=true' \
  -F 'required_pull_request_reviews[required_approving_review_count]=1' \
  -F 'restrictions=null'
```
コマンド案を提示し、承認を得てから実行。共有リポジトリでは特に慎重に。

---

## Phase 3 — 初回ループ（小さく・TDDで）

> 最初は「**受け入れ基準を pytest で書ける編集タスク**」を選ぶ。CI がオラクルになり、maker は
> 編集のみで済む（例: 既存の小機能のバグ修正、明確な仕様の小関数実装）。
> 大きなタスク・本番接続・破壊的操作を含むものは避ける。

1. **Plan（契約生成）。** `planner` サブエージェントをゴールに対して実行する。出力の YAML を
   `.pdca/plan.yaml` に保存（監査用）。`readiness.sufficient=false` なら不足項目を人間に返して STOP。
2. **人間の計画承認（必須ゲート）。** `.pdca/plan.yaml` を人間がレビューして承認。承認できないなら
   修正して再生成。承認後に commit する。
3. **受け入れ基準を「赤いテスト」として具現化（TDD）。** 契約の Tier A `test_spec` を実際の
   pytest ファイルとして書き、**まだ失敗する状態**で用意する。これがループの完了条件になる。
4. **ループ開始 — STOP-PUBLISH。** 内容を要約して承認を得てから:
   ```bash
   git switch -c pdca/<task-id>
   git add .pdca/plan.yaml tests/...     # 赤いテストと契約
   git commit -m "pdca: seed <task-id> acceptance tests (red)"
   git push -u origin pdca/<task-id>     # CI(ci) が走り → red → pdca-act 発火
   ```
   以降は自律: ci(Check) → pdca-act(Act: guard.evaluate) → continue なら maker 修正＋PAT push →
   再び ci … を、全ゲート緑（completed）か stopped_* / replan まで回す。
5. **観測。** `gh run watch` または Actions タブで `ci` と `pdca-act` の往復を追う。各サイクルの
   状態は対象ブランチの `.pdca/state.json` に積まれる。

---

## Phase 4 — 実行時の検証チェックリスト（不変条件の現物確認）

初回ループ中/後に、設計の不変条件が実際に効いているか確認:

- [ ] **継続が回る**: red の後 `pdca-act` が走り、maker のパッチコミットが乗って ci が再発火している。
- [ ] **PAT はサイクル再発火のためだけに使われている**: state-only コミット（terminal）で ci が
      再発火していない。`pdca-act.yml` 内 `secrets.PDCA_PAT` 参照は continue step の1箇所のみ。
- [ ] **完了で止まる**: 全ゲート緑で `completed`、PR にコメント、ループが再発火しない。
- [ ] **暴走しない**: `max_cycles` 到達で `stopped_max`、同一失敗反復で `stopped_oscillation` →
      `pdca-escalation` Issue が立つ。
- [ ] **replan は再開しない**: `replan` 時に `pdca-replan` Issue が立ち、CI は再発火しない。
- [ ] **空サイクルを回さない**: maker が無変更なら Act ステップが明示失敗する。

---

## Phase 5 — キルスイッチ / ロールバック

暴走や想定外時に**即停止**する手段（人間に明示しておく）:

- **そのループだけ止める**: 対象 `pdca/<task>` ブランチを削除（push が止まり次サイクルが起きない）。
- **全ループを止める**: Actions タブで `pdca-act` ワークフローを Disable、または `PDCA_PAT` を失効。
  PAT を失効させると再発火能力が消えるので、走行中のループは次サイクルで自然停止する。
- **設定を戻す**: branch protection を解除（STOP-SETTINGS と同じく承認制）。

---

## Phase 6 — トラブルシュート（初回によくある詰まり）

| 症状 | 原因 | 対処 |
|---|---|---|
| `pdca-act` が発火しない | `ci` の `name:` と `workflow_run.workflows` 不一致 / Actions無効 | 両者が `ci` で一致しているか、Actions が有効か確認 |
| continue で次サイクルが始まらない | `PDCA_PAT` 未登録 or 権限不足（既定 `GITHUB_TOKEN` は再発火しない仕様） | PAT を登録し `contents:write`/`actions:write` 相当を付与 |
| Act ステップが「no file changes」で失敗 | maker が編集できなかった（権限不足/曖昧プロンプト） | `PDCA_MAKER_ALLOWED_TOOLS` を見直す。タスクがビルド/実行を要すなら `Bash(...)` を追加 |
| `claude: command not found` | ランナーに Claude Code 未導入 | `pdca-act.yml` の install ステップ（native installer / npm フォールバック）を確認 |
| 認証エラー | `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN` 未設定 | いずれか一方を登録（OAuth は Max/Team シート） |
| 何度も `stopped_oscillation` | 受け入れ基準（テスト）自体が満たせない/矛盾 | テスト=契約を見直す。これは正しく「replan すべき」のサイン |
| artifact が無い | `ci` のアップロードと Act のダウンロードの `run-id` 不整合 | `ci.yml` の upload と `pdca-act.yml` の download を確認 |

---

## Phase 7 — 初回完走後の次手（実データが取れてから）

ここまでで 1 タスクが自律完走したら、実データ（効いた停止条件・エスカレーション頻度・maker の
コスト/ターン消費。後者は各サイクルの `.pdca/maker_last.json` に記録）を基に:

- **(b) Decision Evidence**: 停止/継続の根拠（品質列・signature 出現回数等）を `state.json` に構造化。
- **(c) 品質差分フィードバック**: `build_feedback` に前回比の改善/悪化を追加。
- それらが安定してから Meta Controller（プロジェクト層）へ。**タスク層が回る前に多層化しない。**

---

## チューニング（環境変数 / `.pdca/state.json`）

- maker: `PDCA_MAKER_MODEL`(`claude-sonnet-5` 明示指定・alias 追従禁止) / `PDCA_MAKER_MAX_TURNS`(30) / `PDCA_MAKER_ALLOWED_TOOLS`。
- guard: `max_cycles`(5) / `oscillation_threshold`(3) / `oscillation_window`(5) /
  `no_progress_threshold`(2) / `min_quality_delta`(0.01)。
