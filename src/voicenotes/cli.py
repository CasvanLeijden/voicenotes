"""Command line interface: `voicenotes ...`."""
from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from . import config, pipeline
from .store import Store
from .transcript import to_lines

app = typer.Typer(help="Lokale meeting-transcriptie en samenvattingen (Nederlands).",
                  no_args_is_help=True)
console = Console()


def _ctx():
    cfg = config.load()
    return cfg, Store(cfg.db_path)


# When run by the menu bar app, progress goes out as machine-readable lines.
GUI_PROGRESS = os.environ.get("VOICENOTES_GUI") == "1"
PROGRESS_PREFIX = "@@progress "


def _progress(msg: str) -> None:
    if GUI_PROGRESS:
        print(PROGRESS_PREFIX + msg, flush=True)
    else:
        console.print(f"[dim]•[/dim] {msg}")


@app.command()
def transcribe(file: Path = typer.Argument(..., exists=True, dir_okay=False),
               title: str = typer.Option(None, "--title", "-t", help="Titel van de meeting"),
               speakers: int = typer.Option(None, "--speakers", "-s",
                                            help="Aantal sprekers, als je dat weet"),
               folder: str = typer.Option(None, "--folder", "-f", help="Map (wordt aangemaakt)"),
               folder_id: int = typer.Option(None, "--folder-id", hidden=True)):
    """Transcribeer en vat een audio- of videobestand samen."""
    cfg, store = _ctx()
    if folder:
        folder_id = store.create_folder(folder)
    m = pipeline.import_file(cfg, store, file, title, progress=_progress, num_speakers=speakers,
                             folder_id=folder_id)
    console.print(f"\n[bold green]Klaar[/bold green]: meeting {m.id} in {m.dir}\n")
    if m.summary:
        console.print(Markdown(m.summary))


@app.command()
def record(title: str = typer.Option(None, "--title", "-t", help="Titel van de meeting"),
           mic: bool = typer.Option(True, "--mic/--no-mic", help="Microfoon opnemen"),
           system: bool = typer.Option(True, "--system/--no-system", help="Systeemaudio opnemen"),
           speakers: int = typer.Option(None, "--speakers", "-s", help="Aantal andere sprekers"),
           process: bool = typer.Option(True, "--process/--no-process",
                                        help="Direct transcriberen na het stoppen")):
    """Neem microfoon en al het systeemgeluid op. Stop met Ctrl+C."""
    import signal
    import sys
    import time
    from datetime import datetime

    from .audio.recorder import Recorder

    cfg, store = _ctx()
    m = store.create_meeting(title or f"Opname {datetime.now():%d-%m-%Y %H:%M}",
                             source="recording", meetings_root=cfg.meetings_dir,
                             title_auto=not title)
    rec = Recorder(m.dir, mic=mic, system=system)
    try:
        rec.start()
    except Exception as e:
        console.print(f"[red]Opname kon niet starten:[/red] {e}")
        raise typer.Exit(1)
    if rec.mic_name:
        console.print(f"[dim]Microfoon: {rec.mic_name}[/dim]")
    console.print("[bold red]● Opname loopt[/bold red] (Ctrl+C om te stoppen)")

    def _interrupt(signum, frame):
        raise KeyboardInterrupt

    # Also works when started in the background (SIGINT ignored) or stopped with `kill`.
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    interactive = sys.stdout.isatty()

    def bar(level: float) -> str:
        n = min(20, int(level * 200))
        return "█" * n + "·" * (20 - n)

    try:
        while True:
            if interactive:
                parts = [f"{k}: {bar(v)}" for k, v in rec.levels().items()]
                t = int(rec.elapsed)
                sys.stdout.write(f"\r{t // 60:02d}:{t % 60:02d}  " + "  ".join(parts))
                sys.stdout.flush()
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        rec.stop()
    except RuntimeError as e:
        console.print(f"\n[yellow]Waarschuwing systeemaudio:[/yellow] {e}")
    console.print(f"\n[green]Opname opgeslagen[/green] ({int(rec.elapsed)} s) in {m.dir}")
    if process:
        pipeline.process_recording(cfg, store, m, progress=_progress, num_speakers=speakers)
        console.print(Markdown(store.get_meeting(m.id).summary or ""))
    else:
        console.print(f"Later verwerken met: voicenotes process {m.id}")


@app.command("process")
def process_cmd(meeting_id: int,
                speakers: int = typer.Option(None, "--speakers", "-s")):
    """(Opnieuw) transcriberen van een opgenomen meeting."""
    cfg, store = _ctx()
    pipeline.reprocess(cfg, store, store.get_meeting(meeting_id), progress=_progress,
                       num_speakers=speakers)
    console.print(Markdown(store.get_meeting(meeting_id).summary or ""))


@app.command("list")
def list_meetings(folder: str = typer.Option(None, "--folder", "-f", help="Alleen deze map")):
    """Toon alle meetings."""
    _, store = _ctx()
    folders = {f["id"]: f["name"] for f in store.list_folders()}
    fid = None
    if folder:
        match = [i for i, n in folders.items() if n.lower() == folder.lower()]
        if not match:
            console.print(f"[red]Geen map '{folder}'[/red]")
            raise typer.Exit(1)
        fid = match[0]
    table = Table("ID", "Datum", "Titel", "Map", "Bron")
    for m in store.list_meetings(fid):
        table.add_row(str(m.id), m.created_at.replace("T", " ")[:16], m.title,
                      folders.get(m.folder_id, ""), m.source or "")
    console.print(table)


@app.command()
def folders():
    """Toon alle mappen."""
    _, store = _ctx()
    table = Table("Map", "Meetings")
    for f in store.list_folders():
        table.add_row(f["name"], str(f["count"]))
    console.print(table)


@app.command()
def move(meeting_id: int, folder: str = typer.Argument(..., help="Mapnaam; '-' = geen map")):
    """Verplaats een meeting naar een map (die zo nodig wordt aangemaakt)."""
    _, store = _ctx()
    fid = None if folder == "-" else store.create_folder(folder)
    store.set_meeting_folder(meeting_id, fid)
    console.print(f"Meeting {meeting_id} staat nu in {'geen map' if fid is None else folder}")


@app.command()
def show(meeting_id: int, transcript: bool = typer.Option(False, "--transcript", "-T")):
    """Toon de samenvatting (of het transcript) van een meeting."""
    _, store = _ctx()
    m = store.get_meeting(meeting_id)
    console.print(f"[bold]{m.title}[/bold]  [dim]{m.created_at}  {m.dir}[/dim]\n")
    if transcript:
        for line in to_lines(store.get_segments(m.id)):
            console.print(line)
    else:
        console.print(Markdown(m.summary or "_Nog geen samenvatting._"))


@app.command()
def resummarize(meeting_id: int):
    """Maak de samenvatting opnieuw (bijv. na het wisselen van LLM)."""
    cfg, store = _ctx()
    pipeline.summarize(cfg, store, meeting_id, progress=_progress)
    console.print(Markdown(store.get_meeting(meeting_id).summary or ""))


@app.command()
def ask(question: str,
        meeting_id: int = typer.Option(None, "--meeting", "-m", help="Alleen in deze meeting zoeken")):
    """Stel een vraag over één meeting of over alle meetings."""
    cfg, store = _ctx()
    console.print(Markdown(pipeline.ask(cfg, store, question, meeting_id)))


@app.command()
def speakers(meeting_id: int = typer.Argument(None, help="Meeting-ID; leeg = opgeslagen stemprofielen")):
    """Toon de sprekers van een meeting, of alle opgeslagen stemprofielen."""
    _, store = _ctx()
    if meeting_id is None:
        table = Table("Naam", "Samples")
        for _, name, _, n in sorted(store.get_profiles(), key=lambda p: p[1].lower()):
            table.add_row(name, str(n))
        console.print(table)
        return
    segs = store.get_segments(meeting_id)
    table = Table("Spreker", "Bekend profiel", "Spreektijd", "Voorbeeld")
    for _, name, pid, _ in store.get_meeting_speakers(meeting_id):
        mine = [s for s in segs if s.speaker == name]
        talk = sum(s.end - s.start for s in mine)
        sample = max(mine, key=lambda s: len(s.text)).text[:70] if mine else ""
        table.add_row(name, "ja" if pid else "nee", f"{talk / 60:.1f} min", sample)
    console.print(table)


@app.command()
def name(meeting_id: int, old: str, new: str,
         no_summary: bool = typer.Option(False, "--no-summary", help="Samenvatting niet opnieuw maken")):
    """Geef een spreker een naam; de stem wordt onthouden voor volgende meetings.

    Voorbeeld: voicenotes name 3 "Spreker 1" "Jan de Vries"
    """
    cfg, store = _ctx()
    try:
        pipeline.name_speaker(cfg, store, meeting_id, old, new, progress=_progress,
                              resummarize=not no_summary)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command()
def forget(name: str):
    """Verwijder een opgeslagen stemprofiel."""
    _, store = _ctx()
    ok = store.delete_profile(name)
    console.print("Verwijderd" if ok else f"[red]Geen profiel '{name}'[/red]")


@app.command("app")
def run_app(hidden: bool = typer.Option(False, "--hidden", help="Alleen in de menubalk starten")):
    """Start de menubalk-app."""
    import sys

    from .app import main
    sys.argv = [sys.argv[0]] + (["--hidden"] if hidden else [])
    main()


@app.command("install-app")
def install_app(login: bool = typer.Option(True, "--login/--no-login",
                                           help="Starten bij inloggen"),
                open_app: bool = typer.Option(True, "--open/--no-open",
                                              help="De app na installatie openen")):
    """Bouw Voicenotes.app in ~/Applications en (optioneel) start hem bij inloggen."""
    import subprocess

    from . import install
    console.print("Voicenotes.app bouwen…")
    path = install.build()
    install.set_login(login)
    console.print(f"[green]Geïnstalleerd:[/green] {path}")
    console.print("Start bij inloggen: " + ("aan" if login else "uit"))
    if open_app:
        subprocess.run(["open", str(path)])


@app.command("config")
def show_config():
    """Toon het pad en de inhoud van de configuratie."""
    config.load()
    console.print(f"[dim]{config.CONFIG_PATH}[/dim]\n")
    console.print(config.CONFIG_PATH.read_text())


if __name__ == "__main__":
    app()
