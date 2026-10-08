"""Terminal rendering for CLI output. No business logic lives here."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from demand_radar.data_plane import CollectionReport
from demand_radar.intelligence import ScreeningReport
from demand_radar.intelligence.reporting import DemandReport

console = Console()


def render_collection(report: CollectionReport) -> None:
    console.print(
        f"Collected [bold]{report.collected}[/bold] signals "
        f"([green]{report.new} new[/green], {report.collected - report.new} already known)"
    )
    if report.by_source:
        table = Table("Source", "Signals", "Pages", "Retries", title="Collection by source")
        for source, count in sorted(report.by_source.items(), key=lambda kv: -kv[1]):
            table.add_row(
                source,
                str(count),
                str(report.pages_by_source.get(source, 0)),
                str(report.retries_by_source.get(source, 0)),
            )
        console.print(table)
    if report.skipped_out_of_window:
        console.print(
            f"[dim]{report.skipped_out_of_window} signals fell outside the window and were skipped[/dim]"
        )
    if report.skipped_off_query:
        console.print(
            f"[dim]{report.skipped_off_query} returned items did not contain the query terms[/dim]"
        )
    if not report.collected and not report.errors:
        console.print(
            "[yellow]No signals matched.[/yellow] Sources rank loosely, so long phrases "
            "rarely match — try shorter, more distinctive queries, or a wider --period."
        )
    for source, error in report.errors.items():
        console.print(f"[yellow]⚠ {source}:[/yellow] {error}")


def render_screening(report: ScreeningReport) -> None:
    if not report.examined:
        console.print("[dim]Nothing new to screen.[/dim]")
        return

    stages = Table("Stage", "Decided", "Kept", "Cost", title="Screening stages")
    stages.add_row(
        "1 · local filter",
        str(report.examined),
        str(report.examined - report.cheap_rejected),
        "[green]free[/green]",
    )
    if report.jev_model:
        stages.add_row(
            f"2 · Jev [dim]{report.jev_model}[/dim]",
            str(report.jev_calls),
            str(report.jev_calls - report.jev_rejected),
            f"[green]${report.jev_cost_usd:.6f}[/green]",
        )
    else:
        stages.add_row("2 · Jev", "[dim]off[/dim]", "—", "—")
    stages.add_row(
        f"3 · {report.model}" if report.model else "3 · generative model",
        str(report.model_calls) if report.model else "[dim]off[/dim]",
        "—",
        f"[yellow]{report.model_calls} calls[/yellow]" if report.model_calls else "—",
    )
    console.print(stages)
    console.print(
        f"Relevant: [bold]{report.relevant}[/bold] · irrelevant: {report.irrelevant} "
        f"· [dim]{report.cost_reduction:.0%} never reached the generative model[/dim]"
    )
    for error in report.errors[:5]:
        console.print(f"[yellow]⚠ {error}[/yellow]")
    if len(report.errors) > 5:
        console.print(f"[yellow]… and {len(report.errors) - 5} more screening errors[/yellow]")


def render_demand_report(report: DemandReport) -> None:
    console.print()
    console.print(f"[bold]{report.hypothesis}[/bold]  ·  {_window(report)}")
    console.print("  ".join(report.headline()))
    console.print(
        f"[dim]{report.relevant_signals} of {report.total_signals} screened signals were relevant "
        f"({report.relevance_rate:.0%})[/dim]"
    )

    for title, counts in (
        ("Pain types", report.by_pain),
        ("Roles", report.by_role),
        ("Language patterns", report.by_language_pair),
        ("Sources", report.by_source),
        ("Communities", report.by_community),
        ("Commercial intent", report.by_intent),
        ("Competitors named", report.competitors),
    ):
        _render_counts(title, counts)

    signals = [
        ("Actively seeking a solution", report.solution_seeking),
        ("Dissatisfied with an existing tool", report.dissatisfied_with_existing),
        ("Possible distribution opportunity", report.distribution_opportunities),
    ]
    if any(count for _, count in signals):
        table = Table("Opportunity signal", "Signals", title="Opportunity")
        for label, count in signals:
            table.add_row(label, str(count))
        console.print(table)

    if report.voice_of_customer:
        console.print("\n[bold]Voice of Customer[/bold] [dim](verbatim)[/dim]")
        for entry in report.voice_of_customer[:10]:
            where = entry.community or entry.source
            console.print(f'  [italic]"{entry.quote}"[/italic] [dim]— {where}[/dim]')


def _render_counts(title: str, counts: dict[str, int], limit: int = 10) -> None:
    if not counts:
        return
    table = Table(title, "Signals", title=title)
    for key, count in list(counts.items())[:limit]:
        table.add_row(key, str(count))
    console.print(table)


def _window(report: DemandReport) -> str:
    return f"{report.window.start.date()} → {report.window.end.date()}"
