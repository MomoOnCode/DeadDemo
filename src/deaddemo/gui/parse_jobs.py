"""Start demo parses from the GUI in the process pool and store results on the main thread."""

from __future__ import annotations

from deaddemo import paths
from deaddemo.core.db.repos import DemoRow
from deaddemo.core.parse.pipeline import parse_demo
from deaddemo.core.parse.runner import store_result
from deaddemo.gui.context import AppContext


def start_parse(ctx: AppContext, demo: DemoRow) -> None:
    name = f"parse:{demo.match_id or demo.id}"
    if ctx.jobs.is_running(name):
        return
    out_dir = paths.parsed_dir(demo.match_id or demo.id)
    datasets = tuple(ctx.settings.extra_datasets)

    def done(result) -> None:
        try:
            store_result(ctx.db, demo, result)
        except Exception as exc:  # noqa: BLE001
            ctx.demos.set_status(demo.id, "error", error=f"store: {exc}")
            ctx.status(f"Failed to store match {demo.match_id}: {exc}", 10000)
        else:
            ctx.status(f"Analyzed match {result.match_id}")
        ctx.events.demos_changed.emit()
        ctx.events.matches_changed.emit()

    def failed(err: str) -> None:
        ctx.demos.set_status(demo.id, "error", error=err.splitlines()[0][:300])
        ctx.status(f"Analysis failed for {demo.path}: {err.splitlines()[0]}", 15000)
        ctx.events.demos_changed.emit()

    ctx.jobs.submit_process(name, parse_demo, demo.path, str(out_dir), datasets,
                            on_finished=done, on_failed=failed)
    ctx.status(f"Analyzing {demo.path} …")
