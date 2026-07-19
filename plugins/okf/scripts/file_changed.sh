#!/usr/bin/env bash
# FileChanged 훅 (T-P5-3) — 감시(watchPaths) 중인 번들 파일 변경 시 대응 개념과
# 가장 가까운 log.md 갱신을 권고. matcher는 두지 않는다(감시 등록은 watchPaths 몫).
set -euo pipefail
command -v jq >/dev/null 2>&1 || exit 0
payload="$(cat)"
file="$(jq -r '.file_path // .path // empty' <<<"$payload")"
[ -n "$file" ] || exit 0
jq -n --arg ctx "번들 파일 변경 감지: $file — 대응 개념 문서를 갱신하고 가장 가까운 log.md에 일자 엔트리를 추가하라(§7)." \
  '{hookSpecificOutput:{hookEventName:"FileChanged", additionalContext:$ctx}}'
