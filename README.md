# Instant Notes

Tiny Windows background note taker.

- `F9`: open a blank note immediately.
- `F10`: open the note list, sorted newest first.
- Close a note window whenever you want. It saves locally, queues sync, then generates a title in the background.
- Notes live in `instant-notes.db` next to the app.

## Run

```powershell
.\start-instant-notes.ps1
```

## Stop

```powershell
.\stop-instant-notes.ps1
```

## Start With Windows

```powershell
.\install-startup.ps1
```

## OpenAI Titles

Create a local `.env` file next to `instant_notes.pyw` and set `OPENAI_API_KEY` there before starting the app. The default title model is `gpt-4.1-mini`; override it with `OPENAI_NOTES_MODEL` if you want.

If no API key is present, the app still works and uses a local fallback title. Those rows are marked with `title_status = missing_api_key` in SQLite.

## Future Online Sync

Every closed note and generated title is written to `sync_queue`. When the online database exists, either consume that table or set `INSTANT_NOTES_SYNC_URL` to an HTTP endpoint and the background worker will POST queued note upserts to it.

## Top Row Keys

The app listens for normal `F9`/`F10`, their physical scan codes, and the HP-style bare action keys where `F9` sends previous-track and `F10` sends play/pause.

The defaults are stored in `instant-notes.json`:

- `new`: `F9`, scan code `67`, media previous-track
- `list`: `F10`, scan code `68`, media play/pause
