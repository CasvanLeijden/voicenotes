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
    return cfg, Store(cfg.db_path, my_name=cfg.my_name)


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
        from .names import render
        console.print(Markdown(render(m.summary, store.get_meeting_speakers(m.id))))


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
        from .names import render
        console.print(Markdown(render(store.get_meeting(m.id).summary or "",
                                      store.get_meeting_speakers(m.id))))
    else:
        console.print(f"Later verwerken met: voicenotes process {m.id}")


@app.command("process")
def process_cmd(meeting_id: int,
                speakers: int = typer.Option(None, "--speakers", "-s")):
    """(Opnieuw) transcriberen van een opgenomen meeting."""
    cfg, store = _ctx()
    pipeline.reprocess(cfg, store, store.get_meeting(meeting_id), progress=_progress,
                       num_speakers=speakers)
    from .names import render
    console.print(Markdown(render(store.get_meeting(meeting_id).summary or "",
                                  store.get_meeting_speakers(meeting_id))))


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
        for line in to_lines(store.get_segments(m.id), store.speaker_names(m.id)):
            console.print(line)
    else:
        from .names import render
        console.print(Markdown(render(m.summary, store.get_meeting_speakers(m.id))
                               or "_Nog geen samenvatting._"))


@app.command()
def resummarize(meeting_id: int):
    """Maak de samenvatting opnieuw (bijv. na het wisselen van LLM)."""
    cfg, store = _ctx()
    pipeline.summarize(cfg, store, meeting_id, progress=_progress)
    from .names import render
    console.print(Markdown(render(store.get_meeting(meeting_id).summary or "",
                                  store.get_meeting_speakers(meeting_id))))


@app.command()
def ask(question: str,
        meeting_id: int = typer.Option(None, "--meeting", "-m", help="Alleen in deze meeting zoeken")):
    """Stel een vraag over één meeting of over alle meetings."""
    cfg, store = _ctx()
    console.print(Markdown(pipeline.ask(cfg, store, question, meeting_id)))


@app.command()
def speakers(meeting_id: int):
    """Toon de sprekers van een meeting."""
    _, store = _ctx()
    segs = store.get_segments(meeting_id)
    table = Table("Code", "Spreker", "Persoon", "Spreektijd", "Voorbeeld")
    for sp in store.get_meeting_speakers(meeting_id):
        mine = [s for s in segs if s.speaker == sp.label]
        if not mine:
            continue
        talk = sum(s.end - s.start for s in mine)
        sample = max(mine, key=lambda s: len(s.text)).text[:60]
        table.add_row(sp.tag or "", sp.default_name, sp.person_name or "-", f"{talk / 60:.1f} min",
                      sample)
    console.print(table)


@app.command()
def name(meeting_id: int, speaker: str = typer.Argument(..., help='Code (S2) of naam ("Spreker 2")'),
         person: str = typer.Argument(..., help="Persoon; bestaat die nog niet, dan wordt hij aangemaakt")):
    """Koppel een spreker aan een persoon. Zelfde persoon bij twee sprekers = samengevoegd.

    Voorbeeld: voicenotes name 3 S2 "Jan de Vries"
    """
    cfg, store = _ctx()
    sp = next((x for x in store.get_meeting_speakers(meeting_id)
               if speaker.lower() in {(x.tag or "").lower(), x.default_name.lower(), x.name.lower()}),
              None)
    if sp is None:
        console.print(f"[red]Spreker '{speaker}' niet gevonden in meeting {meeting_id}[/red]")
        raise typer.Exit(1)
    pipeline.assign_speaker(cfg, store, meeting_id, sp.label, person)
    console.print(f"{sp.tag} ({sp.default_name}) is nu {person}. Samenvatting en transcript zijn bijgewerkt.")


@app.command()
def people():
    """Toon alle personen."""
    _, store = _ctx()
    table = Table("ID", "Naam", "Stem bekend", "Meetings")
    for p in store.list_people():
        table.add_row(str(p["id"]), p["name"] + (" (jij)" if p["isMe"] else ""),
                      "ja" if p["hasVoice"] else "nee", str(p["meetings"]))
    console.print(table)


@app.command("merge-people")
def merge_people(source: str, target: str):
    """Voeg persoon SOURCE samen met TARGET (SOURCE verdwijnt). Namen mogen ook ID's zijn."""
    _, store = _ctx()
    ids = []
    for who in (source, target):
        pid = int(who) if who.isdigit() else store.find_person(who)
        if pid is None:
            console.print(f"[red]Geen persoon '{who}'[/red]")
            raise typer.Exit(1)
        ids.append(pid)
    store.merge_people(*ids)
    pipeline.refresh_people(store)
    console.print("Samengevoegd")


@app.command()
def forget(name: str):
    """Verwijder een persoon (en daarmee de herkenning van zijn of haar stem)."""
    _, store = _ctx()
    pid = store.find_person(name)
    if pid is None:
        console.print(f"[red]Geen persoon '{name}'[/red]")
        raise typer.Exit(1)
    store.delete_person(pid)
    pipeline.refresh_people(store)
    console.print("Verwijderd")


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
