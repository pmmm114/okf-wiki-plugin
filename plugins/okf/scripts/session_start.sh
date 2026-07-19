#!/usr/bin/env bash
# SessionStart 훅 (T-P5-3) — .okf-wiki.json이 가리키는 번들의 압축 컨텍스트 주입.
# 프로젝트 설정은 이 파일 하나만 읽는다(T-P5-5). 설정·번들 부재 시 즉시 exit 0
# (fail-fast) — resume 재실행에도 멱등하다.
set -euo pipefail
project="${CLAUDE_PROJECT_DIR:-$PWD}"
config="$project/.okf-wiki.json"
[ -f "$config" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

[ "$(jq -r 'if .inject == false then "off" else "on" end' "$config")" = "on" ] || exit 0
bundle_rel="$(jq -r '.bundlePath // ".okf"' "$config")"
bundle="$project/$bundle_rel"
[ -d "$bundle" ] || exit 0
max_chars="$(jq -r '.context.maxChars // 8000' "$config")"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ctx="$("$here/../bin/okf" context "$bundle" --max-chars "$max_chars")" || exit 0

find "$bundle" -type f -name '*.md' | jq -R . | jq -s \
  --arg ctx "$ctx" \
  '{hookSpecificOutput:{hookEventName:"SessionStart", additionalContext:$ctx, watchPaths:.}}'
