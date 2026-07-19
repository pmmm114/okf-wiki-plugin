"""T-B1 okf init — 완료 기준 매핑: 산출물이 `okf validate --strict`를 통과한다."""

from okf_core.cli import main
from okf_core.validate import validate_bundle


def test_init_creates_strict_conformant_bundle(tmp_path, capsys):
    target = tmp_path / "bundle"
    assert main(["init", str(target)]) == 0
    assert capsys.readouterr().out.split() == ["index.md", "log.md"]
    assert validate_bundle(target, strict=True) == []  # 완료 기준
    index_text = (target / "index.md").read_text(encoding="utf-8")
    assert index_text.startswith('---\nokf_version: "0.1"\n---\n')  # §11
    assert "* **Initialization**:" in (target / "log.md").read_text(encoding="utf-8")  # §7


def test_init_refuses_nonempty_target(tmp_path, capsys):
    (tmp_path / "stale.md").write_text("x", encoding="utf-8")
    assert main(["init", str(tmp_path)]) == 2
    assert not (tmp_path / "index.md").exists()  # 부분 생성 없음


def test_init_refuses_existing_bundle(tmp_path, capsys):
    target = tmp_path / "b"
    assert main(["init", str(target)]) == 0
    capsys.readouterr()
    assert main(["init", str(target)]) == 2
