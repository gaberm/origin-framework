from __future__ import annotations
import sys
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.measure import Measurement
from rich.panel import Panel
from rich.progress import (
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

_EGG = "\U0001f95a"


def _section_title(title: str) -> str:
    return f"\n{_EGG} [bold]{title}[/bold]"


def _safe_console() -> Console:
    """A Console whose output stream never raises on an unencodable
    character (e.g. an emoji under a non-UTF-8 locale). Instead of crashing
    the run, unencodable characters degrade to a backslash-escaped form."""
    stream = sys.stdout
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(errors="backslashreplace")
        except Exception:
            pass
    return Console(file=stream)


class _DinoTrack:
    """Renderable that fills whatever width the table gives it, up to
    `max_width`, so the dino never gets cropped on a narrow terminal."""

    def __init__(self, fraction: float, max_width: int, style: str):
        self.fraction = fraction
        self.max_width = max_width
        self.style = style

    def __rich_measure__(self, console, options) -> Measurement:
        return Measurement(1, min(self.max_width, options.max_width))

    def __rich_console__(self, console, options):
        # The dino emoji renders 2 cells wide, so the track's dots (1 cell
        # each) only fill `width - 2` cells to keep the total exact.
        width = max(3, min(self.max_width, options.max_width))
        dots = width - 2
        position = min(int(self.fraction * dots), dots)
        track = "·" * position + "\U0001f996" + "·" * (dots - position)
        yield Text(track, style=self.style)


class DinoBarColumn(ProgressColumn):
    """A 🦖 running left to right along a dotted track, in place of a bar."""

    def __init__(self, width: int = 40):
        self.width = width
        super().__init__()

    def render(self, task) -> _DinoTrack:
        total = task.total or 1
        fraction = min((task.completed or 0) / total, 1.0) if total else 0.0
        return _DinoTrack(fraction, self.width, style="green")


class StatusTagColumn(ProgressColumn):
    """A right-aligned '[pending]' / '[done]' tag reflecting task completion."""

    def render(self, task) -> Text:
        if task.finished:
            return Text("[done]", style="green", justify="right")
        return Text("[pending]", style="yellow", justify="right")


class ElapsedRemainingRateColumn(ProgressColumn):
    """Renders '[elapsed<remaining, rate]' as one dim unit, tqdm-style."""

    def render(self, task) -> Text:
        def format_time(seconds: float | None) -> str:
            if seconds is None:
                return "?"
            seconds = int(seconds)
            hours, remainder = divmod(seconds, 3600)
            minutes, secs = divmod(remainder, 60)
            if hours:
                return f"{hours:d}:{minutes:02d}:{secs:02d}"
            return f"{minutes:02d}:{secs:02d}"

        speed = task.finished_speed or task.speed
        rate = f"{speed:.1f}it/s" if speed else "?it/s"
        elapsed = format_time(task.elapsed)
        remaining = format_time(task.time_remaining)
        return Text(f"[{elapsed}<{remaining}, {rate}]", style="dim")


def _panel_width(values: dict[str, str]) -> int:
    return max(
        max(len(name) for name in values) + 6,
        max(len(value) for value in values.values()) + 4,
    )


def _model_times_view(model_times: dict[str, float], title: str) -> Table:
    """A row of per-model time panels, with the title vertically centered
    beside them rather than on its own line above."""
    values = {name: str(t) for name, t in model_times.items()}
    grid = Table.grid(padding=(0, 1))
    grid.add_column(vertical="middle")
    if not values:
        grid.add_row(f"[bold not italic]{title}[/bold not italic]")
        return grid
    width = _panel_width(values)
    min_time = min(model_times.values())
    panels = [
        Panel(
            f"[bold]{value}[/bold]",
            title=f"[white]{name}[/white]",
            width=width,
            border_style="yellow" if model_times[name] == min_time else "green",
        )
        for name, value in values.items()
    ]
    for _ in panels:
        grid.add_column(vertical="middle")
    grid.add_row(f"[bold not italic]{title}[/bold not italic]", *panels)
    return grid


class WaitList:
    """Live spinner/status list, one row per name, shown under a section
    header. Used both for worker registration and for RPC broadcasts."""

    def __init__(self, console: Console, names: list[str], title: str):
        self._console = console
        self._names = names
        self._title = title
        self._progress: Progress | None = None
        self._tasks: dict[str, int] = {}

    def __enter__(self) -> "WaitList":
        self._console.print(_section_title(self._title))
        self._progress = Progress(
            SpinnerColumn(style="yellow", finished_text="[green]✓[/green]"),
            TextColumn("{task.description}"),
            StatusTagColumn(),
            console=self._console,
        )
        self._progress.__enter__()
        self._tasks = {
            name: self._progress.add_task(name, total=1) for name in self._names
        }
        return self

    def __exit__(self, *exc_info):
        return self._progress.__exit__(*exc_info)

    def mark_done(self, name: str):
        self._progress.update(self._tasks[name], completed=1)


class LoadingDataView:
    """Live table with one row per data adapter: status + dataset count."""

    def __init__(self, console: Console, names: list[str]):
        self._console = console
        self._names = names
        self._counts = {name: 0 for name in names}
        self._done: set[str] = set()
        self._spinner = Spinner("dots", style="yellow")
        self._live: Live | None = None

    def _render(self) -> Table:
        table = Table(
            title="[bold]Loading Data[/bold]", box=box.ROUNDED, header_style="bold cyan"
        )
        table.add_column("Adapter")
        table.add_column("Status")
        table.add_column("Datasets", justify="right")
        for name in self._names:
            status = "[green]✓ done[/green]" if name in self._done else self._spinner
            table.add_row(name, status, str(self._counts[name]))
        return table

    def __enter__(self) -> "LoadingDataView":
        self._live = Live(self._render(), console=self._console, refresh_per_second=8)
        self._live.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._live.__exit__(*exc_info)

    def record(self, name: str):
        self._counts[name] += 1
        self._live.update(self._render())

    def mark_done(self, name: str):
        self._done.add(name)
        self._live.update(self._render())


class RunDashboard:
    """Live 'Iterations' bar (a running 🦖) plus per-model time panels,
    updated once per scheduler tick."""

    def __init__(self, console: Console, max_global_time: float):
        self._console = console
        self._progress = Progress(
            TextColumn("[bold]{task.description}"),
            DinoBarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(text_format="{task.percentage:.0f}%", style="green"),
            ElapsedRemainingRateColumn(),
            console=console,
        )
        self._task = self._progress.add_task("Iterations", total=max_global_time)
        self._model_times: dict[str, float] = {}
        self._live: Live | None = None

    def _render(self) -> Group:
        return Group(self._progress, "", _model_times_view(self._model_times, "Model Times"))

    def __enter__(self) -> "RunDashboard":
        self._console.print(_section_title("Simulation Dashboard") + "\n")
        self._live = Live(self._render(), console=self._console, refresh_per_second=8)
        self._live.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._live.__exit__(*exc_info)

    def update(self, elapsed: float, model_times: dict[str, float]):
        self._model_times = model_times
        self._progress.update(self._task, completed=elapsed)
        self._live.update(self._render())


class ConsoleDisplay:
    """Owns all terminal presentation for a simulation run: the console
    itself, section headers, and every live dashboard/table/panel view.
    SupervisoryModel calls into this rather than touching rich directly."""

    def __init__(self):
        self.console = _safe_console()

    def print_dependencies(self, dependencies: dict[str, tuple[str, ...]]):
        self.section_header("Derived dependencies")
        for name, deps in dependencies.items():
            if deps:
                self.console.print(
                    f"  [white]{name}[/white] [dim]→[/dim] {', '.join(deps)}"
                )
            else:
                self.console.print(
                    f"  [white]{name}[/white] [dim italic](none)[/dim italic]"
                )

    def section_header(self, title: str):
        """A lighter-weight heading for a phase within the run, as opposed to
        the full-width Rule that brackets the run as a whole."""
        self.console.print(_section_title(title))

    def warning(self, message: str):
        self.console.print(f"[yellow]⚠[/yellow] {message}")

    def start_run(self):
        self.console.print(Rule("[bold]\U0001f996 Starting simulation run[/bold]"))

    def end_run(self):
        self.console.print(Rule("[bold green]Simulation run completed[/bold green]"))

    def wait_list(self, names: list[str], title: str) -> WaitList:
        return WaitList(self.console, names, title)

    def loading_data(self, names: list[str]) -> LoadingDataView:
        return LoadingDataView(self.console, names)

    def run_dashboard(self, max_global_time: float) -> RunDashboard:
        return RunDashboard(self.console, max_global_time)

    def print_final_model_times(self, model_times: dict[str, float]):
        self.console.print(_model_times_view(model_times, "Final Model Times"))
