"""okf CLI (T-P2-5) — 서브커맨드 5종을 각 모듈의 main으로 위임한다.

  okf validate <path> [--strict] [--format json]
  okf index    <path> [--write]
  okf graph    <path> --json [--linked-to P]
  okf context  <path> [--max-chars N]
  okf log      append <path> -m MSG

종료코드 계약은 각 서브커맨드가 따른다(F-3: 0 정상/컨포먼트, 1 비컨포먼트,
2 실행 오류).
"""
from __future__ import annotations

import sys

from okf_core import context, graph, index, logmd, validate

_COMMANDS = {
    "validate": validate.main,
    "index": index.main,
    "graph": graph.main,
    "context": context.main,
    "log": logmd.main,
}

_USAGE = """\
사용법: okf <command> ...

  validate <path> [--strict] [--format json]  §9 컨포먼스 검사
  index    <path> [--write]                   §6 형식 index.md 재생성
  graph    <path> --json [--linked-to P]      링크 그래프·역링크 조회
  context  <path> [--max-chars N]             주입용 압축 인덱스
  log      append <path> -m MSG               log.md 항목 추가(§7)

각 서브커맨드의 도움말: okf <command> --help"""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
        return 0
    command, rest = argv[0], argv[1:]
    handler = _COMMANDS.get(command)
    if handler is None:
        print(f"오류: 알 수 없는 서브커맨드 `{command}`\n\n{_USAGE}", file=sys.stderr)
        return 2
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
