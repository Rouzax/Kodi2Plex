# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0] - 2026-02-20

### Added

- Playlist sync mode as alternative to collections (`"sync_mode": "playlist"`)
- Multi-mode support — sync to both collection and playlist in one run (`"sync_mode": ["collection", "playlist"]`)
- CLI `--mode` flag to override sync mode(s)
- CLI `-n` / `--name` flag for target name (replaces `--collection-name`)

### Changed

- Summary now shows per-mode stats when running multiple sync modes
- "Already in collection" renamed to "Already synced" for mode neutrality

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
