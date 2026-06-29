# PDCA 自律ループ — レビュー依頼書（別AI向け）

このドキュメントは、本スキャフォールド一式を別のAIにレビューしてもらうための説明書です。
ファイル名はすべて同梱物と1対1で一致しています（末尾の「ファイル目録」参照）。
本版は、replan / Judge(quality_vector) / no-progress を**実コードに実装した後**の状態を反映しています。

---

## 0. 何をレビューしてほしいか（要約）

GitHubを制御基盤（状態・隔離・ゲート・監査）に置き、唯一の自前制御ロジックを
「決定論的な停止・再計画・継続判定(guard/router)」に絞ったPDCA自律ループのスキャフォールドです。
設計が成立するかは、後述の**横断的な不変条件6点**がコードとワークフロー上で本当に守られているかにかかっています。
まずそこを検証してください。

---

## 1. 全体アーキテクチャ

人間がゴールを与える → Plan が機械検証可能な契約（受け入れ基準＋oracle）を生成・人間が承認 →
`pdca/<task>` ブランチへ push → `ci.yml`（決定論ゲート＝Check）→ 完了で `pdca-act.yml`（Act）が発火 →
`act.py` がCI結果と任意のJudge出力を記録し `guard.evaluate()` で次手を決定（完了 / 継続 / 再計画 / 停止）。

- LLM/Judgeは無状態の推論関数としてのみ呼ばれる。
- 状態遷移はすべてコードが決める（LLM/Judgeに制御を渡さない＝再現性の根拠）。
- 人間の必須ゲートは原則「計画承認」（および replan 時の再計画承認）。

```
push pdca/<task> ─► ci.yml (Check) ─► pdca-act.yml (Act: guard.evaluate)
                                          ├─ completed → PR コメント
                                          ├─ continue  → maker 修正 + PAT push → 次サイクル
                                          ├─ replan    → 再計画Issue + 人間承認ゲート（PATなし）
                                          └─ stopped_* → エスカレーション Issue
```

---

## 2. 横断的な不変条件（最優先レビュー対象）

これが崩れると設計全体が破綻します。各ファイル単体より先に、**この6点**を検証してください。

1. **停止判定を通らない限り次サイクルは始まらない。**
   CI再発火能力を持つ唯一のtoken `PDCA_PAT` は、`pdca-act.yml` の `decision == 'continue'` のstepでのみ参照される。
   → 検証: `pdca-act.yml` を grep し、`PDCA_PAT` が continue 以外のstepに露出していないこと（コメント行を除く）。`if:` 評価順とstepスキップ時の挙動。

2. **失敗シグネチャは生ログでなく「落ちたチェックidのソート集合」のハッシュ。**
   生ログをハッシュすると毎サイクル別物になり振動検知が永久に効かない。
   → 検証: `guard.py::compute_failure_signature` が `sorted(set(...))` を使い、ログ文字列を受けないこと。

3. **`evaluate()` は純粋関数。状態を変えるのは `record_outcome()` だけ。**
   同じ状態＝同じ判定。
   → 検証: `guard.py` で `evaluate` が `state` を書き換えないこと。`tests/test_guard.py` の決定論性。

4. **terminal状態は再起動しない（冪等）。**
   → 検証: `evaluate` が `TERMINAL_STATUSES` で `ALREADY_TERMINAL` を返すこと。`pdca-act.yml` がそれを安全側に扱うこと。

5. **REPLANは現在のループを終了し、人間承認なしに再開しない。**
   `decision == 'replan'` は `PDCA_PAT` を参照せず、再計画Issueを作成するだけ。
   → 検証: `pdca-act.yml` で `replan` 分岐がCI再発火を持たないこと。`guard.TERMINAL_STATUSES` に `replan` が含まれること。

6. **品質評価はJudgeが出しても、状態遷移はGuardが決める。**
   Judgeは `quality_vector`（正規化・高いほど良い）と `replan_requested` の構造化入力だけを出せる。
   → 検証: `act.py::read_judge_result` がJudge出力を検証して `record_outcome` に渡すだけで、LLM/Judgeが直接 `continue`/`completed`/`stopped_*` を決めないこと。品質停滞は `stopped_no_progress` になること。

---

## 3. ファイル別の説明とレビュー観点

### .pdca/guard.py — 停止判定の中核（最重要）
- **役割**: GitHubに委譲できない唯一の制御ロジック。永続状態を読み、継続 / 完了 / 再計画 / 停止(budget) / 停止(振動) / 停止(品質停滞) を返す。
- **契約**: `LoopState`(load/save), `compute_failure_signature`, `aggregate_quality`, `record_outcome`(唯一の変更操作), `evaluate`(純粋), `apply_decision`。`quality_vector` と `replan_requested` はJudge/Actから渡される入力で、Guardが最終判断する。
- **判定順序（実装）**: terminal → 空 → pass(=completed) → replan(明示) → 振動 → no-progress → budget → continue。
- **観点**: ① 振動検知が「最新シグネチャがwindow内に閾値回出現」というwindowed-count方式。A,B,A,B,Aのflappingも捕捉する意図。consecutive方式との優劣、誤検知/見逃し。② 判定順序の妥当性。特に「別失敗5種でbudget=5」は振動でなくSTOP_MAX（テストで固定済）。replanが振動より先に評価される設計（間違った契約を「stuck」と誤ラベルしない）の是非。③ シグネチャ16桁truncateの衝突リスク。④ `_is_stalled` の停滞判定（窓内の集約品質の総改善 < `min_quality_delta`）が過敏/鈍感でないか。集約が平均でよいか。

### .pdca/act.py — Actコントローラ
- **役割**: Actionsジョブ内で走る本体。CI結果＋任意のJudge結果を読む→`record_outcome`→`evaluate`→決定を `$GITHUB_OUTPUT` へ。continue時のみ `call_maker()` を呼ぶ。
- **maker結線（実装済）**: `call_maker()` は Claude Code headless（`claude -p <prompt> --permission-mode acceptEdits --max-turns N --model <m> --output-format json`）を起動し、**ファイル編集のみ**を行う。git の commit/push は**ワークフロー層**が担い、token選択（PAT）もそこに固定。認証はCLIが環境変数から取得（`ANTHROPIC_API_KEY` または Max/Team用 `CLAUDE_CODE_OAUTH_TOKEN`）。FastAPIブリッジに差し替える場合は `run_claude_maker()` のみ置換すればよい。
- **観点**: ① CI結果アーティファクト欠落時に `__no_ci_result__` でhard failにする防御（サイレント成功の防止）。② `build_feedback`/`build_maker_prompt` がメイカーに自分の過去推論を渡さない設計（採点独立性）。③ メイカーが無変更なら `call_maker` がraiseし、空サイクルを回さず明示停止する設計が安全側か。④ `read_judge_result` の軽量バリデーションが、Judge出力を直接decisionにしない不変条件を守れているか。⑤ メイカーが編集のみ・commitはワークフロー、という分離が「LLMはgit操作・遷移を持たない」を守れているか。⑥ `--permission-mode acceptEdits` の権限範囲（任意Bashは未許可）が妥当か、CLAUDE.md非bare読み込みとの両立。

### .pdca/ci_report.py — CI結果の構造化ヘルパー
- **役割**: 各ゲートの結果を `ci_result.json` に変換。ワークフローYAMLにPythonヒアドキュメントを埋めるとインデントが壊れるため外出し（実際に壊れたので分離）。
- **観点**: ① `failing_checks` の粒度混在（テストはnode id、lintはグループid `lint:ruff`）がシグネチャ安定性に影響しないか。② `error_kinds` と `failing_checks` の使い分けが `compute_failure_signature` の意図と整合するか。

### .pdca/state.json — 初期状態シード
- **役割**: ループ状態の初期値（version=2, cycle=0, status=running）。`max_cycles`/`oscillation_threshold`/`oscillation_window`/`no_progress_threshold`/`min_quality_delta` のチューニング点。
- **観点**: 既定値（5 / 3 / 5 / 2 / 0.01）の妥当性。タスク種別ごとに変えるべきか。`min_quality_delta=0.01` が品質スケール（0–1正規化前提）と整合するか。

### .pdca/state.schema.json — 状態の契約
- **役割**: `state.json` のJSON Schema（draft 2020-12, `additionalProperties:false`, `version const 2`）。
- **追随状況**: status enum を `running / completed / replan / stopped_max / stopped_oscillation / stopped_no_progress` に更新済み（旧 `escalated` は除去）。history項目に `quality_vector`(object|null, 値は数値) と `replan_requested`(bool) を追加。top-levelに `no_progress_threshold` / `min_quality_delta` を追加。
- **観点**: ① guard が書く全statusを網羅しているか（`already_terminal` は遷移結果であって永続statusではない点の確認）。② CIでこのschemaを実際に検証する手順が無い点（手動検証は実施済だがpipeline化されていない）。

### .pdca/README.md — .pdca機構のクイックスタート
- **役割**: `.pdca/` 各ファイルの早見表、guardテスト実行手順、サイクルの流れ、2トークンの理由、失敗シグネチャの注意。
- **観点**: 記述と実コードの整合（特に2トークンの説明と replan/no-progress の追加が `pdca-act.yml` の実装と一致しているか）。※ replan/Judge 追加に伴い本ファイルの追補が必要なら指摘してほしい。

### .github/workflows/ci.yml — Check（決定論ゲート）
- **役割**: `pdca/**` への push で ruff＋pytest を走らせ、`ci_report.py` で `ci_result.json` を生成・アーティファクト化。`name: ci` は `pdca-act.yml` の `workflow_run.workflows` から参照される。
- **観点**: ① ジョブのpass/failとアーティファクトの整合。② `set +e` での結果収集に取りこぼしが無いか。③ アーティファクトretention（7日）とAct側 `download-artifact` の `run-id` 紐付け。④ Judge を将来CIで走らせる場合、`judge_result.json` をどのジョブで生成しアーティファクト化するか（現状はAct側がオプショナル読み込み）。

### .github/workflows/pdca-act.yml — Act（コントローラ）
- **役割**: `ci` 完了で発火するループの頭。state永続化commit→決定で分岐（完了=PRコメント / 継続=メイカー＋PAT push / 再計画=Issue作成（PATなし）/ 停止=エスカレーションIssue）。
- **観点（最重要）**:
  ① `concurrency`(branch単位・cancel-in-progress:false) が多重ループを直列化するか。
  ② `timeout-minutes:15` の妥当性。
  ③ **PAT再発火の安全性**（不変条件1の現物検証）— `PDCA_PAT` が continue stepのみ。
  ④ `replan` 分岐がPATを持たず、Issue作成のみのterminal挙動になっていること（不変条件5）。
  ⑤ state永続化pushの `[skip ci]` でCI誤発火を防いでいる点。
  ⑥ デフォルト `GITHUB_TOKEN` push が再帰しないGitHub仕様への依存の明示。
  ⑦ escalation条件に `stopped_no_progress` が含まれていること。

### .claude/agents/planner.md — Plan subagent（契約生成器）
- **役割**: ゴール→機械検証可能な契約（タスクDAG＋受け入れ基準＋oracle＋test_spec＋human_gate）を生成。実装はしない。`.claude/agents/` のsubagent形式。
- **観点**: ① Definition of Ready未達時に「推測で埋めず停止」する指示の強度（曖昧さ埋め込み/インジェクション耐性）。② 検証可能性トリアージ（Tier A/B/C）が実運用で機能するか。③ 出力がYAMLのみ・コードフェンス無しという制約の堅牢性。④ Tier B（ルブリック）を Judge の quality_vector に橋渡しする記述を足すべきか。

### .claude/skills/pdca-init/SKILL.md — 組み込み機能（インストーラ）
- **役割**: プロジェクト作成時に一式を導入する現行スキル形式（`/pdca-init` で手動起動、説明文マッチで自律起動。`.claude/commands/` はlegacyのためskillを採用）。導入手順・必要secret・結合点・人間ゲートを記述。
- **観点**: ① 既存ファイルがある場合に「diffして確認」とする上書き防御。② guardテストが緑でなければ先に進まないゲートが手順に明記されているか。③ description が起動トリガとして適切な粒度か（誤発火/不発火）。④ replan/Judge の追加を手順・secret説明に反映すべきか。

### CLAUDE.md — プロジェクト憲法
- **役割**: ループ思想（GitHub=制御基盤、自前=guard/router、LLMは無状態worker）、役割（Plan/Do/Check/Judge/Act）、Decision model（7決定）、Quality Vector ルール、運用ルールをエージェントに常時読ませる。
- **観点**: Decision model と `guard.py::Decision` の完全一致（検証済）。運用ルールが具体的禁止事項（テストを弱めない・新規失敗は恒久テスト化・replan/escalateの使い分け）として行動可能か。

### tests/test_guard.py — guardのoracle（自己適用）
- **役割**: 停止判定そのものを決定論テストで縛る（本設計のoracle哲学のドッグフーディング）。**16ケース、全緑確認済み**（replan優先・replan terminal冪等・品質改善継続・品質停滞→no_progress・品質データ無しでは誤発火しない、を含む）。
- **観点**: ① カバレッジの穴（振動window境界、budgetと振動が同時成立する競合、no-progress と budget の同時成立）。② `sys.path` 操作の移植性。③ 冪等性テストが二重実行を実際に防げているか。

### tests/test_act.py — Actコントローラ/メイカー結線のテスト
- **役割**: Claude Code と git をモックし、メイカー結線をオフラインで決定論検証。6ケース（プロンプトに失敗チェックが載る・no-fail時の表記・無変更でraise・成功でテレメトリ書き出し・claude非ゼロ終了でraise・Judge不正値drop）。
- **観点**: ① 実 `claude -p` を呼ばずに結線の安全性（無変更raise、非ゼロraise）を担保できているか。② プロンプトが「テストを弱めない/編集のみ/commitしない」を含むことの固定。

### pyproject.toml — 依存・ツール設定
- **役割**: 依存をバージョンpin（再現性レバー）、ruff/pytest設定。
- **観点**: pinバージョンの保守方針（Dependabot等）。

### .gitignore — 生成物除外
- **役割**: 実行時生成物（`ci_result.json`, `pytest.json`, `judge_result.json`, `last_feedback.json`, 各キャッシュ）をgit管理外に。
- **観点**: 永続物（`state.json`）と生成物の区別が正しく分かれているか。

---

## 4. ファイル目録（同梱物と完全一致）

```
.claude/agents/planner.md
.claude/skills/pdca-init/SKILL.md
.github/workflows/ci.yml
.github/workflows/pdca-act.yml
.gitignore
.pdca/README.md
.pdca/act.py
.pdca/ci_report.py
.pdca/guard.py
.pdca/state.json
.pdca/state.schema.json
CLAUDE.md
pyproject.toml
tests/test_act.py
tests/test_guard.py
```

（`REVIEW.md` と `IMPLEMENTATION.md` はメタ/オペレーション文書であり、ランタイム構成物15点には含めない。）

## 5. 今回の変更点（メイカー結線）

- `act.py::call_maker()` を **Claude Code headless** に結線。`continue` 時に `claude -p` を起動し、メイカーは**ファイル編集のみ**。git の commit/push と token選択はワークフロー層に固定。
- ワークフローを改修: `continue` は **state＋差分を1コミット**にまとめ `PDCA_PAT` でpush（head に `[skip ci]` を乗せずCI再発火を保証）。terminal は state のみを default token でpush（再発火なし）。Actステップにモデル認証情報、`PDCA_PAT` は **continue push のみ**（不変条件1維持）。
- 認証は `ANTHROPIC_API_KEY`（従量API）または `CLAUDE_CODE_OAUTH_TOKEN`（Max/Teamシート）。CLIが環境変数から取得し、コードには鍵を埋めない。
- メイカー無変更時は `call_maker` がraiseして空サイクルを回さず明示停止。
- `tests/test_act.py` を追加（claude/git をモック、6ケース）。

以前の `replan` / `quality_vector` / `stopped_no_progress` 実装、ChatGPT版ドキュメントのバグ修正は前版で反映済み。

## 6. 検証済みステータス（本版・生成時点）

- `pytest tests/` … **22 passed**（guard 16 + act 6）
- `ruff check .pdca tests` … All checks passed
- ワークフローYAML（`ci.yml`, `pdca-act.yml`）… パース成功
- `state.json`（seed）… schema検証OK（version=2, cycle=0）
- PAT露出 … `continue` step限定（grep確認、不変条件1・5）
- メイカー結線 … `build_maker_prompt`/`call_maker`/`run_claude_maker` をモックで検証（無変更raise・非ゼロraise・成功テレメトリ）

**結線済み**: `act.py::call_maker()` は Claude Code headless に接続済み。実走には secret（`PDCA_PAT` ＋ `ANTHROPIC_API_KEY` または `CLAUDE_CODE_OAUTH_TOKEN`）の登録のみが必要。
Judge結線は任意（`judge_result.json` が無ければ品質機能はスキップ）。
