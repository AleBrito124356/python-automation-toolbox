# Changelog

## Unreleased

Every change below is covered by the offline test-suite (`python -m pytest`, 183 tests).

### Fixed — data loss and silent corruption

- **04_folder_backup**: rotation deleted the archive it had just written and counted other folders' backups as its own (`proj_old_*.zip` when backing up `proj`). It now matches only `<name>_YYYYmmdd-HHMMSS[_n].zip`, orders by the embedded timestamp and never deletes the archive from the same run. Two runs in the same second no longer overwrite each other, and a half-written archive (`.partial`) is never counted as a backup.
- **03_bulk_rename**: chained renames (`a → zz_2` while `zz_2 → zz_3`) crashed halfway on Windows or overwrote a file on Linux/macOS, and left no undo log. Renames are now two-phase through temporary names, roll back on any error, and the undo log is written before the first rename.
- **05_image_batch**: without `--format`, JPEGs were saved as PNG data with a `.jpg` name (so `--quality` and EXIF were lost); the source format is now kept. Rotated photos kept `Orientation=6` after the pixels were rotated, so viewers turned them twice; the tag is now reset to 1. Two sources that map to the same output name no longer overwrite each other.
- **08_csv_excel**: names such as `Nan` or `Infinity` were turned into empty cells and codes such as `1e5` or `1_000` into numbers. Number detection is strict now.
- **11_wifi_qr / 13_qr_generator**: with redirected output on Windows the scripts crashed before writing the PNG. The PNG is written first and the preview falls back to `#` characters.
- **14_uptime_checker**: printed "Webhook alert sent" when the webhook answered 404, and in `--loop` mode re-sent the same alert every cycle.

### Added

- **02_duplicate_finder**: `--dry-run`, `--keep newest|oldest|shortest-path`, `--json`, `--exclude`, and `--undo MANIFEST`. It refuses to run unattended without a policy instead of crashing with `EOFError`. The quarantine mirrors the original folders. Hard links, symlinks and `.git`/`.hg`/`.svn` are skipped.
- **10_downloads_cleaner**: `--undo LOG`. Skips `desktop.ini`, `Thumbs.db`, system files and in-progress downloads (`.crdownload`, `.part`, …). A locked file is reported and skipped.
- **01_file_organizer**: `--undo` also removes the category and date folders it emptied.
- **04_folder_backup**: `--verify ZIP…` (checks the CRC of every member and compares the archive with its manifest, exit 1 on mismatch) and `--verify-after`.
- **14_uptime_checker**: UP/DOWN/SLOW states with `expect_text` and `max_latency_ms`. A state file makes alerts fire on changes only, with a RECOVERED message that includes the outage length. Also `--alert-every`, `--json`, `--state`, and a list of codes for `expect_status`. A failed webhook is retried once and kept pending for the next run. Output is line-buffered. Exit codes: 0 up, 1 down/slow, 2 config error.
- **08_csv_excel**: `--decimal-comma` (read and write `3,50` / `1.234,56`), `--allow-exponent`, `--encoding` with automatic cp1252 fallback.
- **06_pdf_tools**: open ranges (`5-`, `-3`), `--password` for encrypted inputs, one-line errors for bad page specs, and a refusal to overwrite an input file.
- **19_currency_converter**: unknown currency codes are reported as such (HTTP 4xx), not as "offline". Offline conversions can use any cached base (cross rates).
- **13_qr_generator**: RFC 2426 escaping for vCards, `Family, Given` names, and `mailto:`/`tel:` links left untouched.
- Tests: `tests/` with a local HTTP server, fakes for edge-tts, pyperclip, mss and winreg, fixture JSON for Open-Meteo and Frankfurter, and a network guard that also covers subprocesses. `requirements-dev.txt`, `pytest.ini`.

### Changed

- `17_screenshot_tool` uses `mss.MSS` (no more deprecation warning on mss 10). `--list` no longer creates the output folder.
- `05_image_batch` exits 1 if any image failed. `--resize` and `--quality` are validated by argparse.
- `03_bulk_rename` logs are now `{"version": 2, "status": ..., "entries": [...]}`. Older list-style logs still undo.
- `10_downloads_cleaner --days` accepts fractions (e.g. `--days 0.5`).
