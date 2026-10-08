"""The Demand Radar command line.

Commands map onto the two planes rather than hiding them:

* ``collect`` runs the Data Plane only — fetch, normalise, deduplicate, store.
* ``screen`` runs the Intelligence Plane only — over data already collected.
* ``run`` does both, which is what a researcher usually wants.
* ``report`` reads stored results and never fetches or classifies anything.

Because the planes are separate, ``screen`` and ``report`` can be re-run against
a new hypothesis without touching the network.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from demand_radar import __version__
from demand_radar.cli.rendering import (
    console,
    render_collection,
    render_demand_report,
    render_screening,
)
from demand_radar.config import Config
from demand_radar.data_plane import CollectionService
from demand_radar.data_plane.collectors import REGISTRY
from demand_radar.data_plane.storage import ClassificationRepository, Database, SignalRepository
from demand_radar.domain import Hypothesis, TimeWindow
from demand_radar.intelligence import ScreeningService
from demand_radar.intelligence.export import report_to_json, signals_to_csv, signals_to_json
from demand_radar.intelligence.reporting import build_report

app = typer.Typer(
    help="Demand Radar — find where real user needs appear in public conversations.",
    no_args_is_help=True,
    add_completion=False,
)

DEFAULT_PERIOD = "6m"


def _version(value: bool):
    if value:
        console.print(f"demand-radar {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    _version_flag: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Show version."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", help="Log pipeline progress (per-signal decisions)."
    ),
):
    # --verbose is the dev view: one line per decision, showing which stage
    # decided, how certain it was, and how long it took.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s" if verbose else "%(levelname)s %(name)s %(message)s",
    )
    if verbose:
        # Keep transport chatter out of the dev view. The SDK ships its own
        # httpx2/httpcore2 fork, so both spellings are silenced.
        for noisy in (
            "httpx", "httpx2", "httpcore", "httpcore2", "hpack", "h2",
            "typesafe_sdk", "urllib3",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def _load_hypothesis(path: Path) -> Hypothesis:
    try:
        return Hypothesis.from_yaml(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        console.print(f"[red]No such hypothesis file:[/red] {path}")
        raise typer.Exit(code=2) from None
    except ValueError as exc:
        console.print(f"[red]Invalid hypothesis file:[/red] {exc}")
        raise typer.Exit(code=2) from None


def _window(period: str) -> TimeWindow:
    try:
        return TimeWindow.of_period(period)
    except ValueError as exc:
        console.print(f"[red]Invalid period:[/red] {exc}")
        raise typer.Exit(code=2) from None


def _config(db: str | None) -> Config:
    config = Config()
    if db:
        config.db_path = db
    return config


@app.command()
def sources():
    """List available source collectors and whether they need configuration."""
    from rich.table import Table

    table = Table("Source", "Name", "Configuration", title="Source collectors")
    for name, collector in sorted(REGISTRY.items()):
        table.add_row(
            collector.label,
            name,
            "[yellow]required[/yellow]" if collector.needs_config else "[green]none[/green]",
        )
    console.print(table)


@app.command()
def collect(
    hypothesis_file: Path = typer.Argument(..., help="Path to a hypothesis YAML file."),
    period: str = typer.Option(DEFAULT_PERIOD, help="Window to research: today, 7d, 6m, 1y."),
    pages: int = typer.Option(None, min=1, max=20, help="Max pages per query and source."),
    db: str = typer.Option(None, help="Database path (default: demand-radar.db)."),
):
    """Collect signals only — the Data Plane. Safe to re-run; duplicates collapse."""
    hypothesis = _load_hypothesis(hypothesis_file)
    window = _window(period)
    console.print(
        f"Collecting for [bold]{hypothesis.name}[/bold] across "
        f"{', '.join(hypothesis.sources)} · {len(hypothesis.queries)} queries · "
        f"{window.start.date()} → {window.end.date()}"
    )
    with CollectionService(_config(db)) as service:
        render_collection(service.collect(hypothesis, window, max_pages=pages))


@app.command()
def screen(
    hypothesis_file: Path = typer.Argument(..., help="Path to a hypothesis YAML file."),
    period: str = typer.Option(DEFAULT_PERIOD, help="Window to research: today, 7d, 6m, 1y."),
    limit: int = typer.Option(None, min=1, help="Max signals to screen in this pass."),
    reclassify: bool = typer.Option(
        False, help="Drop this hypothesis's prior verdicts and screen the window again."
    ),
    db: str = typer.Option(None, help="Database path (default: demand-radar.db)."),
):
    """Screen already-collected signals — the Intelligence Plane. No network fetching."""
    hypothesis = _load_hypothesis(hypothesis_file)
    window = _window(period)
    config = _config(db)
    if not config.classification_available:
        console.print(
            "[yellow]No GEMINI_API_KEY set — using the local filter only. "
            "Verdicts will be recorded with stage=cheap.[/yellow]"
        )
    with ScreeningService(config) as service:
        render_screening(
            service.screen(hypothesis, window, limit=limit, reclassify=reclassify)
        )


@app.command()
def run(
    hypothesis_file: Path = typer.Argument(..., help="Path to a hypothesis YAML file."),
    period: str = typer.Option(DEFAULT_PERIOD, help="Window to research: today, 7d, 6m, 1y."),
    pages: int = typer.Option(None, min=1, max=20, help="Max pages per query and source."),
    db: str = typer.Option(None, help="Database path (default: demand-radar.db)."),
):
    """Collect, screen, and report in one pass."""
    hypothesis = _load_hypothesis(hypothesis_file)
    window = _window(period)
    config = _config(db)

    with Database(config.db_path) as database:
        collection = CollectionService(config, database)
        render_collection(collection.collect(hypothesis, window, max_pages=pages))

        screening = ScreeningService(config, database)
        screening_report = screening.screen(hypothesis, window)
        render_screening(screening_report)

        pairs = ClassificationRepository(database).list_with_signals(
            hypothesis.name, since=window.start, until=window.end
        )
        render_demand_report(
            build_report(
                hypothesis.name, window, pairs, total_signals=screening_report.examined or len(pairs)
            )
        )


@app.command()
def report(
    hypothesis_file: Path = typer.Argument(..., help="Path to a hypothesis YAML file."),
    period: str = typer.Option(DEFAULT_PERIOD, help="Window to report on: today, 7d, 6m, 1y."),
    out: Path = typer.Option(None, help="Write output to a file instead of the terminal."),
    output_format: str = typer.Option(
        "table",
        "--format",
        help="table | json (aggregate view) | csv | signals-json (one row per signal).",
    ),
    db: str = typer.Option(None, help="Database path (default: demand-radar.db)."),
):
    """Report on stored results. Reads only — never fetches or classifies."""
    hypothesis = _load_hypothesis(hypothesis_file)
    window = _window(period)
    chosen_format = output_format.strip().lower()
    formats = {"table", "json", "csv", "signals-json"}
    if chosen_format not in formats:
        console.print(f"[red]Unknown format:[/red] {output_format} ({' | '.join(sorted(formats))})")
        raise typer.Exit(code=2)

    with Database(_config(db).db_path) as database:
        classifications = ClassificationRepository(database)
        pairs = classifications.list_with_signals(
            hypothesis.name, since=window.start, until=window.end
        )
        screened = len(
            classifications.list_with_signals(
                hypothesis.name, relevant_only=False, since=window.start, until=window.end
            )
        )

    if not screened:
        console.print(
            f"[yellow]No screened signals for {hypothesis.name} in this window.[/yellow] "
            "Run 'demand-radar run' first."
        )
        raise typer.Exit(code=1)

    demand_report = build_report(hypothesis.name, window, pairs, total_signals=screened)
    if chosen_format == "table":
        render_demand_report(demand_report)
        return

    # csv / signals-json export one row per signal; json carries the aggregate view.
    exporters = {
        "csv": lambda: signals_to_csv(pairs),
        "signals-json": lambda: signals_to_json(pairs),
        "json": lambda: report_to_json(demand_report),
    }
    payload = exporters[chosen_format]()
    if out:
        out.write_text(payload, encoding="utf-8")
        console.print(f"Wrote [bold]{len(pairs)}[/bold] relevant signals to {out}")
    else:
        console.print(payload)


@app.command()
def signals(
    db: str = typer.Option(None, help="Database path (default: demand-radar.db)."),
):
    """Show what has been collected, independent of any hypothesis."""
    from rich.table import Table

    with Database(_config(db).db_path) as database:
        repository = SignalRepository(database)
        total = repository.count()
        by_source = repository.sources()

    if not total:
        console.print("[yellow]No signals collected yet.[/yellow]")
        return
    console.print(f"[bold]{total}[/bold] signals collected")
    table = Table("Source", "Signals", title="Collected signals by source")
    for source, count in by_source.items():
        table.add_row(source, str(count))
    console.print(table)
