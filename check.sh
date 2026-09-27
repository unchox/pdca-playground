#!/bin/sh
# check.sh — pdca-playground の合否を終了コード 1 個に畳む判定器 (2026-09-14 設置)。
#
# 用途: ループを回す時の唯一の合否判定。緑 = 0 / 赤 = 非 0。
#
# 実行環境: **repo の .venv を優先し、無ければ PATH にフォールバックする。**
#   2026-09-14 の設置時は「.venv を持たず pyenv の python3 / ruff を使う」前提だったが、
#   2026-09-24 の Mac 移行で pyenv を入れない方針になり、かわりに .venv が作られた。
#   前提が環境に依存していたため、**新 Mac では ruff が PATH に無く判定器が赤になった**
#   (コードは健全なのに判定できない)。判定器は自分で環境を解決すること。
#   → [[feedback-noninteractive-ssh-context-assumptions]]
#
# なぜテスト件数の下限を見るか:
#   収集段階で落ちたテストは「失敗」ではなく「存在しない」になる。件数を見ないと
#   テストファイルが丸ごと消えても "N passed" と表示されて緑のまま通る。
#   → [[feedback-undiscovered-tests-are-not-counted-as-failures]]
#
# 参考: リポジトリ内の pytest_output.txt は 41 passed で止まった古い成果物。
#       実測は 99 件。**過去の出力ファイルを現在の事実として読まないこと。**
#       → [[feedback-open-item-lists-go-stale]]

set -eu
cd "$(dirname "$0")"

EXPECTED_TESTS=99

fail() { echo "NG: $*" >&2; exit 1; }

# repo の .venv があればそれを使う (版が pyproject で固定されているのはこちら)
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
  RUFF=.venv/bin/ruff
else
  PY=python3
  RUFF=ruff
fi
command -v "$PY"   >/dev/null 2>&1 || [ -x "$PY" ]   || fail "python が無い ($PY)"
command -v "$RUFF" >/dev/null 2>&1 || [ -x "$RUFF" ] || fail "ruff が無い ($RUFF)"

# --- 1) テスト -------------------------------------------------------------
echo "== pytest =="
if ! out=$("$PY" -m pytest -q 2>&1); then
  printf '%s\n' "$out"
  fail "pytest が赤"
fi
printf '%s\n' "$out" | tail -2

n=$(printf '%s\n' "$out" | grep -oE '[0-9]+ passed' | tail -1 | cut -d' ' -f1)
[ -n "${n:-}" ] || fail "pytest の件数を読み取れなかった (出力形式が変わった可能性)"
[ "$n" -ge "$EXPECTED_TESTS" ] || \
  fail "テスト件数が減っている: $n < $EXPECTED_TESTS (収集から落ちたテストがある)"

# --- 2) lint ---------------------------------------------------------------
echo "== ruff check =="
"$RUFF" check . || fail "ruff check が赤"

echo "OK: pytest ${n} passed (>= ${EXPECTED_TESTS}) / ruff clean"
