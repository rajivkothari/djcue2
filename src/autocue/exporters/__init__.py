"""Cue-point exporters for other DJ software.

Engine DJ is written directly through autocue.db. These modules cover
the rest:

  serato  — Serato's tag format written into the audio file itself.
            Serato DJ reads it natively; djay Pro and VirtualDJ import it.
  vdj     — VirtualDJ's database.xml (direct write, with backup).

Each exporter is independent and only imported when its target is used.
"""
