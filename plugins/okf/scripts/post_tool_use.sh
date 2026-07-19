#!/usr/bin/env bash
# PostToolUse 훅 (T-P5-3, matcher: Write|Edit) — 수정 파일로 들어오는 역링크
# 개념을 okf graph --linked-to로 조회해 제안. 무관 파일이면 무출력 exit 0.
set -euo pipefail
project="${CLAUDE_PROJECT_DIR:-$PWD}"
config="$project/.okf-wiki.json"
[ -f "$config" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

payload="$(cat)"
file="$(jq -r '.tool_input.file_path // empty' <<<"$payload")"
[ -n "$file" ] || exit 0
bundle_rel="$(jq -r '.bundlePath // ".okf"' "$config")"
bundle="$project/$bundle_rel"
[ -d "$bundle" ] || exit 0
rel="${file#"$bundle"/}"   # 번들 밖 파일이면 원문 그대로 → 무매칭 무출력
[ "$rel" != "$file" ] || exit 0

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
links="$("$here/../bin/okf" graph "$bundle" --linked-to "$rel" 2>/dev/null || true)"
[ -n "$links" ] || exit 0

jq -n --arg ctx "수정한 번들 파일($rel)로 링크하는 개념: $(echo "$links" | tr '\n' ' ')— 관련 개념과 log.md 갱신 필요 여부를 검토하라." \
  '{hookSpecificOutput:{hookEventName:"PostToolUse", additionalContext:$ctx}}'
