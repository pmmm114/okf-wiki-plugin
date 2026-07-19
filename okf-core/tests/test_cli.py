"""T-P2-5 okf CLI — 완료 기준 매핑: 서브커맨드 5종 동작 + 도움말 존재.
(`uv run okf ...` 엔트리는 pyproject [project.scripts]가 cli.main을 가리키므로
여기서는 cli.main 직접 호출로 동일 경로를 검증한다.)"""
import json
import shutil
from pathlib import Path

import pytest

from okf_core.cli import main
from okf_core.validate import validate_bundle

FIXTURES = Path(__file__).parent / "fixtures"
APPENDIX_A = FIXTURES / "appendix-a"


def test_validate_subcommand(capsys):
    assert main(["validate", str(APPENDIX_A)]) == 0
    capsys.readouterr()
    assert main(["validate", str(FIXTURES / "violations"), "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert all(set(f) == {"file", "rule", "level", "msg"} for f in payload)


def test_index_subcommand(tmp_path, capsys):
    bundle = tmp_path / "bundle"
    shutil.copytree(APPENDIX_A, bundle)
    assert main(["index", str(bundle), "--write"]) == 0
    written = capsys.readouterr().out.split()
    assert "index.md" in written and "tables/index.md" in written


def test_graph_subcommand(capsys):
    assert main(["graph", str(APPENDIX_A), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"nodes", "edges"}
    assert main(["graph", str(APPENDIX_A), "--linked-to", "no-such"]) == 0
    assert capsys.readouterr().out == ""  # 무매칭이면 무출력


def test_context_subcommand(capsys):
    assert main(["context", str(APPENDIX_A), "--max-chars", "500"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("<okf-context>") and len(out) <= 501  # print 개행 1자


def test_log_append_subcommand(tmp_path, capsys):
    assert main(["log", "append", str(tmp_path), "-m", "첫 항목"]) == 0
    assert main(["log", "append", str(tmp_path), "-m", "둘째 항목"]) == 0
    text = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert text.count("## ") == 1  # 같은 날짜는 한 그룹
    assert "* **Update**: 첫 항목" in text and "* **Update**: 둘째 항목" in text
    assert validate_bundle(tmp_path) == []  # §7/§9 통과(ISO 날짜 헤딩)


def test_help_exists(capsys):
    assert main([]) == 0
    top = capsys.readouterr().out
    for cmd in ("validate", "index", "graph", "context", "log"):
        assert cmd in top
    with pytest.raises(SystemExit) as exc:
        main(["validate", "--help"])
    assert exc.value.code == 0
    assert "--strict" in capsys.readouterr().out


def test_unknown_subcommand(capsys):
    assert main(["nope"]) == 2
