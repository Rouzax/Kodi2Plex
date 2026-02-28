# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [2.1.0] - 2026-02-28

### Added

- Interactive mode (`--interactive` / `-i`) to create title overrides for unmatched shows
- Shows top 5 fuzzy candidates for each unmatched title
- New mappings saved automatically to `title_overrides` in config file
- Supports pick-by-number, type exact title, skip, or quit

## [2.0.0] - 2026-02-27

### Changed

- **Breaking**: Replaced XSP file parsing with Kodi JSON-RPC
  - `playlist_path` config replaced by `kodi` object with `url`, `playlist`, `username`, `password`
  - Removed `-p` / `--playlist` CLI argument
  - Playlist rules are now evaluated by Kodi — always matches what you see in Kodi
- No longer requires access to Kodi's filesystem

### Added

- Kodi JSON-RPC integration via `Files.GetDirectory`
- Optional HTTP Basic Auth for Kodi web interface

## [1.1.0] - 2026-02-26

### Added

- Pushover notification support (shows added, removed, and not found)
- Notifications only sent when changes are made and not during dry runs
- No extra dependencies — uses Python's built-in urllib
- Title overrides: manually map Kodi titles to exact Plex titles for shows that fuzzy matching can't resolve

## [1.0.0] - 2026-02-20

### Added

- Initial release
- Kodi Smart Playlist (.xsp) parsing
- Fuzzy title matching with configurable threshold
- Full sync: add missing shows, remove stale ones
- Multi-library support
- Dry run mode for previewing changes
- Color-coded console output
- Optional log file
- CLI argument overrides for all config settings
- Title normalization: articles, punctuation, year suffixes, ampersands
- Guarded matching to prevent false positives from substring matches
