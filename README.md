# Kodi2Plex

<p align="center">
  <img src="icon.svg" width="128" height="128" alt="Kodi2Plex Icon"/>
</p>

Sync a [Kodi Smart Playlist](https://kodi.wiki/view/Smart_playlists) to a [Plex](https://www.plex.tv/) Collection using fuzzy title matching.

Kodi2Plex connects to your Kodi instance over [JSON-RPC](https://kodi.wiki/view/JSON-RPC_API), fetches the resolved list of TV shows from a smart playlist, and synchronizes them into a Plex collection. Fuzzy matching handles title differences between the two libraries — year suffixes, articles, punctuation, and naming variations are all normalized automatically.

## Features

- **Full sync** — adds missing shows and removes stale ones from the collection
- **Kodi JSON-RPC** — fetches the resolved smart playlist directly from Kodi over HTTP
- **Multi-library** — searches across multiple Plex TV show libraries
- **Fuzzy matching** — normalizes articles, punctuation, year suffixes, and ampersands for reliable matching
- **Guarded matching** — prevents false positives from short substring matches
- **Title overrides** — manually map titles that fuzzy matching can't resolve
- **Interactive mode** — interactively create overrides for unmatched shows, saved to config
- **Dry run mode** — preview all changes before applying
- **Configurable threshold** — tune fuzzy match sensitivity per environment
- **Logging** — color-coded console output with optional log file
- **Pushover notifications** — optional push notifications when changes are made
- **No extra network dependencies** — Pushover and Kodi JSON-RPC use Python's built-in `urllib`

## How It Works

1. Connects to Kodi via JSON-RPC and fetches the smart playlist (playlist rules are evaluated by Kodi)
2. Connects to Plex and retrieves all shows from the configured libraries
3. Checks title overrides first (exact match), then falls back to fuzzy matching
4. Compares matched shows against the existing Plex collection
5. Adds missing shows and removes stale ones (full sync)
6. Optionally sends a Pushover notification with the changes

## Requirements

- Python 3.10+
- A Kodi instance with the [web interface enabled](#enabling-kodis-web-interface)
- A Plex server with a valid [Plex token](#finding-your-plex-token)
- Dependencies: [`plexapi`](https://github.com/pkkid/python-plexapi), [`thefuzz`](https://github.com/seatgeek/thefuzz)

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

2. Edit `config.json` with your Kodi and Plex connection details.

3. Preview the sync (no changes made):

   ```bash
   python kodi2plex.py --dry-run
   ```

4. Interactively resolve any unmatched titles:

   ```bash
   python kodi2plex.py --interactive --dry-run
   ```

5. Run the sync:

   ```bash
   python kodi2plex.py
   ```

## Configuration

All settings are stored in `config.json`. Copy `config.json.example` to get started.

| Setting           | Type       | Description                                         | Default  |
|-------------------|------------|-----------------------------------------------------|----------|
| `kodi`            | `object`   | Kodi connection settings ([details](#kodi-settings)) | —       |
| `plex_url`        | `string`   | Plex server URL                                     | —        |
| `plex_token`      | `string`   | Plex authentication token                           | —        |
| `library_names`   | `string[]` | Plex TV show library names to search                | —        |
| `collection_name` | `string`   | Collection name (`null` = use playlist name)        | `null`   |
| `log_file`        | `string`   | Log file path (`null` = console only)               | `null`   |
| `fuzzy_threshold` | `int`      | Minimum fuzzy match score, 0–100                    | `80`     |
| `dry_run`         | `bool`     | Preview mode — no changes made to Plex              | `false`  |
| `pushover`        | `object`   | Pushover notification settings ([details](#pushover-notifications)) | `null` |
| `title_overrides` | `object`   | Manual Kodi → Plex title mappings ([details](#title-overrides)) | `{}` |

### Kodi Settings

```json
"kodi": {
    "url": "http://kodi.local:8080",
    "playlist": "TV Shows",
    "username": null,
    "password": null
}
```

| Setting    | Description                                           |
|------------|-------------------------------------------------------|
| `url`      | Kodi web interface URL (default port: 8080)           |
| `playlist` | Name of the Smart Playlist (without `.xsp` extension) |
| `username` | HTTP username (`null` if no authentication)           |
| `password` | HTTP password (`null` if no authentication)           |

The script uses Kodi's `Files.GetDirectory` JSON-RPC method to fetch the resolved list of shows from the smart playlist. This means the playlist rules are evaluated by Kodi itself — you always get the same results as you see in Kodi.

#### Enabling Kodi's Web Interface

1. Go to **Settings** → **Services** → **Control**
2. Enable **Allow remote control via HTTP**
3. Set a port (default: 8080)
4. Optionally set a username and password

### Multi-Library Support

Search across multiple Plex libraries by listing them in the config:

```json
"library_names": ["TV Shows - EN", "TV Shows - NL"]
```

Shows are aggregated from all libraries and matched against the playlist. Collections are managed within each library where the matched shows reside.

For backwards compatibility, a single string via `"library_name": "TV Shows"` also works.

### Title Overrides

When fuzzy matching can't find a show — for example because the names are too different — you can manually map Kodi playlist titles to their exact Plex titles:

```json
"title_overrides": {
    "Clarkson's Farm": "Clarkson's Farm (2021)",
    "The Great": "The Great (2020)",
    "James May: Our Man in…": "James May: Our Man In..."
}
```

The left side is the title as it appears in Kodi. The right side must be the **exact title** as shown in your Plex library. Overrides bypass fuzzy matching entirely — if the Plex title doesn't exist, it's reported as not found.

The easiest way to build overrides is with [interactive mode](#interactive-mode) (`--interactive`), which lets you pick from candidates and saves them to your config automatically.

### Pushover Notifications

Get push notifications on your phone when the collection changes.

```json
"pushover": {
    "user_key": "YOUR_PUSHOVER_USER_KEY",
    "app_token": "YOUR_PUSHOVER_APP_TOKEN"
}
```

Set to `null` to disable notifications.

Notification behavior:

- Only sent when shows are actually added or removed
- Skipped during dry runs
- Includes which shows were added, removed, and not found in Plex
- Failures to send are logged but don't break the sync

To get your keys:

1. Sign up or log in at [pushover.net](https://pushover.net/)
2. Your **User Key** is on the main dashboard
3. Go to **Your Applications** → **Create an Application/API Token** to get your **App Token**

### Finding Your Plex Token

1. Open Plex Web and browse to any media item
2. Click **Get Info** → **View XML**
3. The URL will contain `X-Plex-Token=xxxx` — that's your token

## Usage

```
usage: kodi2plex.py [-h] [-c CONFIG] [-n COLLECTION_NAME] [--dry-run]
                    [-i] [--log LOG]

Kodi2Plex — Sync a Kodi Smart Playlist to a Plex Collection.

options:
  -h, --help            show this help message and exit
  -c, --config CONFIG   Path to JSON config file (default: config.json)
  -n, --collection-name COLLECTION_NAME
                        Collection name (overrides config and playlist name)
  --dry-run             Preview changes without modifying Plex
  -i, --interactive     Interactively create title overrides for unmatched shows
  --log LOG             Log file path (overrides config)
```

All CLI arguments override their `config.json` equivalents.

### Examples

```bash
# Run with config.json in the current directory
python kodi2plex.py

# Specify a different config file
python kodi2plex.py -c /path/to/config.json

# Override collection name
python kodi2plex.py -n "My TV Shows"

# Dry run — preview changes without modifying Plex
python kodi2plex.py --dry-run

# Interactive — create overrides for unmatched shows
python kodi2plex.py --interactive

# Interactive dry run — build overrides without touching Plex
python kodi2plex.py --interactive --dry-run

# Enable log file
python kodi2plex.py --log sync.log
```

### Interactive Mode

Run with `--interactive` (or `-i`) to create title overrides for shows that fuzzy matching can't resolve. After the matching phase, the script presents each unmatched title with its top 5 fuzzy candidates:

```
============================================================
  Interactive Override Builder
============================================================
  9 unmatched title(s) to resolve
  For each title, pick a number, type a Plex title,
  press Enter to skip, or type 'q' to stop.
============================================================

  [1/9] 'Clarkson's Farm'

    1. Clarkson's Farm (2021) (67%)
    2. Clark (58%)
    3. Clarissa Explains It All (42%)
    4. Charmed (38%)
    5. Chicago Fire (35%)

  Pick [1-5], type exact Plex title, Enter=skip, q=quit: 1
  ✓ 'Clarkson's Farm' → 'Clarkson's Farm (2021)'
```

For each title you can:

- **Pick a number** (1–5) to select from the candidates
- **Type an exact Plex title** if the correct match isn't in the list
- **Press Enter** to skip
- **Type `q`** to stop resolving and continue with the sync

New mappings are automatically saved to `title_overrides` in your config file and applied immediately to the current sync run. This means the next scheduled run will use them without needing `--interactive` again.

Combine with `--dry-run` to build overrides without modifying your Plex collection — the recommended workflow for first-time setup:

```bash
python kodi2plex.py --interactive --dry-run
```

## Automation

### Windows Task Scheduler

To run Kodi2Plex on a schedule, create a PowerShell script like `Register-Kodi2Plex.ps1`:

```powershell
#Requires -RunAsAdministrator

$TaskName = "Kodi2Plex"
$ScriptDir = "C:\GitHub\Kodi2Plex"

$Action = New-ScheduledTaskAction `
    -Execute "python" `
    -Argument "kodi2plex.py --log sync.log" `
    -WorkingDirectory $ScriptDir

$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Hours 6)

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Sync Kodi Smart Playlist to Plex Collection every 6 hours"
```

Run with administrator privileges. The `-StartWhenAvailable` flag ensures missed runs (e.g. if the machine was off) catch up on next boot.

### Linux (cron)

```bash
# Run every 6 hours
0 */6 * * * cd /path/to/Kodi2Plex && python kodi2plex.py --log sync.log
```

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

### Why Some Titles Don't Match

Fuzzy matching works well when titles are similar, but some shows have names that are too short or too generic:

| Kodi Title | Plex Title | Issue |
|------------|------------|-------|
| `The Great` | `The Great (2020)` | After normalization, "great" is too short for a reliable fuzzy match |
| `The Gold` | `The Gold (2023)` | Same — "gold" matches many other titles above threshold |
| `Clarkson's Farm` | `Clarkson's Farm (2021)` | Apostrophe + year suffix creates enough distance to drop below threshold |

These are exactly the cases `title_overrides` and `--interactive` mode are designed for.

## Example Output

```
============================================================
  Kodi2Plex — Sync Smart Playlist → Plex Collection
============================================================
Fetching playlist 'TV Shows' from Kodi...
Playlist:    TV Shows
Shows:       52
Collection:  My Collection
Libraries:   TV Shows - EN, TV Shows - NL
Threshold:   80%
Mode:        *** DRY RUN ***
------------------------------------------------------------
Connecting to Plex at http://plex.local:32400...
Library 'TV Shows - EN' contains 303 shows
Library 'TV Shows - NL' contains 2 shows
Total across all libraries: 305 shows
------------------------------------------------------------
Matching playlist titles to Plex libraries...
  ✓ 'Acapulco' → 'Acapulco (2021)' (100%)
  ✓ 'Arcane' (100%)
  ✓ 'Barry' (100%)
  ✓ 'Castlevania' (100%)
  ✓ 'Castlevania: Nocturne' (100%)
  ✓ 'Clarkson's Farm' → 'Clarkson's Farm (2021)' (override)
  ✓ 'Cosmos' → 'Cosmos (2014)' (100%)
  ✓ 'Mr. & Mrs. Smith' → 'Mr. & Mrs. Smith (2024)' (100%)
  ✗ 'The Marvelous Mrs. Maisel' — no match (best score: 56%)
  ✓ 'Workin' Moms' (100%)
  ...
Matched 48/52 titles
------------------------------------------------------------
No shows to add.
No shows to remove.

============================================================
  [DRY RUN] Sync Summary
============================================================
  Playlist titles:       52
  Plex library size:     305
  Already in collection: 48
  Added:                 0
  Removed:               0
  Not found in Plex:     4
    • The Legend of Korra
    • The Marvelous Mrs. Maisel
    • Voltron: Legendary Defender
    • WondLa
============================================================
```

## Troubleshooting

**"No shows found in Kodi playlist"** — Check that the playlist name in your config matches exactly (without `.xsp` extension) and that the Kodi library is populated. You can test with: `curl -s -X POST -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"Files.GetDirectory","params":{"directory":"special://profile/playlists/video/TV Shows.xsp","media":"video"}}' http://kodi.local:8080/jsonrpc`

**Connection refused on Kodi** — Ensure the web interface is enabled in Kodi under Settings → Services → Control → Allow remote control via HTTP.

**HTTP 401 from Kodi** — Kodi has authentication enabled. Add `username` and `password` to the `kodi` section of your config.

**Shows in Plex not matching** — Check the exact title in Plex (including year suffixes). Lower the `fuzzy_threshold` to see if matches appear, or use `--interactive` to create overrides. A threshold of 80 works well in practice.

**Override target not found** — The right side of a `title_overrides` entry must exactly match the Plex title (case-insensitive). Check for year suffixes and punctuation differences.

**Pushover notifications not arriving** — Verify your `user_key` and `app_token` at [pushover.net](https://pushover.net/). Check the log file for error messages. Notifications are only sent on real runs (not dry runs) with actual changes.

## License

[MIT](LICENSE)