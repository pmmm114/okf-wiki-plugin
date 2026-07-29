"""study 후보 적재·해소의 결정적 판정 (#305).

세 지점에서 존재 검사·구조 검사 부재가 후보를 어긋나게 한다.

1. `resolve --id`에 존재 검사가 **전무**했다 — 오타·환각 id가 exit 0으로 원장·저널에
   `promoted`로 기록된다. 진짜 후보는 인박스에 남고 doctor 이력은 거짓이 된다.
   같은 커맨드의 `--source` 경로는 무매칭 시 exit 1로 실패하므로 **비대칭**이었다.
2. 선두 `---`를 **위치만으로** frontmatter로 단정했다 — 안쪽이 YAML 매핑인지 보지 않는다.
3. 레거시 메모리 정규식에 앵커가 없어 repo 안 무관 경로가 메모리로 인정된다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import study
import study_blocks
import study_inbox
import study_scope

# --- 1. resolve --id 존재 검사 -------------------------------------------------


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    (project / ".okf-study").mkdir(parents=True)
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review"}}), encoding="utf-8"
    )
    return project


def _seed(project: Path, text: str = "실재하는 사실 하나.") -> str:
    source = project / "MEMORY.md"
    source.write_text(f"- {text}\n", encoding="utf-8")
    scope = study_scope.resolve_capture(project)
    return study_inbox.append(scope["runtime_root"], text, str(source))


def _resolve(project: Path, *ids: str, status: str = "promoted") -> int:
    argv = ["resolve", str(project), "--status", status, "--ref", "x.md"]
    for ident in ids:
        argv += ["--id", ident]
    return study.main(argv)


def test_unknown_id_fails_and_records_nothing(runtime, capsys):
    """미존재 id는 exit 1이고 원장·저널에 기록이 **0건**이다."""
    project = runtime
    _seed(project)
    scope = study_scope.resolve_capture(project)
    before = len(study_inbox.list_candidates(scope["runtime_root"]))

    rc = _resolve(project, "존재하지-않는-id")
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"], out
    assert out["unknown_ids"] == ["존재하지-않는-id"], out

    after = study_inbox.list_candidates(scope["runtime_root"])
    assert len(after) == before, "실패했는데 후보가 사라졌다"


def test_partial_ids_are_all_or_nothing(runtime, capsys):
    """일부만 존재하면 **존재분도 기록되지 않는다** — 반쪽 적용 금지."""
    project = runtime
    known = _seed(project)
    scope = study_scope.resolve_capture(project)

    rc = _resolve(project, known, "없는-id")
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["unknown_ids"] == ["없는-id"], out
    assert known in out["known_ids"], out

    remaining = {c["id"] for c in study_inbox.list_candidates(scope["runtime_root"])}
    assert known in remaining, "반쪽 적용됐다 — 존재분이 드레인됐다"


def test_known_id_still_resolves(runtime, capsys):
    """정상 경로 회귀 방지 — 존재하는 id는 그대로 드레인된다."""
    project = runtime
    known = _seed(project)
    assert _resolve(project, known) == 0
    capsys.readouterr()
    scope = study_scope.resolve_capture(project)
    assert known not in {c["id"] for c in study_inbox.list_candidates(scope["runtime_root"])}


def test_store_helper_is_actually_used():
    """`has_candidate`가 존재하는데 안 쓰이던 상태로 되돌아가지 않게."""
    src = Path(study.__file__).read_text(encoding="utf-8")
    assert "has_candidate" in src, "존재 검사 헬퍼를 쓰지 않는다"


# --- 2. 선두 `---` 오판 (이슈의 실측 표 4행 그대로) ----------------------------

FRONTMATTER_CASES = [
    pytest.param(
        "---\n첫 구간의 사실.\n\n둘째 블록.\n",
        ["첫 구간의 사실."],
        id="선두---닫는펜스없음",  # 원래도 정상 — 보수적으로 전체를 본문으로 남긴다
    ),
    pytest.param(
        "---\n첫 구간의 사실.\n\n---\n\n둘째 블록.\n",
        ["첫 구간의 사실."],
        id="선두---뒤에또---",  # 수평선 구분자 — 지금은 첫 구간이 소실된다
    ),
    pytest.param(
        "---\n첫 구간의 사실.\n\n...\n\n둘째 블록.\n",
        ["첫 구간의 사실."],
        id="선두---뒤에...",  # 지금은 첫 구간이 소실된다
    ),
    pytest.param(
        "---\ntype: memory\ntitle: 제목\n---\n본문 사실.\n",
        ["본문 사실."],
        id="진짜frontmatter",  # 의도된 스킵 — 회귀 대상
    ),
]


@pytest.mark.parametrize(("text", "expected_first"), FRONTMATTER_CASES)
def test_leading_fence_only_skips_real_frontmatter(text, expected_first):
    """펜스 안쪽이 **YAML 매핑꼴**일 때만 frontmatter로 스킵한다."""
    blocks = study_blocks.concept_blocks(text)
    assert blocks, f"블록이 통째로 사라졌다: {text!r}"
    assert blocks[0] == expected_first, blocks


def test_block_splitting_unchanged_for_real_frontmatter():
    """진짜 frontmatter 문서의 **블록 분할**이 무변경 — id 계산의 입력이 그대로다.

    블록 경계가 바뀌면 id가 바뀌고 기존 인박스·원장 dedup과 어긋난다. 펜스 판정만
    정밀화하고 분할은 건드리지 않았음을 고정한다.
    """
    text = "---\ntype: memory\n---\n사실 A.\n\n사실 B.\n"
    assert study_blocks.concept_blocks(text) == [["사실 A."], ["사실 B."]]


# --- 3. 레거시 메모리 정규식 앵커 ----------------------------------------------


def test_repo_internal_lookalike_is_not_memory(tmp_path, monkeypatch):
    """repo 안 `docs/.claude/projects/x/memory/y.md`는 메모리가 아니다."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    project = tmp_path / "repo"
    bogus = project / "docs" / ".claude" / "projects" / "x" / "memory" / "y.md"
    bogus.parent.mkdir(parents=True)
    bogus.write_text("- 무관한 파일.\n", encoding="utf-8")
    assert study_scope.is_memory_path(str(bogus), {}, project) is False


def test_home_memory_path_is_still_memory(tmp_path, monkeypatch):
    """홈 아래 정상 메모리 경로는 계속 인정된다 — 무회귀."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    real = home / ".claude" / "projects" / "proj" / "memory" / "m.md"
    real.parent.mkdir(parents=True)
    real.write_text("- 사실.\n", encoding="utf-8")
    assert study_scope.is_memory_path(str(real), {}, tmp_path / "repo") is True


def test_config_dir_override_is_still_memory(tmp_path, monkeypatch):
    """`CLAUDE_CONFIG_DIR` 하위도 인정된다."""
    config = tmp_path / "custom-config"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    real = config / "projects" / "proj" / "memory" / "m.md"
    real.parent.mkdir(parents=True)
    real.write_text("- 사실.\n", encoding="utf-8")
    assert study_scope.is_memory_path(str(real), {}, tmp_path / "repo") is True
