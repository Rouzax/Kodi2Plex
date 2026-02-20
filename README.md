# Kodi2Plex

Sync a [Kodi Smart Playlist](https://kodi.wiki/view/Smart_playlists) (.xsp) to a [Plex](https://www.plex.tv/) Collection or Playlist using fuzzy title matching.

## Features

- **Full sync** — adds missing shows and removes stale ones
- **Collection or Playlist** — sync to either a Plex collection or a Plex playlist
- **Multi-library** — searches across multiple Plex TV show libraries
- **Fuzzy matching** — normalizes articles, punctuation, year suffixes, and ampersands for reliable matching
- **Guarded matching** — prevents false positives from short substring matches
- **Dry run mode** — preview all changes before applying
- **Configurable threshold** — tune fuzzy match sensitivity per environment
- **Logging** — color-coded console output with optional log file

## Requirements

- Python 3.10+
- A Plex server with a valid [Plex Token](#finding-your-plex-token)
- A Kodi Smart Playlist file (`.xsp`)

## Installation

```bash
git clone https://github.com/Rouzax/Kodi2Plex.git
cd Kodi2Plex
pip install -r requirements.txt
```

## Quick Start

1. Copy the example config and fill in your details:

   ```bash
   cp config.json.example config.json
   ```

2. Edit `config.json` with your Plex server URL, token, and library names.

3. Preview the sync (no changes made):

   ```bash
   python kodi2plex.py --dry-run
   ```

4. Run the sync:

   ```bash
   python kodi2plex.py
   ```

## Configuration

All settings are stored in `config.json`:

| Setting           | Type       | Description                                         | Default        |
|-------------------|------------|-----------------------------------------------------|----------------|
| `plex_url`        | `string`   | Plex server URL                                     | —              |
| `plex_token`      | `string`   | Plex authentication token                           | —              |
| `library_names`   | `string[]` | Plex TV show library names to search                | —              |
| `sync_mode`       | `string\|string[]` | `"collection"`, `"playlist"`, or both       | `"collection"` |
| `collection_name` | `string`   | Target name (`null` = use playlist name)            | `null`         |
| `playlist_path`   | `string`   | Path to Kodi `.xsp` file                            | —              |
| `log_file`        | `string`   | Log file path (`null` = console only)               | `null`         |
| `fuzzy_threshold` | `int`      | Minimum fuzzy match score, 0–100                    | `80`           |
| `dry_run`         | `bool`     | Preview mode — no changes made to Plex              | `false`        |

### Sync Modes

**Collection** (default) — shows appear as a browsable collection within your Plex library. Collections are visible to all users with access to the library.

**Playlist** — shows appear in the Plex sidebar under Playlists. Playlists are per-user and support custom ordering.

**Both** — sync to both a collection and a playlist in a single run.

```json
"sync_mode": "collection"
```

```json
"sync_mode": "playlist"
```

```json
"sync_mode": ["collection", "playlist"]
```

You can also override the mode via CLI:

```bash
python kodi2plex.py --mode playlist
python kodi2plex.py --mode collection playlist
```

### Multi-Library Support

Search across multiple Plex libraries by listing them in the config:

```json
"library_names": ["TV Shows - EN", "TV Shows - NL"]
```

For backwards compatibility, a single string via `"library_name": "TV Shows"` also works.

### Finding Your Plex Token

1. Open Plex Web and browse to any media item
2. Click **Get Info** → **View XML**
3. The URL will contain `X-Plex-Token=xxxx` — that's your token

## Usage

```bash
# Run with config.json in the current directory
python kodi2plex.py

# Specify a different config file
python kodi2plex.py -c /path/to/config.json

# Override playlist path
python kodi2plex.py -p "/path/to/playlist.xsp"

# Override target name
python kodi2plex.py -n "My TV Shows"

# Sync as playlist instead of collection
python kodi2plex.py --mode playlist

# Sync to both collection and playlist
python kodi2plex.py --mode collection playlist

# Dry run — preview changes without modifying Plex
python kodi2plex.py --dry-run

# Enable log file
python kodi2plex.py --log sync.log
```

All CLI arguments override their `config.json` equivalents.

## Matching Logic

Titles are normalized before fuzzy comparison:

1. Lowercased
2. `&` and `&amp;` replaced with `and`
3. Leading articles stripped (`The`, `A`, `An`)
4. Year suffixes removed — e.g. `(2014)`, `(2024)`
5. Punctuation removed
6. Whitespace collapsed

Three fuzzy strategies are evaluated per candidate (best score wins):

| Strategy            | Use Case                          | Guard                              |
|---------------------|-----------------------------------|------------------------------------|
| `fuzz.ratio`        | Straight character comparison     | Always used                        |
| `fuzz.token_sort_ratio` | Word-order independent        | Always used                        |
| `fuzz.partial_ratio`    | Substring matching            | Only when title lengths are within 2× |

A match requires **both**:

- The best combined score meets the configured `fuzzy_threshold`
- The basic `fuzz.ratio` score is at least **70** (prevents spurious partial matches)

When candidates tie on combined score, the basic `ratio` is used as a tiebreaker — ensuring an exact title match always wins over a substring match.

### Example Output

```
✓ 'Castlevania' (100%)
✓ 'Castlevania: Nocturne' (100%)
✓ 'Cosmos' → 'Cosmos (2014)' (100%)
✓ 'Mr. & Mrs. Smith' → 'Mr. & Mrs. Smith (2024)' (100%)
✗ 'The Marvelous Mrs. Maisel' — no match (best score: 56%)
```

## License

[MIT](LICENSE)
