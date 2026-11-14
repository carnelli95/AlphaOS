"""run_daily_pipeline이 Stage D 뉴스를 호출하지 않는지 확인 (DECISIONS.md 2026-11-15).

Stage 모듈·QA 리포트는 가짜로 바꿔 DB·외부 API를 쓰지 않는다.
"""

import sys
import types
from unittest import mock

from app import tasks


def test_daily_pipeline_skips_stage_d_news():
    called = []

    def fake_import(name):
        mod = types.SimpleNamespace(run=lambda **kw: called.append(name) or {})
        return mod

    fake_qa = types.SimpleNamespace(generate=lambda: {"summary": {"issue_count": 0}})
    fake_sr = types.SimpleNamespace(now_kst=lambda: None, write_stage_result=lambda *a, **k: None)
    with mock.patch.dict(sys.modules, {"generate_qa_report": fake_qa, "stage_result": fake_sr}), \
         mock.patch("importlib.import_module", side_effect=fake_import):
        result = tasks.run_daily_pipeline.run()

    assert "batch_generate_stage_d_news" not in called
    assert called == ["batch_generate_stage_c", "batch_generate_stage_d", "batch_generate_thesis"]
    assert result.startswith("파이프라인 완료")
