# Voicenotes

Lokale meeting-transcriptie (Nederlands) met samenvattingen en Q&A via LM Studio. Alles draait op je eigen Mac.

## Vereisten
- Apple Silicon Mac, [uv](https://docs.astral.sh/uv/)
- LM Studio met de server aan (`lms server start`), een LLM geladen en optioneel `nomic-embed-text-v1.5` voor Q&A

## Installatie
```sh
uv sync
cd swift/audiotap && swift build -c release   # helper voor microfoon, systeemaudio en call-detectie (macOS 14.2+)
```

## App installeren
```sh
uv run voicenotes install-app            # bouwt ~/Applications/Voicenotes.app, start bij inloggen
uv run voicenotes install-app --no-login # zonder starten bij inloggen
```
Daarna start je Voicenotes zoals elke app (Spotlight, Launchpad of Finder). Bij inloggen start hij op de achtergrond met alleen het icoon in de menubalk; dat zet je aan of uit bij Instellingen → "Openen bij inloggen". Uitvoer staat in `~/Library/Logs/Voicenotes/voicenotes.log`.

De app bevat alleen een klein opstartprogramma (`macos/launcher.swift`) dat Python in het app-proces laadt; de code en pakketten blijven in deze projectmap. Codewijzigingen werken dus direct na een herstart van de app, zonder opnieuw te bouwen. Opnieuw bouwen is alleen nodig als het opstartprogramma verandert of de Python-omgeving verhuist, en dan vraagt macOS de toestemmingen opnieuw.

Voor ontwikkelen kan het nog steeds vanuit de terminal:
```sh
uv run voicenotes app            # menubalk-icoon + venster
uv run voicenotes app --hidden   # alleen het menubalk-icoon
```
Via het microfoon-icoon in de menubalk of de rode knop in het venster start en stop je een opname (sneltoets ⌘R). Tijdens de opname lees je live mee; na het stoppen maakt Voicenotes een nauwkeurig transcript met namen per spreker en een samenvatting. De spreekstrook boven elke meeting laat zien wie wanneer sprak; klik erop om naar dat moment te springen. Onderaan stel je vragen over één of alle meetings. Instellingen: ⌘, .

**Dicteren met fn:** houd fn ingedrukt, praat, en laat los: de tekst verschijnt waar je cursor staat, in elke app. Tik twee keer kort op fn om handsfree te dicteren tot je fn weer indrukt. Fn samen met een andere toets (zoals fn+F5) blijft gewoon werken. Stopwoorden als "eh" en "ehm" worden altijd weggehaald; in de instellingen kun je de tekst ook laten opschonen door het taalmodel (ongeveer 1 seconde extra). Dicteer je tijdens een meetingopname, dan komt dat stuk niet in het meetingtranscript.

Eenmalig nodig: toestemming voor *Invoerbewaking* (fn herkennen) en *Toegankelijkheid* (plakken) in Systeeminstellingen → Privacy en beveiliging, voor Voicenotes (of voor Terminal als je de app vanuit de terminal start). Zet daarnaast in Toetsenbord-instellingen "Druk op 🌐-toets om" op "Niets doen", en kies in Wispr Flow een andere toets als die ook fn gebruikt.

**Calls herkennen:** zodra Teams, WhatsApp, Zoom, FaceTime, Slack, Discord, Webex of een browser je microfoon een paar seconden gebruikt, vraagt Voicenotes rechtsboven of je wilt opnemen. Per app kun je in de instellingen kiezen voor *Vragen*, *Altijd opnemen* of *Nooit*. Een opname die door een call is gestart, stopt vanzelf zo'n 10 seconden nadat de call is afgelopen.

**Mappen:** orden meetings per onderwerp. Maak een map met + in de zijbalk, sleep meetings erheen of kies de map bovenaan een meeting. Nieuwe opnames en imports komen in de map die open staat. Met "hele map" stel je vragen over alle meetings in die map.

De interface is HTML/CSS/JS (`src/voicenotes/web/`) in een QtWebEngine-venster, gekoppeld aan Python via QWebChannel (`ui/bridge.py`).

## CLI
```sh
uv run voicenotes record -t "Weekstart"      # mic + al het laptopgeluid; Ctrl+C = stop + verwerken
uv run voicenotes record --no-mic            # alleen systeemgeluid (bijv. een opname afspelen)
uv run voicenotes speakers 1                 # sprekers van een meeting
uv run voicenotes name 1 "Spreker 1" "Jan"   # naam geven, stem wordt onthouden
uv run voicenotes transcribe opname.m4a -t "Weekstart"   # transcript + samenvatting
uv run voicenotes list
uv run voicenotes show 1            # samenvatting
uv run voicenotes show 1 -T         # transcript
uv run voicenotes ask "Wat zijn mijn actiepunten?"        # over alle meetings
uv run voicenotes ask -m 1 "Wanneer is de deadline?"
uv run voicenotes config            # pad en inhoud van config.toml
uv run voicenotes folders           # mappen
uv run voicenotes move 3 "Klant X"  # meeting naar map (map wordt zo nodig aangemaakt)
uv run voicenotes list -f "Klant X"
```

Data en instellingen staan in `~/Library/Application Support/Voicenotes/`. Dat pad kun je overschrijven met `VOICENOTES_HOME`.
Zet namen en vakjargon in `vocabulary` in `config.toml` voor betere spelling.

## Status
Alle fases uit het plan zijn klaar: bestanden transcriberen, samenvatten, Q&A, sprekerherkenning met stemprofielen, opnemen van microfoon plus systeemaudio, de menubalk-app, live transcriptie (parakeet), call-detectie, mappen en dicteren met fn. De app is te installeren als Voicenotes.app en start bij inloggen.

## Bekend probleem: iCloud en `.venv`
Dit project staat in Documenten, dat met iCloud wordt gesynchroniseerd. iCloud markeert bestanden in `.venv` als verborgen, en Python en Qt slaan die over ("No module named voicenotes"). Daarom is `.venv` een symlink naar `~/.venvs/voicenotes`, buiten iCloud. Op een nieuwe machine:
```sh
uv venv --python 3.12 ~/.venvs/voicenotes && ln -s ~/.venvs/voicenotes .venv && uv sync
```
