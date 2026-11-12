"""JS <-> Python bridge for the HTML interface (exposed via QWebChannel as `vn`)."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import markdown as md
from PySide6.QtCore import QObject, Signal, Slot

from ..names import render
from ..store import MIC_LABEL

if TYPE_CHECKING:
    from ..app import Controller


def _plain(markdown_text: str) -> str:
    """Markdown -> plain text for searching."""
    import re
    t = re.sub(r"^#+\s*", "", markdown_text, flags=re.M)
    t = re.sub(r"^\s*[-*]\s*(\[[ x]\]\s*)?", "", t, flags=re.M)
    return re.sub(r"[*_`]", "", t)


def md_html(text: str) -> str:
    html = md.markdown(text or "", extensions=["sane_lists"])
    # Render "[ ]" / "[x]" task items as checkboxes.
    return (html.replace("<li>[ ] ", '<li class="task"><span class="box"></span>')
                .replace("<li>[x] ", '<li class="task done"><span class="box"></span>'))


class Bridge(QObject):
    # Python -> JS
    meetingsChanged = Signal()
    meetingChanged = Signal(int)
    peopleChanged = Signal()
    status = Signal(str)
    recording = Signal(str)      # {"active": bool, "id": int|null}
    live = Signal(str)           # {"id": int, "segments": [...]}
    levels = Signal(str)         # {"mic": f, "system": f, "elapsed": s}
    progress = Signal(str)       # {"id": int|null, "label": str, "line": str, "done": bool, "ok": bool}
    answer = Signal(str)         # {"req": str, "html": str, "error": str|null}
    theme = Signal(str)          # "light" | "dark"
    command = Signal(str)        # menu bar action for the page: "rename", "tab:summary", ...

    def __init__(self, ctl: "Controller"):
        super().__init__()
        self.ctl = ctl

    # --- reading ----------------------------------------------------------

    @Slot(result=str)
    def listMeetings(self) -> str:
        c = self.ctl
        return json.dumps([{
            "id": m.id, "title": m.title, "created": m.created_at, "source": m.source,
            "busy": m.id in c.busy, "recording": m.id == c.rec_meeting,
            "hasSummary": bool(m.summary), "folder": m.folder_id,
            # for the sidebar search: the summary as plain text, with today's names
            "summaryText": _plain(render(m.summary, c.store.get_meeting_speakers(m.id)))
                           if m.summary else "",
        } for m in c.store.list_meetings()])

    @Slot(result=str)
    def listFolders(self) -> str:
        return json.dumps(self.ctl.store.list_folders())

    @Slot(int, result=str)
    def getMeeting(self, mid: int) -> str:
        c = self.ctl
        try:
            m = c.store.get_meeting(mid)
        except KeyError:
            return "null"
        segments = c.store.get_segments(mid)
        sps = c.store.get_meeting_speakers(mid)
        names = {sp.label: sp.name for sp in sps}
        speakers = []
        for sp in sps:
            mine = [s for s in segments if s.speaker == sp.label]
            if not mine:
                continue
            speakers.append({
                "label": sp.label, "tag": sp.tag, "name": sp.name,
                "personId": sp.person_id, "defaultName": sp.default_name,
                "talk": sum(s.end - s.start for s in mine), "turns": len(mine),
                "sample": max(mine, key=lambda s: len(s.text)).text,
                "me": sp.is_me, "linked": sp.person_id is not None,
                "voiceKnown": sp.person_id is not None and any(
                    p[0] == sp.person_id for p in c.store.voice_profiles()),
                "mic": sp.label == MIC_LABEL,
            })
        return json.dumps({
            "id": m.id, "title": m.title, "created": m.created_at, "source": m.source,
            "dir": str(m.dir), "folder": m.folder_id,
            # Summaries contain codes like [S2]; fill in today's names.
            "summaryHtml": md_html(render(m.summary, sps)) if m.summary else "",
            "segments": [{"start": s.start, "end": s.end, "speaker": names.get(s.speaker, s.speaker),
                          "text": s.text} for s in segments],
            "speakers": speakers,
            "me": next((sp.name for sp in sps if sp.is_me), c.cfg.my_name),
            "busy": m.id in c.busy, "recording": m.id == c.rec_meeting,
            "progress": c.progress_text.get(m.id, ""),
        })

    @Slot(result=str)
    def getLive(self) -> str:
        return json.dumps(self.ctl.live_payload())

    @Slot(result=str)
    def getState(self) -> str:
        c = self.ctl
        return json.dumps({"recording": c.rec_meeting, "theme": c.body_classes(),
                           "me": c.cfg.my_name, "live": c.cfg.live_transcription})

    @Slot(int, int)
    def setSelection(self, mid: int, folder: int) -> None:
        """The page tells which meeting/folder is selected (-1 = none), for the menu bar."""
        self.ctl.set_selection(None if mid < 0 else mid, None if folder < 0 else folder)

    # --- actions ----------------------------------------------------------

    @Slot()
    def toggleRecording(self) -> None:
        self.ctl.toggle_recording()

    @Slot()
    def startRecording(self) -> None:
        self.ctl.start_recording()

    @Slot()
    def stopRecording(self) -> None:
        self.ctl.stop_recording()

    @Slot()
    def importFile(self) -> None:
        self.ctl.pick_and_import()

    @Slot(int, str)
    def rename(self, mid: int, title: str) -> None:
        if title.strip():
            self.ctl.store.rename_meeting(mid, title.strip())
            self.meetingsChanged.emit()

    @Slot(int)
    def deleteMeeting(self, mid: int) -> None:
        self.ctl.delete_meeting(mid)

    @Slot(int)
    def resummarize(self, mid: int) -> None:
        self.ctl.resummarize(mid)

    @Slot(int)
    def reprocess(self, mid: int) -> None:
        self.ctl.reprocess(mid)

    @Slot(int)
    def reveal(self, mid: int) -> None:
        import subprocess
        subprocess.run(["open", str(self.ctl.store.get_meeting(mid).dir)])

    # --- folders ----------------------------------------------------------

    @Slot(int)
    def setActiveFolder(self, fid: int) -> None:
        """New recordings and imports go into this folder (-1 = none)."""
        self.ctl.active_folder = None if fid < 0 else fid

    @Slot(str, result=int)
    def createFolder(self, name: str) -> int:
        try:
            fid = self.ctl.store.create_folder(name)
        except ValueError as e:
            self.status.emit(str(e))
            return -1
        self.meetingsChanged.emit()
        return fid

    @Slot(int, str)
    def renameFolder(self, fid: int, name: str) -> None:
        try:
            self.ctl.store.rename_folder(fid, name)
        except ValueError as e:
            self.status.emit(str(e))
        self.meetingsChanged.emit()

    @Slot(int)
    def deleteFolder(self, fid: int) -> None:
        self.ctl.store.delete_folder(fid)
        if self.ctl.active_folder == fid:
            self.ctl.active_folder = None
        self.meetingsChanged.emit()

    @Slot(int, int)
    def moveMeeting(self, mid: int, fid: int) -> None:
        self.ctl.store.set_meeting_folder(mid, None if fid < 0 else fid)
        self.meetingsChanged.emit()
        self.meetingChanged.emit(mid)

    # --- people -----------------------------------------------------------

    @Slot(int, str, str)
    def assignSpeaker(self, mid: int, label: str, person: str) -> None:
        """Link a speaker to a person by name ("" = unlink). Instant: no new summary needed."""
        self.ctl.assign_speaker(mid, label, person)

    @Slot(result=str)
    def listPeople(self) -> str:
        return json.dumps(self.ctl.store.list_people())

    @Slot(str, result=int)
    def createPerson(self, name: str) -> int:
        try:
            pid = self.ctl.store.create_person(name)
        except ValueError as e:
            self.status.emit(str(e))
            return -1
        self.ctl.people_changed()
        return pid

    @Slot(int, str)
    def renamePerson(self, pid: int, name: str) -> None:
        try:
            self.ctl.store.rename_person(pid, name)
        except ValueError as e:
            self.status.emit(str(e))
        self.ctl.people_changed()

    @Slot(int, int)
    def mergePeople(self, source: int, target: int) -> None:
        self.ctl.store.merge_people(source, target)
        self.ctl.people_changed()

    @Slot(int)
    def deletePerson(self, pid: int) -> None:
        try:
            self.ctl.store.delete_person(pid)
        except ValueError as e:
            self.status.emit(str(e))
        self.ctl.people_changed()

    @Slot(str, str, int, int)
    def ask(self, req: str, question: str, mid: int, folder: int) -> None:
        """mid >= 0: one meeting. Otherwise folder >= 0: that folder. Otherwise everything."""
        def done(text: str):
            self.answer.emit(json.dumps({"req": req, "html": md_html(text), "error": None}))

        def failed(err: str):
            self.answer.emit(json.dumps({"req": req, "html": "", "error": err}))

        self.ctl.ask(question, None if mid < 0 else mid, done, failed,
                     folder_id=None if mid >= 0 or folder < 0 else folder)

    @Slot(result=str)
    def getSettings(self) -> str:
        return json.dumps(self.ctl.settings_payload())

    @Slot(str)
    def openSystemSettings(self, pane: str) -> None:
        import subprocess
        urls = {
            "input": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
            "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            "keyboard": "x-apple.systempreferences:com.apple.Keyboard-Settings.extension",
        }
        if pane in urls:
            subprocess.run(["open", urls[pane]])

    @Slot(str)
    def saveSettings(self, payload: str) -> None:
        self.ctl.save_settings(json.loads(payload))
