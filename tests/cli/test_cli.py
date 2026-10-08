"""CLI behaviour: argument handling, plane separation, and exit codes."""

import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
import respx
from typer.testing import CliRunner

from demand_radar.cli import app
from demand_radar.data_plane.storage import (
    ClassificationRepository,
    Database,
    SignalRepository,
)

runner = CliRunner()

HN_API = "https://hn.algolia.com/api/v1/search_by_date"

HYPOTHESIS_YAML = """
name: h1
statement: B2B professionals who cannot negotiate in a non-native language.
queries: [negotiating]
sources: [hackernews]
relevance_criteria: the author describes a work conversation in a second language
"""

RELEVANT_TEXT = "I freeze when the buyer pushes back in German and cannot answer quickly enough."


@pytest.fixture
def hypothesis_file(tmp_path):
    path = tmp_path / "h1.yaml"
    path.write_text(HYPOTHESIS_YAML)
    return path


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "radar.db")


def _hn_payload(*texts, timestamp=None):
    # Relative to real "now": the CLI resolves --period against the system clock,
    # so a fixed fixture date would fall outside the window and be filtered out.
    created = int((timestamp or (datetime.now(timezone.utc) - timedelta(days=2))).timestamp())
    return {
        "hits": [
            {
                "objectID": str(index),
                "title": "negotiating",
                "comment_text": text,
                "author": f"author{index}",
                "created_at_i": created,
            }
            for index, text in enumerate(texts)
        ],
        "page": 0,
        "nbPages": 1,
    }


def _invoke(*args):
    return runner.invoke(app, list(args))


def test_version_is_reported():
    result = _invoke("--version")
    assert result.exit_code == 0
    assert "demand-radar" in result.stdout


def test_sources_lists_collectors_and_their_config_needs():
    result = _invoke("sources")
    assert result.exit_code == 0
    assert "hackernews" in result.stdout
    assert "reddit" in result.stdout


def test_help_lists_the_plane_commands():
    result = _invoke("--help")
    for command in ("collect", "screen", "run", "report", "signals"):
        assert command in result.stdout


def test_a_missing_hypothesis_file_exits_with_a_clear_error(db_path):
    result = _invoke("collect", "nope.yaml", "--db", db_path)
    assert result.exit_code == 2
    assert "No such hypothesis file" in result.stdout


def test_an_invalid_hypothesis_file_exits_with_a_clear_error(tmp_path, db_path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: only-a-name\nstatement: s\n")  # no queries or sources
    result = _invoke("collect", str(path), "--db", db_path)
    assert result.exit_code == 2
    assert "Invalid hypothesis file" in result.stdout


def test_an_invalid_period_exits_with_a_clear_error(hypothesis_file, db_path):
    result = _invoke("collect", str(hypothesis_file), "--period", "lastweek", "--db", db_path)
    assert result.exit_code == 2
    assert "Invalid period" in result.stdout


@respx.mock
def test_collect_stores_signals_without_classifying_them(hypothesis_file, db_path):
    """collect runs the Data Plane only — no verdicts should appear."""
    respx.get(HN_API).mock(return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT)))
    result = _invoke("collect", str(hypothesis_file), "--period", "30d", "--db", db_path)

    assert result.exit_code == 0
    assert "Collected" in result.stdout
    with Database(db_path) as database:
        assert SignalRepository(database).count() == 1
        assert ClassificationRepository(database).list("h1") == []


@respx.mock
def test_collect_is_safe_to_rerun(hypothesis_file, db_path):
    respx.get(HN_API).mock(return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT)))
    _invoke("collect", str(hypothesis_file), "--period", "30d", "--db", db_path)
    result = _invoke("collect", str(hypothesis_file), "--period", "30d", "--db", db_path)

    assert result.exit_code == 0
    with Database(db_path) as database:
        assert SignalRepository(database).count() == 1
    assert "0 new" in result.stdout


@respx.mock
def test_collect_reports_a_source_failure_without_crashing(hypothesis_file, db_path):
    respx.get(HN_API).mock(return_value=__import__("httpx").Response(500))
    result = _invoke("collect", str(hypothesis_file), "--period", "30d", "--db", db_path)
    assert result.exit_code == 0
    assert "hackernews" in result.stdout


@respx.mock
def test_screen_without_a_key_warns_and_uses_the_local_filter(hypothesis_file, db_path, monkeypatch):
    for _var in ("GEMINI_API_KEY", "TYPESAFE_JEV_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(_var, raising=False)
    respx.get(HN_API).mock(return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT)))
    _invoke("collect", str(hypothesis_file), "--period", "30d", "--db", db_path)

    result = _invoke("screen", str(hypothesis_file), "--period", "30d", "--db", db_path)
    assert result.exit_code == 0
    assert "GEMINI_API_KEY" in result.stdout
    with Database(db_path) as database:
        stored = ClassificationRepository(database).list("h1")
        assert len(stored) == 1
        assert stored[0].relevance.stage == "cheap"


def test_screen_does_not_fetch_anything(hypothesis_file, db_path, monkeypatch):
    """The Intelligence Plane must work entirely off stored data."""
    for _var in ("GEMINI_API_KEY", "TYPESAFE_JEV_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(_var, raising=False)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(HN_API)
        result = _invoke("screen", str(hypothesis_file), "--period", "30d", "--db", db_path)
        assert not route.called
    assert result.exit_code == 0


@respx.mock
def test_run_collects_screens_and_reports_in_one_pass(hypothesis_file, db_path, monkeypatch):
    for _var in ("GEMINI_API_KEY", "TYPESAFE_JEV_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(_var, raising=False)
    respx.get(HN_API).mock(
        return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT, "short"))
    )
    result = _invoke("run", str(hypothesis_file), "--period", "30d", "--db", db_path)

    assert result.exit_code == 0
    assert "Collected" in result.stdout
    assert "Screening stages" in result.stdout
    assert "local filter" in result.stdout
    assert "relevant signals" in result.stdout


@respx.mock
def test_report_renders_the_research_view(hypothesis_file, db_path, monkeypatch):
    for _var in ("GEMINI_API_KEY", "TYPESAFE_JEV_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(_var, raising=False)
    respx.get(HN_API).mock(return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT)))
    _invoke("run", str(hypothesis_file), "--period", "30d", "--db", db_path)

    result = _invoke("report", str(hypothesis_file), "--period", "30d", "--db", db_path)
    assert result.exit_code == 0
    assert "h1" in result.stdout
    assert "relevant signals" in result.stdout


def test_report_without_data_exits_nonzero(hypothesis_file, db_path):
    result = _invoke("report", str(hypothesis_file), "--period", "30d", "--db", db_path)
    assert result.exit_code == 1
    assert "No screened signals" in result.stdout


def test_report_rejects_an_unknown_format(hypothesis_file, db_path):
    result = _invoke("report", str(hypothesis_file), "--format", "xml", "--db", db_path)
    assert result.exit_code == 2
    assert "Unknown format" in result.stdout


@respx.mock
def test_report_exports_json_to_a_file(hypothesis_file, db_path, tmp_path, monkeypatch):
    for _var in ("GEMINI_API_KEY", "TYPESAFE_JEV_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(_var, raising=False)
    respx.get(HN_API).mock(return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT)))
    _invoke("run", str(hypothesis_file), "--period", "30d", "--db", db_path)

    out = tmp_path / "report.json"
    result = _invoke(
        "report", str(hypothesis_file), "--period", "30d", "--format", "json",
        "--out", str(out), "--db", db_path,
    )
    assert result.exit_code == 0
    payload = json.loads(out.read_text())
    assert payload["hypothesis"] == "h1"
    assert payload["relevant_signals"] == 1
    assert "by_pain" in payload


@respx.mock
def test_report_exports_csv_with_the_original_text(hypothesis_file, db_path, tmp_path, monkeypatch):
    for _var in ("GEMINI_API_KEY", "TYPESAFE_JEV_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(_var, raising=False)
    respx.get(HN_API).mock(return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT)))
    _invoke("run", str(hypothesis_file), "--period", "30d", "--db", db_path)

    out = tmp_path / "signals.csv"
    result = _invoke(
        "report", str(hypothesis_file), "--period", "30d", "--format", "csv",
        "--out", str(out), "--db", db_path,
    )
    assert result.exit_code == 0
    rows = list(csv.DictReader(io.StringIO(out.read_text())))
    assert len(rows) == 1
    assert "I freeze when the buyer pushes back" in rows[0]["text"]
    assert rows[0]["signal_id"]


@respx.mock
def test_report_exports_per_signal_json(hypothesis_file, db_path, tmp_path, monkeypatch):
    for _var in ("GEMINI_API_KEY", "TYPESAFE_JEV_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(_var, raising=False)
    respx.get(HN_API).mock(return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT)))
    _invoke("run", str(hypothesis_file), "--period", "30d", "--db", db_path)

    out = tmp_path / "signals.json"
    _invoke(
        "report", str(hypothesis_file), "--period", "30d", "--format", "signals-json",
        "--out", str(out), "--db", db_path,
    )
    rows = json.loads(out.read_text())
    assert isinstance(rows, list)
    assert rows[0]["relevant"] is True


def test_signals_reports_an_empty_dataset(db_path):
    result = _invoke("signals", "--db", db_path)
    assert result.exit_code == 0
    assert "No signals collected yet" in result.stdout


@respx.mock
def test_signals_summarises_what_was_collected(hypothesis_file, db_path):
    respx.get(HN_API).mock(
        return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT, RELEVANT_TEXT + " two"))
    )
    _invoke("collect", str(hypothesis_file), "--period", "30d", "--db", db_path)

    result = _invoke("signals", "--db", db_path)
    assert result.exit_code == 0
    assert "signals collected" in result.stdout
    assert "hackernews" in result.stdout


@respx.mock
def test_collected_data_is_reusable_by_a_second_hypothesis(tmp_path, db_path, monkeypatch):
    """The core architectural promise: one dataset, many hypotheses, no refetching."""
    for _var in ("GEMINI_API_KEY", "TYPESAFE_JEV_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(_var, raising=False)
    first = tmp_path / "h1.yaml"
    first.write_text(HYPOTHESIS_YAML)
    route = respx.get(HN_API).mock(
        return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT))
    )
    _invoke("run", str(first), "--period", "30d", "--db", db_path)
    calls_after_first = route.call_count

    second = tmp_path / "h2.yaml"
    second.write_text(HYPOTHESIS_YAML.replace("name: h1", "name: h2"))
    result = _invoke("screen", str(second), "--period", "30d", "--db", db_path)

    assert result.exit_code == 0
    assert route.call_count == calls_after_first  # nothing was refetched
    with Database(db_path) as database:
        classifications = ClassificationRepository(database)
        assert sorted(classifications.hypotheses()) == ["h1", "h2"]
        assert SignalRepository(database).count() == 1


@respx.mock
def test_reclassify_rescreens_the_window(hypothesis_file, db_path, monkeypatch):
    for _var in ("GEMINI_API_KEY", "TYPESAFE_JEV_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(_var, raising=False)
    respx.get(HN_API).mock(return_value=__import__("httpx").Response(200, json=_hn_payload(RELEVANT_TEXT)))
    _invoke("run", str(hypothesis_file), "--period", "30d", "--db", db_path)

    plain = _invoke("screen", str(hypothesis_file), "--period", "30d", "--db", db_path)
    assert "Nothing new to screen" in plain.stdout

    again = _invoke(
        "screen", str(hypothesis_file), "--period", "30d", "--reclassify", "--db", db_path
    )
    assert "Screening stages" in again.stdout
    assert "Relevant:" in again.stdout
