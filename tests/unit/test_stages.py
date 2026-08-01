from __future__ import annotations

from lispmdoc.stages import StageKey, StageRunner


def _key(index: int) -> StageKey:
    return StageKey("ocr", "a" * 64, index, "b" * 64, "c" * 64, ("engine:1",))


def test_stage_runner_is_deterministic_across_worker_counts_and_resumes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls: list[int] = []

    def build(key: StageKey) -> tuple[str, ...]:
        calls.append(key.source_page_index)
        return (f"{key.source_page_index:064x}",)

    keys = (_key(2), _key(0), _key(1))
    first = StageRunner(tmp_path / "one").run(keys, build, jobs=1)
    second = StageRunner(tmp_path / "many").run(keys, build, jobs=3)
    assert [result.to_dict() for result in first] == [result.to_dict() for result in second]
    before = len(calls)
    resumed = StageRunner(tmp_path / "one").run(keys, build, jobs=2)
    assert [result.to_dict() for result in resumed] == [result.to_dict() for result in first]
    assert len(calls) == before


def test_stage_runner_records_failure_for_recovery(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def fail(_: StageKey) -> tuple[str, ...]:
        raise RuntimeError("bad")

    result = StageRunner(tmp_path).run((_key(0),), fail)[0]
    assert result.status == "failed"
    assert result.error == "RuntimeError: bad"


def test_stage_runner_rejects_non_digest_output_as_a_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = StageRunner(tmp_path).run((_key(0),), lambda _: ("not-a-digest",))[0]
    assert result.status == "failed"
    assert result.error is not None
