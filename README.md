# Auto-Cue for Engine DJ

Puts hot cues on your tracks so they land on musical phrase boundaries, then
writes them where your DJ software will find them: Engine DJ's library,
Serato's file tags (which djay Pro imports), and VirtualDJ's database.

The idea is simple. **Cue 1 goes on bar 1.** Every other cue is a fixed number
of bars after it, counted on the beat grid. Get bar 1 right and everything
else is right.

```
bar:   1        9        17       25       33       41   ...
       |--------|--------|--------|--------|--------|
cue:   1        2                 3                 4      (every 8 bars)
cue:   1                 2                 3               (every 16 bars)
```

## Install (Windows PowerShell)

```powershell
git clone <this repo> ; cd djcue2
pip install -e ".[gui]"          # CLI + web GUI + Serato tag export
pip install -e ".[beats]"        # optional: AI beat grid / downbeat detection (~500 MB)
```

The `[beats]` extra pulls in PyTorch and the Beat This! model (CPJKU, ISMIR
2024). Without it everything still works on Engine DJ's own beat grid.

## Quick start: the cue editor

```powershell
python -m autocue serve --db "C:\Users\you\Music\Engine Library\Database2\m.db"
```

Open **http://127.0.0.1:5555/editor**.

1. **Pick a track** from your library (search box on the left).
2. **Beat grid & bar 1.** The editor uses Engine DJ's grid if the track has one.
   Click **AI (Beat This!)** to build a fresh grid from the audio instead
   (~10 s). Then check bar 1: press **▶ from bar 1** to hear it, and fix it by
   clicking the waveform, pressing `B` at the playhead, or nudging by a beat or
   a bar.
3. **Generate.** Pick **Every 8 / 16 / 32 bars** (phrase-match cues from cue 1)
   or a named template, and click generate. Drag any marker, or edit the
   time, label and colour in the table.
4. **Save** to Engine DJ, Serato tags, VirtualDJ. Every write is backed up.

The older playlist review page at **http://127.0.0.1:5555/** steps through a
whole playlist one track at a time with the same engine.

## Batch from the command line

```powershell
# preview, phrase-match cues every 16 bars, 6 cues
python -m autocue batch --db "…\m.db" --playlist "Added 05.19.2026" --phrase 16 --dry-run

# write them (Engine DJ must be closed; m.db is backed up first)
python -m autocue batch --db "…\m.db" --playlist "Added 05.19.2026" --phrase 16 --overwrite

# same with a named template
python -m autocue batch --db "…\m.db" --crate "Bollywood" --template bollywood --overwrite
```

Useful flags:

| Flag | What it does |
|---|---|
| `--phrase N` / `--phrase-cues K` | cue 1 at bar 1, then a cue every N bars, K cues (default 6) |
| `--template NAME` | `edm`, `djedit`, `bollywood`, `bhangra`, `phrase-16`, `phrase-16-8`, or a `.yaml` path |
| `--grid auto\|engine\|ai` | which beat grid to count bars on. `auto` = Engine's, or AI when the track has none |
| `--anchor auto\|main-cue\|ai\|grid` | how to find bar 1. `auto` = your main cue, else AI downbeat, else the grid |
| `--beat-offset N` | shift every cue by N beats (negative allowed) |
| `--write-grid` | also replace Engine DJ's beat grid with the AI one (only when `--grid ai` produced it) |
| `--overwrite` | replace cues that already exist |
| `--max-duration S` | skip tracks longer than S seconds (default 900) |

Other commands: `list-playlists`, `list-crates`, `inspect <track>`,
`analyze <track>` (single track), `set <track> --cue N --at m:ss`,
`roundtrip` (verifies the codec reproduces your library's blobs byte for byte).

## How bar 1 is found

Engine DJ's grid is good at **tempo** and unreliable about **phase** (which
beat is the 1, and grids that start a beat before the intro). So the tool never
trusts the grid for bar 1. In order:

1. **Your main cue.** Engine (and you) put the load cue on the first downbeat.
2. **AI downbeat.** Beat This! finds beats and downbeats; any downbeat it
   reports inside the leading silence is discarded.
3. **The grid's own first downbeat**, as a last resort.

Every run prints all three so you can see when they disagree, and the editor
lets you override by ear.

## Where cues are written

| Software | Mechanism | Notes |
|---|---|---|
| Engine DJ | `m.db` quickCues blob | schema 2.18–3.0.2; refuses to write while Engine is open |
| Serato DJ | "Serato Markers2" tag in the audio file | MP3, M4A, AIFF, WAV, FLAC; audio data untouched |
| djay Pro | reads the Serato tag | re-import or re-analyze the track in djay after saving |
| VirtualDJ | `database.xml` | auto-detected in Documents\VirtualDJ, or `serve --vdj-db PATH` |

## Templates

A template is a small YAML file mapping cue slots to bar numbers:

```yaml
name: My phrasing
cues:
  1: {detect: bar_1,  label: Intro,  color: yellow}
  2: {detect: bar_9,  label: Verse,  color: orange}
  3: {detect: bar_25, label: Chorus, color: purple}
```

Colours: yellow, orange, purple, red, green, teal, cyan, blue. Pass a path
with `--template my.yaml`. (Templates can also use the older audio-analysis
keys `mix_in`, `first_chorus`, `outro_start`…, which need
`pip install -e ".[analysis]"`; phrase-based cueing does not.)

## Safety

- Never writes while Engine DJ (or VirtualDJ) is running.
- Backs up `m.db`, `database.xml`, and each file's previous Serato tag before
  writing. Backups land next to the originals (`m_backup_<date>.db`,
  `Database2/autocue_backups/`).
- The blob codec is verified to round-trip real library data byte for byte
  (`python -m autocue roundtrip`).

## Development

```
pip install -e ".[gui,dev]"
python -m pytest
```
