"""Voicenotes menu bar app: PySide6 shell around an HTML interface."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, QPointF, QProcess, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from . import config, pipeline
from .asr.final import Segment
from .detect import APP_NAMES, BROWSERS, CallDetector, MicPoller
from .audio.recorder import Recorder
from .store import Store
from .transcript import drop_echo
from .ui.bridge import Bridge
from .ui.hud import DictationHUD
from .ui.prompt import CallPrompt
from .ui.web_window import WebWindow
from .ui.workers import CliJob, run_async

AUDIO_FILTER = ("Audio of video (*.wav *.mp3 *.m4a *.mp4 *.mov *.aac *.flac *.ogg *.opus *.webm)"
                ";;Alle bestanden (*)")
OTHERS = "Anderen"


def _icon(recording: bool) -> QIcon:
    """Microphone glyph; red dot while recording. Mask icon so macOS adapts it to the menu bar."""
    size = 44
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("black"), 3.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(QColor("black"))
    p.drawRoundedRect(QRectF(16, 6, 12, 20), 6, 6)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(QPointF(10, 20))
    path.cubicTo(QPointF(10, 36), QPointF(34, 36), QPointF(34, 20))
    p.drawPath(path)
    p.drawLine(QPointF(22, 33), QPointF(22, 39))
    if recording:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#E5484D"))
        p.drawEllipse(QRectF(29, 1, 14, 14))
    p.end()
    icon = QIcon(pm)
    icon.setIsMask(not recording)
    return icon


def _login_enabled() -> bool:
    from . import install
    return install.login_enabled()


def _app_installed() -> bool:
    from . import install
    return install._installed_app() is not None


def _perm_input() -> bool:
    try:
        import Quartz
        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        return False


def _perm_paste() -> bool:
    try:
        import Quartz
        return bool(Quartz.CGPreflightPostEventAccess())
    except Exception:
        return False


def _set_dock_visible(visible: bool) -> None:
    try:
        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSApplicationActivationPolicyRegular)
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular if visible
                                 else NSApplicationActivationPolicyAccessory)
        if visible:
            app.activateIgnoringOtherApps_(True)
    except Exception:
        pass


class Controller(QObject):
    _live_segment = Signal(object)  # thread-safe hop from the ASR thread to the UI thread
    _live_error = Signal(str)
    _mic_users = Signal(object)
    _fn_action = Signal(str)
    _dict_step = Signal(str)

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.cfg = config.load()
        self.store = Store(self.cfg.db_path)
        self.busy: set[int] = set()
        self.progress_text: dict[int, str] = {}
        self.jobs: list[CliJob] = []
        self.recorder: Recorder | None = None
        self.rec_meeting: int | None = None
        self.active_folder: int | None = None
        self.live = None
        self.live_segments: list = []

        self.bridge = Bridge(self)
        self.window = WebWindow(self.bridge, self.hide_window)
        self._live_segment.connect(self._on_live_segment)
        self._live_error.connect(lambda e: self.bridge.status.emit(e))

        hints = QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(lambda _: self._apply_theme())
        self._apply_theme()

        self._build_tray()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

        # Call detection
        self.detector = CallDetector(ignore_pids={os.getpid()})
        self.rec_by_call: str | None = None   # app whose call started the current recording
        self.prompt: CallPrompt | None = None
        self.poller: MicPoller | None = None
        self._mic_users.connect(self._on_mic_users)
        self._update_poller()

        # Dictation (Fn)
        self.capture = None
        self.dict_span_start: float | None = None
        self.dict_spans: list[tuple[float, float]] = []   # within the current recording
        self.fn_listener = None
        self.hud = DictationHUD(lambda: self.capture.level if self.capture else 0.0)
        self._fn_action.connect(self._on_fn)
        self._dict_step.connect(lambda st: self.hud.show_state(st))
        self.perm_timer = QTimer(self)
        self.perm_timer.timeout.connect(self._update_dictation)
        self._update_dictation(prompt=True)

    # --- tray & window --------------------------------------------------

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_icon(False))
        self.tray.setToolTip("Voicenotes")
        menu = QMenu()
        self.rec_item = QAction("Opname starten")
        self.rec_item.triggered.connect(self.toggle_recording)
        self.time_item = QAction("")
        self.time_item.setEnabled(False)
        self.time_item.setVisible(False)
        show = QAction("Voicenotes openen")
        show.triggered.connect(self.show_window)
        imp = QAction("Bestand importeren…")
        imp.triggered.connect(self.pick_and_import)
        quit_ = QAction("Voicenotes stoppen")
        quit_.triggered.connect(self.quit)
        menu.addAction(self.rec_item)
        menu.addAction(self.time_item)
        menu.addSeparator()
        menu.addAction(show)
        menu.addAction(imp)
        menu.addSeparator()
        menu.addAction(quit_)
        self._menu = menu
        self._menu_actions = [self.rec_item, self.time_item, show, imp, quit_]
        self.tray.setContextMenu(menu)
        self.tray.show()

    def theme_name(self) -> str:
        scheme = QGuiApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def _apply_theme(self) -> None:
        name = self.theme_name()
        self.window.set_dark(name == "dark")
        self.bridge.theme.emit(name)

    def show_window(self) -> None:
        _set_dock_visible(True)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self._apply_theme()

    def hide_window(self) -> None:
        self.window.hide()
        _set_dock_visible(False)

    def notify(self, text: str) -> None:
        self.tray.showMessage("Voicenotes", text, QSystemTrayIcon.MessageIcon.Information, 5000)

    def changed(self, mid: int | None = None) -> None:
        self.bridge.meetingsChanged.emit()
        if mid is not None:
            self.bridge.meetingChanged.emit(mid)

    # --- recording ------------------------------------------------------

    def toggle_recording(self) -> None:
        self.stop_recording() if self.recorder else self.start_recording()

    def start_recording(self, title: str | None = None, call_app: str | None = None) -> None:
        if self.recorder:
            return
        self._close_prompt()
        # Placeholder title; replaced by a generated one (in the meeting's language) after
        # processing, unless the user renames the meeting first.
        placeholder = (f"{self._call_label(call_app)} {datetime.now():%d-%m %H:%M}" if call_app
                       else f"Opname {datetime.now():%d-%m %H:%M}")
        m = self.store.create_meeting(title or placeholder,
                                      source=f"recording:{call_app}" if call_app else "recording",
                                      meetings_root=self.cfg.meetings_dir,
                                      folder_id=self.active_folder, title_auto=True)
        self.live_segments = []
        self.live = None
        if self.cfg.live_transcription:
            from .asr.live import LiveTranscriber
            self.live = LiveTranscriber(self._live_segment.emit, model=self.cfg.live_model,
                                        language=self.cfg.language,
                                        on_error=self._live_error.emit)
            self.live.start()
        rec = Recorder(m.dir, live=self.live)
        try:
            rec.start()
        except Exception as e:
            if self.live:
                self.live.stop(timeout=1)
                self.live = None
            shutil.rmtree(self.store.delete_meeting(m.id), ignore_errors=True)
            self.show_window()
            QMessageBox.critical(self.window, "Opname kon niet starten",
                                 f"{e}\n\nGeef Voicenotes toegang tot de microfoon en "
                                 "systeemaudio in Systeeminstellingen → Privacy en beveiliging.")
            return
        self.recorder, self.rec_meeting = rec, m.id
        self.rec_by_call = call_app
        self.dict_spans = []
        self.tray.setIcon(_icon(True))
        self.rec_item.setText("Opname stoppen en verwerken")
        self.time_item.setVisible(True)
        self.timer.start(100)
        self.bridge.recording.emit(json.dumps({"active": True, "id": m.id}))
        self.changed(m.id)

    def _tick(self) -> None:
        if not self.recorder:
            return
        t = self.recorder.elapsed
        lv = self.recorder.levels()
        self.bridge.levels.emit(json.dumps({"mic": lv.get("mic", 0.0),
                                            "system": lv.get("system", 0.0), "elapsed": t}))
        label = f"{int(t) // 60:02d}:{int(t) % 60:02d}"
        self.time_item.setText(f"Opname loopt  {label}")
        self.tray.setToolTip(f"Voicenotes: opname {label}")

    def _on_live_segment(self, seg) -> None:
        self.live_segments.append(seg)
        self.bridge.live.emit(json.dumps(self.live_payload()))

    def _live_as_segments(self) -> list[Segment]:
        mic = [Segment(s.start, s.end, s.text, self.cfg.my_name)
               for s in self.live_segments if s.channel == "mic"]
        mic = pipeline.drop_dictation(mic, self.dict_spans)
        system = [Segment(s.start, s.end, s.text, OTHERS)
                  for s in self.live_segments if s.channel == "system"]
        return sorted(drop_echo(mic, system) + system, key=lambda s: s.start)

    def live_payload(self) -> dict:
        return {"id": self.rec_meeting,
                "segments": [{"start": s.start, "end": s.end, "speaker": s.speaker, "text": s.text}
                             for s in self._live_as_segments()]}

    def stop_recording(self, process: bool = True) -> int | None:
        if not self.recorder:
            return None
        rec, mid = self.recorder, self.rec_meeting
        self.recorder = None
        self.rec_by_call = None
        self._close_prompt()
        self.timer.stop()
        try:
            rec.stop()
        except Exception as e:
            self.bridge.status.emit(f"Waarschuwing bij stoppen: {e}")
        if self.live:
            self.live.stop(timeout=5)
            self.live = None
        if self.capture is not None and self.dict_span_start is not None:
            self.dict_spans.append((self.dict_span_start, rec.elapsed if rec.t0 else 0.0))
            self.dict_span_start = None
        pipeline.save_dictation_spans(self.store.get_meeting(mid).dir, self.dict_spans)
        # Keep the live transcript as a first version until processing replaces it.
        live = self._live_as_segments()
        if live:
            self.store.save_segments(mid, live)
        self.rec_meeting = None
        self.tray.setIcon(_icon(False))
        self.tray.setToolTip("Voicenotes")
        self.rec_item.setText("Opname starten")
        self.time_item.setVisible(False)
        self.bridge.recording.emit(json.dumps({"active": False, "id": mid}))
        if process:
            self.run_job(["process", str(mid)], mid, "Verwerken")
        self.changed(mid)
        return mid

    # --- dictation --------------------------------------------------------

    def _update_dictation(self, prompt: bool = False) -> None:
        """Start/stop the Fn listener to match the settings and permissions."""
        from .hotkey import FnListener, input_monitoring_allowed
        if not self.cfg.dictation:
            if self.fn_listener:
                self.fn_listener.stop()
                self.fn_listener = None
            self.perm_timer.stop()
            return
        if self.fn_listener is not None:
            return
        if not input_monitoring_allowed(prompt=prompt):
            # macOS shows its own dialog; check again every few seconds until allowed.
            if prompt:
                self.notify("Geef Voicenotes toegang tot Invoerbewaking om met fn te dicteren "
                            "(Systeeminstellingen → Privacy en beveiliging).")
            self.perm_timer.start(3000)
            return
        self.perm_timer.stop()
        self.fn_listener = FnListener(self._fn_action.emit)
        self.fn_listener.start()
        from .paste import can_post_keys
        can_post_keys(prompt=prompt)  # ask for Accessibility up front, so pasting works later
        # Load the fast model now so the first dictation is instant.
        from .asr.live import warm_up
        cfg = self.cfg
        run_async(lambda: warm_up(cfg.dictation_model, cfg.language), lambda _: None,
                  lambda e: print(f"Dicteermodel laden mislukt: {e}"))

    def _on_fn(self, action: str) -> None:
        from . import dictation
        if action == "start":
            if self.capture is not None:
                return
            cap = dictation.Capture(recorder=self.recorder)
            try:
                cap.start()
            except Exception as e:
                self.hud.show_state("error", auto_hide_ms=2500)
                self.bridge.status.emit(f"Dicteren lukte niet: {e}")
                return
            self.capture = cap
            self.dict_span_start = self.recorder.elapsed if self.recorder else None
            self.hud.show_state("listening")
            QTimer.singleShot(dictation.MAX_SECONDS * 1000,
                              lambda c=cap: self._on_fn("stop") if self.capture is c else None)
        elif action == "handsfree":
            if self.capture is not None:
                self.hud.show_state("handsfree")
        elif action in ("stop", "cancel"):
            cap, self.capture = self.capture, None
            if cap is None:
                return
            audio = cap.stop()
            if self.recorder and self.dict_span_start is not None:
                self.dict_spans.append((self.dict_span_start, self.recorder.elapsed))
            self.dict_span_start = None
            if action == "cancel":
                self.hud.hide()
                return
            self.hud.show_state("transcribing")
            cfg = self.cfg
            run_async(lambda: dictation.process(audio, cfg, self._dict_step.emit),
                      self._dictation_done,
                      lambda e: (self.hud.show_state("error", auto_hide_ms=2500),
                                 self.bridge.status.emit(f"Dicteren lukte niet: {e}")))

    def _dictation_done(self, text: str) -> None:
        from .paste import paste_text
        if not text:
            self.hud.show_state("empty", auto_hide_ms=1200)
            return
        if paste_text(text):
            self.hud.hide()
        else:
            self.hud.show_state("clipboard", auto_hide_ms=2500)
            self.notify("Geef Voicenotes toegang tot Toegankelijkheid om tekst direct te plakken. "
                        "De tekst staat nu op je klembord.")

    # --- call detection -------------------------------------------------

    def _update_poller(self) -> None:
        want = self.cfg.detect_calls
        if want and self.poller is None:
            try:
                from .audio.system_tap import find_audiotap
                find_audiotap()
            except FileNotFoundError:
                return
            self.poller = MicPoller(self._mic_users.emit)
            self.poller.start()
        elif not want and self.poller is not None:
            self.poller.stop()
            self.poller = None

    def _on_mic_users(self, users: list) -> None:
        if not self.cfg.detect_calls:
            return
        for ev in self.detector.update(time.monotonic(), users):
            if ev.kind == "started":
                self._call_started(ev.app)
            else:
                self._call_ended(ev.app)

    @staticmethod
    def _call_label(app: str) -> str:
        return f"Call in {app}" if app in BROWSERS else f"{app}-call"

    def _call_started(self, app: str) -> None:
        if self.recorder or app in self.cfg.ignored_apps:
            return
        if app in self.cfg.auto_record_apps:
            self.start_recording(call_app=app)
            if self.recorder:
                self.notify(f"Opname gestart: {self._call_label(app)}. "
                            "Laat de deelnemers weten dat je opneemt.")
            return
        self._show_prompt(
            f"{self._call_label(app)} gestart",
            "Wil je deze call opnemen? Laat de deelnemers weten dat je opneemt.",
            "Opnemen", "Niet nu",
            lambda: self.start_recording(call_app=app))

    def _call_ended(self, app: str) -> None:
        if not self.recorder:
            self._close_prompt()  # call ended before anyone answered the prompt
            return
        if self.rec_by_call == app:
            self.stop_recording()
            self.notify(f"{self._call_label(app)} afgelopen. De opname wordt verwerkt.")
        elif self.rec_by_call is None and not self.detector.active_calls:
            self._show_prompt(
                f"{self._call_label(app)} lijkt afgelopen",
                "De opname loopt nog. Wil je hem stoppen en laten verwerken?",
                "Stoppen", "Doorgaan", self.stop_recording)

    def _show_prompt(self, title, text, primary, secondary, on_primary) -> None:
        self._close_prompt()
        self.prompt = CallPrompt(title, text, primary, secondary, on_primary,
                                 dark=self.theme_name() == "dark")
        self.prompt.destroyed.connect(lambda *_: setattr(self, "prompt", None))
        self.prompt.show()

    def _close_prompt(self) -> None:
        if self.prompt is not None:
            try:
                self.prompt.close()
            except RuntimeError:
                pass
            self.prompt = None

    # --- background jobs ------------------------------------------------

    def run_job(self, args: list[str], mid: int | None, label: str,
                on_done: Callable[[bool], None] | None = None) -> None:
        job = CliJob(args, self)
        if mid is not None:
            self.busy.add(mid)
            self.progress_text[mid] = f"{label}…"
        self.jobs.append(job)

        def emit(line: str, done=False, ok=True):
            self.bridge.progress.emit(json.dumps({"id": mid, "label": label, "line": line,
                                                  "done": done, "ok": ok}))

        def progress(line: str) -> None:
            if mid is not None:
                self.progress_text[mid] = line
            emit(line)
            self.changed(mid)

        def finished(ok: bool, last: str) -> None:
            self.jobs.remove(job)
            if mid is not None:
                self.busy.discard(mid)
                self.progress_text.pop(mid, None)
            self.cfg = config.load()
            emit("Klaar" if ok else f"Mislukt: {last}", done=True, ok=ok)
            self.notify(f"{label}: klaar" if ok else f"{label} mislukt")
            self.changed(mid)
            if on_done:
                on_done(ok)

        job.progress.connect(progress)
        job.finished.connect(finished)
        job.start()
        emit(f"{label}…")
        self.changed(mid)

    def pick_and_import(self) -> None:
        self.show_window()
        path, _ = QFileDialog.getOpenFileName(self.window, "Audio of video importeren", "",
                                              AUDIO_FILTER)
        if path:
            from pathlib import Path
            args = ["transcribe", path]
            if self.active_folder is not None:
                args += ["--folder-id", str(self.active_folder)]
            self.run_job(args, None, f"{Path(path).name} importeren")

    def reprocess(self, mid: int) -> None:
        self.run_job(["process", str(mid)], mid, "Opnieuw verwerken")

    def resummarize(self, mid: int) -> None:
        self.run_job(["resummarize", str(mid)], mid, "Samenvatten")

    def name_speaker(self, mid: int, old: str, new: str) -> None:
        self.busy.add(mid)
        self.progress_text[mid] = f"{new} opslaan en samenvatting bijwerken…"
        self.changed(mid)
        cfg = self.cfg

        def work():
            pipeline.name_speaker(cfg, Store(cfg.db_path), mid, old, new, progress=lambda _: None)

        def finish(msg: str):
            self.busy.discard(mid)
            self.progress_text.pop(mid, None)
            self.bridge.status.emit(msg)
            self.changed(mid)

        run_async(work, lambda _: finish(f"{new} wordt voortaan herkend"),
                  lambda e: finish(f"Naam opslaan mislukt: {e}"))

    def ask(self, question: str, mid: int | None, on_done, on_error,
            folder_id: int | None = None) -> None:
        cfg = self.cfg
        run_async(lambda: pipeline.ask(cfg, Store(cfg.db_path), question, mid, folder_id=folder_id),
                  on_done, on_error)

    def delete_meeting(self, mid: int) -> None:
        if mid == self.rec_meeting or mid in self.busy:
            self.bridge.status.emit("Deze meeting is nog bezig en kan nu niet weg.")
            return
        shutil.rmtree(self.store.delete_meeting(mid), ignore_errors=True)
        self.changed()

    # --- settings -------------------------------------------------------

    def settings_payload(self) -> dict:
        from .llm import loaded_models
        loaded = loaded_models(self.cfg.lmstudio_url, timeout=2)
        models = [m["id"] for m in loaded]
        contexts = {m["id"]: m.get("loaded_context_length") for m in loaded}
        c = self.cfg
        return {"my_name": c.my_name, "whisper_model": c.whisper_model,
                "vocabulary": c.vocabulary, "llm_model": c.llm_model, "llm_thinking": c.llm_thinking, "llm_models": models, "llm_contexts": contexts,
                "lmstudio_ok": bool(models), "diarize": c.diarize,
                "speaker_threshold": c.speaker_threshold,
                "live_transcription": c.live_transcription,
                "detect_calls": c.detect_calls,
                "start_at_login": _login_enabled(), "app_installed": _app_installed(),
                "dictation": c.dictation, "dictation_cleanup": c.dictation_cleanup,
                "dictation_model": c.dictation_model,
                "perm_input": _perm_input(), "perm_paste": _perm_paste(),
                "call_apps": [{"name": a,
                               "mode": "auto" if a in c.auto_record_apps
                               else "ignore" if a in c.ignored_apps else "ask"}
                              for a in APP_NAMES],
                "profiles": sorted(p[1] for p in self.store.get_profiles())}

    def save_settings(self, data: dict) -> None:
        c = self.cfg
        c.my_name = (data.get("my_name") or "").strip() or "Ik"
        c.whisper_model = data.get("whisper_model") or c.whisper_model
        c.vocabulary = [v.strip() for v in data.get("vocabulary", []) if v.strip()]
        c.llm_model = data.get("llm_model", "")
        c.llm_thinking = bool(data.get("llm_thinking", True))
        c.diarize = bool(data.get("diarize", True))
        c.speaker_threshold = float(data.get("speaker_threshold", c.speaker_threshold))
        c.live_transcription = bool(data.get("live_transcription", True))
        c.dictation = bool(data.get("dictation", True))
        c.dictation_cleanup = bool(data.get("dictation_cleanup", False))
        if data.get("dictation_model") and data["dictation_model"] != c.dictation_model:
            c.dictation_model = data["dictation_model"]
            if self.fn_listener:  # load the newly chosen model now, not on the next fn press
                from .asr.live import warm_up
                cfg = c
                run_async(lambda: warm_up(cfg.dictation_model, cfg.language),
                          lambda _: None, lambda e: print(f"Dicteermodel laden mislukt: {e}"))
        c.detect_calls = bool(data.get("detect_calls", True))
        if "start_at_login" in data and _app_installed():
            from . import install
            try:
                install.set_login(bool(data["start_at_login"]))
            except Exception as e:
                self.bridge.status.emit(f"Starten bij inloggen aanpassen lukte niet: {e}")
        modes = data.get("call_apps", {})
        c.auto_record_apps = [a for a, m in modes.items() if m == "auto"]
        c.ignored_apps = [a for a, m in modes.items() if m == "ignore"]
        self._update_poller()
        self._update_dictation(prompt=True)
        for name in data.get("forget_profiles", []):
            self.store.delete_profile(name)
        config.save(c)
        self.bridge.status.emit("Instellingen opgeslagen")
        self.changed()

    # --- quit -----------------------------------------------------------

    def quit(self) -> None:
        if self.recorder:
            answer = QMessageBox.question(
                None, "Opname loopt",
                "Er loopt een opname. Stoppen en op de achtergrond verwerken?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            mid = self.stop_recording(process=False)
            QProcess.startDetached(sys.executable, ["-m", "voicenotes.cli", "process", str(mid)])
        if self.jobs:
            answer = QMessageBox.question(
                None, "Nog bezig",
                "Er wordt nog een meeting verwerkt. Toch stoppen? Je kunt de meeting later "
                "opnieuw laten verwerken.")
            if answer != QMessageBox.StandardButton.Yes:
                return
            for job in list(self.jobs):
                job.proc.kill()
        if self.poller:
            self.poller.stop()
        if self.fn_listener:
            self.fn_listener.stop()
        self._close_prompt()
        self.tray.hide()
        self.app.quit()


INSTANCE_KEY = f"nl.voicenotes.app.{os.getuid()}"


def _log_to_file() -> None:
    """Without a terminal (started as an app), keep output in ~/Library/Logs/Voicenotes."""
    if sys.stdout is not None and sys.stdout.isatty():
        return
    from pathlib import Path
    log_dir = Path.home() / "Library" / "Logs" / "Voicenotes"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / "voicenotes.log"
    if log.exists() and log.stat().st_size > 5_000_000:
        log.replace(log_dir / "voicenotes.old.log")
    f = open(log, "a", buffering=1)
    f.write(f"\n--- gestart {datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
    sys.stdout = sys.stderr = f


def _signal_running_instance() -> bool:
    """If Voicenotes already runs, ask it to show its window. Returns True if it did."""
    from PySide6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"show")
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def _listen_for_instances(ctl: "Controller"):
    from PySide6.QtNetwork import QLocalServer
    QLocalServer.removeServer(INSTANCE_KEY)  # stale socket after a crash
    server = QLocalServer()
    server.listen(INSTANCE_KEY)

    def on_conn():
        conn = server.nextPendingConnection()
        conn.waitForReadyRead(300)
        if bytes(conn.readAll()) == b"show":
            ctl.show_window()
        conn.close()

    server.newConnection.connect(on_conn)
    return server


def _install_reopen_handler(ctl: "Controller"):
    """Double-clicking the app (Finder, Spotlight, Dock) while it runs opens the window."""
    try:
        import objc
        from Foundation import NSAppleEventManager, NSObject

        class _Reopen(NSObject):
            def handleReopen_withReply_(self, event, reply):  # noqa: N802
                QTimer.singleShot(0, ctl.show_window)
            handleReopen_withReply_ = objc.selector(handleReopen_withReply_,
                                                    signature=b"v@:@@")

        handler = _Reopen.alloc().init()
        k_core, k_reopen = 0x61657674, 0x72617070  # 'aevt', 'rapp'
        NSAppleEventManager.sharedAppleEventManager() \
            .setEventHandler_andSelector_forEventClass_andEventID_(
                handler, b"handleReopen:withReply:", k_core, k_reopen)
        return handler
    except Exception as e:
        print(f"Heropen-handler niet beschikbaar: {e}")
        return None


def main() -> None:
    _log_to_file()
    app = QApplication(sys.argv)
    app.setApplicationName("Voicenotes")
    app.setQuitOnLastWindowClosed(False)
    if _signal_running_instance():
        return  # the running instance shows its window
    _set_dock_visible(False)
    ctl = Controller(app)
    app._controller = ctl  # keep alive
    app._instance_server = _listen_for_instances(ctl)
    app._reopen = _install_reopen_handler(ctl)
    if "--hidden" not in sys.argv:
        ctl.show_window()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
