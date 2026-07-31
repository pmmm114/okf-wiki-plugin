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


def test_census_subcommand(capsys):
    assert main(["census", str(APPENDIX_A), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"bundle", "fields", "axes", "dirs"}
    # 판정이 없으므로 §9 탈락 번들에서도 0
    assert main(["census", str(FIXTURES / "violations")]) == 0


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
    for cmd in ("validate", "index", "graph", "context", "census", "log", "init"):
        assert cmd in top
    with pytest.raises(SystemExit) as exc:
        main(["validate", "--help"])
    assert exc.value.code == 0
    assert "--strict" in capsys.readouterr().out


def test_unknown_subcommand(capsys):
    assert main(["nope"]) == 2


def test_context_group_by_multivalue_degrades_exit0(tmp_path, capsys):
    """다중값 축 `--group-by`: exit 0 + stdout은 무섹션 본문, 경고는 stderr(#329).

    훅이 stdout을 그대로 컨텍스트에 주입하고 엔진 실패를 exit 0으로 흡수하므로,
    경고가 stdout에 섞여도 비-0 종료여도 주입이 오염되거나 전무가 된다.
    """
    bundle = tmp_path / "b"
    bundle.mkdir()
    (bundle / "a.md").write_text(
        "---\ntype: Note\ndescription: d.\ntags:\n  - x\n  - y\n---\n\n# a\n", encoding="utf-8"
    )
    assert main(["context", str(bundle), "--group-by", "tags"]) == 0
    cap = capsys.readouterr()
    assert cap.out.startswith("<okf-context>")
    assert not any(ln.startswith("## ") for ln in cap.out.split("\n")), cap.out
    assert "경고" in cap.err and "tags" in cap.err
