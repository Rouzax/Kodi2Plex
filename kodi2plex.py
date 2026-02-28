#!/usr/bin/env python3
"""
Kodi2Plex: Syncs a Kodi Smart Playlist to a Plex Collection.

Fetches TV show titles from a Kodi Smart Playlist via JSON-RPC and
synchronizes them into a Plex collection using fuzzy title matching.

Full sync behavior: adds missing shows, removes stale ones.
"""

import argparse
import base64
import json
import logging
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from plexapi.server import PlexServer
from thefuzz import fuzz


# ── Configuration ──────────────────────────────────────────────────────────────

@dataclass
class PushoverConfig:
    """Pushover notification settings."""
    user_key: str
    app_token: str


@dataclass
class KodiConfig:
    """Kodi connection settings."""
    url: str
    playlist: str
    username: str | None = None
    password: str | None = None


@dataclass
class Config:
    """Script configuration loaded from JSON."""
    plex_url: str
    plex_token: str
    library_names: list[str]
    kodi: KodiConfig
    collection_name: str | None = None
    log_file: str | None = None
    fuzzy_threshold: int = 80
    dry_run: bool = False
    pushover: PushoverConfig | None = None
    title_overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Load configuration from a JSON file."""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Support both "library_name" (string) and "library_names" (list)
        if "library_name" in data and "library_names" not in data:
            value = data.pop("library_name")
            data["library_names"] = [value] if isinstance(value, str) else value
        elif "library_names" in data:
            value = data["library_names"]
            if isinstance(value, str):
                data["library_names"] = [value]

        # Parse nested config objects
        kodi_data = data.pop("kodi")
        data["kodi"] = KodiConfig(**kodi_data)

        pushover_data = data.pop("pushover", None)
        if pushover_data and isinstance(pushover_data, dict):
            data["pushover"] = PushoverConfig(**pushover_data)

        return cls(**data)


# ── Logging Setup ──────────────────────────────────────────────────────────────

class ColorFormatter(logging.Formatter):
    """Adds color codes to console log output."""
    COLORS = {
        logging.DEBUG:    "\033[90m",     # Gray
        logging.INFO:     "\033[97m",     # White
        logging.WARNING:  "\033[93m",     # Yellow
        logging.ERROR:    "\033[91m",     # Red
        logging.CRITICAL: "\033[91;1m",   # Bold Red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, self.RESET)
        if hasattr(record, "action"):
            if record.action == "add":
                color = "\033[92m"   # Green
            elif record.action == "remove":
                color = "\033[91m"   # Red
            elif record.action == "skip":
                color = "\033[93m"   # Yellow
            elif record.action == "match":
                color = "\033[96m"   # Cyan
        message = super().format(record)
        return f"{color}{message}{self.RESET}"


def setup_logging(log_file: str | None = None) -> logging.Logger:
    """Configure console (colored) and optional file logging."""
    logger = logging.getLogger("kodi2plex")
    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(ColorFormatter("%(message)s"))
    logger.addHandler(console)

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(file_handler)

    return logger


# ── Title Normalization ────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    """
    Normalize a title for fuzzy comparison.

    - Lowercase
    - Replace '&' with 'and'
    - Strip leading articles (the, a, an)
    - Remove year suffixes like (2014), (2024)
    - Remove punctuation
    - Collapse whitespace
    """
    t = title.lower().strip()
    t = t.replace("&amp;", "and").replace("&", "and")
    t = re.sub(r"^(the|a|an)\s+", "", t)
    t = re.sub(r"\s*\(\d{4}\)\s*$", "", t)  # Strip trailing year
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ── Kodi JSON-RPC ─────────────────────────────────────────────────────────────

@dataclass
class PlaylistInfo:
    """Playlist data fetched from Kodi."""
    name: str
    titles: list[str] = field(default_factory=list)


def kodi_jsonrpc(kodi: KodiConfig, method: str, params: dict) -> dict:
    """
    Send a JSON-RPC request to Kodi and return the result.

    Supports optional HTTP Basic Auth.
    """
    url = kodi.url.rstrip("/") + "/jsonrpc"
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    # Add Basic Auth if credentials are configured
    if kodi.username and kodi.password:
        credentials = f"{kodi.username}:{kodi.password}"
        b64 = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {b64}")

    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if "error" in data:
        error = data["error"]
        raise RuntimeError(
            f"Kodi JSON-RPC error: {error.get('message', error)}"
        )

    return data.get("result", {})


def fetch_kodi_playlist(kodi: KodiConfig, logger: logging.Logger) -> PlaylistInfo:
    """
    Fetch the resolved list of TV shows from a Kodi Smart Playlist
    via JSON-RPC Files.GetDirectory.
    """
    playlist_name = kodi.playlist
    # Construct the special:// path to the smart playlist
    playlist_path = (
        f"special://profile/playlists/video/{playlist_name}.xsp"
    )

    logger.info(f"Fetching playlist '{playlist_name}' from Kodi...")
    logger.debug(f"Playlist path: {playlist_path}")

    result = kodi_jsonrpc(kodi, "Files.GetDirectory", {
        "directory": playlist_path,
        "media": "video",
    })

    files = result.get("files", [])
    titles = [f["label"] for f in files if f.get("label")]

    if not titles:
        raise RuntimeError(
            f"No shows found in Kodi playlist '{playlist_name}'. "
            f"Check the playlist name and ensure Kodi's library is populated."
        )

    return PlaylistInfo(name=playlist_name, titles=titles)


# ── Plex Matching ─────────────────────────────────────────────────────────────

@dataclass
class MatchResult:
    """Result of matching a playlist title to a Plex show."""
    playlist_title: str
    plex_show: object | None = None
    plex_title: str | None = None
    score: int = 0
    matched: bool = False


def find_best_match(
    playlist_title: str,
    plex_shows: list,
    threshold: int
) -> MatchResult:
    """
    Find the best fuzzy match for a playlist title in the Plex library.

    Matching strategy:
    - ratio and token_sort_ratio are always used
    - partial_ratio is only used when normalized title lengths are within
      a 2x ratio, preventing short substrings from matching inside longer
      titles (e.g. "kin" inside "workin moms")
    - A minimum basic ratio of 70 is required as a guard rail to prevent
      matches that only score well on token_sort or partial strategies
    """
    normalized_playlist = normalize_title(playlist_title)
    len_playlist = len(normalized_playlist)
    best_score = 0
    best_ratio = 0
    best_show = None

    for show in plex_shows:
        normalized_plex = normalize_title(show.title)
        len_plex = len(normalized_plex)

        # Always use ratio and token_sort_ratio
        ratio_score = fuzz.ratio(normalized_playlist, normalized_plex)
        token_sort_score = fuzz.token_sort_ratio(normalized_playlist, normalized_plex)

        # Only allow partial_ratio when title lengths are similar
        partial_score = 0
        if len_playlist > 0 and len_plex > 0:
            length_ratio = max(len_playlist, len_plex) / min(len_playlist, len_plex)
            if length_ratio <= 2.0:
                partial_score = fuzz.partial_ratio(normalized_playlist, normalized_plex)

        score = max(ratio_score, token_sort_score, partial_score)

        # Use (score, ratio) as sort key: when combined scores tie,
        # prefer the candidate with the higher basic ratio — this stops
        # "Castlevania" from beating "Castlevania: Nocturne" via partial
        if (score, ratio_score) > (best_score, best_ratio):
            best_score = score
            best_ratio = ratio_score
            best_show = show

    # Require both:
    # 1. Best combined score meets the configured threshold
    # 2. Basic ratio >= 70 to prevent spurious partial/token matches
    min_ratio = 70
    if best_score >= threshold and best_ratio >= min_ratio and best_show:
        return MatchResult(
            playlist_title=playlist_title,
            plex_show=best_show,
            plex_title=best_show.title,
            score=best_score,
            matched=True,
        )

    return MatchResult(playlist_title=playlist_title, score=best_score)


def find_top_candidates(
    playlist_title: str,
    plex_shows: list,
    max_results: int = 5,
) -> list[tuple[str, int]]:
    """
    Return the top N Plex shows by fuzzy score for a given title.

    Used in interactive mode to present candidates to the user.
    Returns list of (plex_title, score) tuples, sorted by score descending.
    """
    normalized_playlist = normalize_title(playlist_title)
    len_playlist = len(normalized_playlist)
    scored = []

    for show in plex_shows:
        normalized_plex = normalize_title(show.title)
        len_plex = len(normalized_plex)

        ratio_score = fuzz.ratio(normalized_playlist, normalized_plex)
        token_sort_score = fuzz.token_sort_ratio(normalized_playlist, normalized_plex)

        partial_score = 0
        if len_playlist > 0 and len_plex > 0:
            length_ratio = max(len_playlist, len_plex) / min(len_playlist, len_plex)
            if length_ratio <= 2.0:
                partial_score = fuzz.partial_ratio(normalized_playlist, normalized_plex)

        score = max(ratio_score, token_sort_score, partial_score)
        scored.append((show.title, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_results]


# ── Collection Sync ───────────────────────────────────────────────────────────

@dataclass
class SyncStats:
    """Tracks sync operation statistics."""
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    already_in_collection: list[str] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    total_playlist: int = 0
    total_plex_library: int = 0


def sync_collection(
    config: Config,
    logger: logging.Logger,
    interactive: bool = False,
    config_path: str | None = None,
) -> SyncStats:
    """
    Main sync logic: fetch playlist from Kodi, match to Plex across all
    libraries, sync collection.
    """
    stats = SyncStats()

    # ── Fetch playlist from Kodi ──────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("  Kodi2Plex — Sync Smart Playlist → Plex Collection")
    logger.info("=" * 60)

    playlist = fetch_kodi_playlist(config.kodi, logger)
    collection_name = config.collection_name or playlist.name
    stats.total_playlist = len(playlist.titles)

    logger.info(f"Playlist:    {playlist.name}")
    logger.info(f"Shows:       {len(playlist.titles)}")
    logger.info(f"Collection:  {collection_name}")
    logger.info(f"Libraries:   {', '.join(config.library_names)}")
    logger.info(f"Threshold:   {config.fuzzy_threshold}%")
    if config.dry_run:
        logger.info("Mode:        *** DRY RUN ***")
    logger.info("-" * 60)

    # ── Connect to Plex ───────────────────────────────────────────────────
    logger.info(f"Connecting to Plex at {config.plex_url}...")
    plex = PlexServer(config.plex_url, config.plex_token)

    # ── Gather shows from all libraries ───────────────────────────────────
    all_plex_shows = []
    libraries = []
    for lib_name in config.library_names:
        library = plex.library.section(lib_name)
        libraries.append(library)
        shows = library.all()
        all_plex_shows.extend(shows)
        logger.info(f"Library '{lib_name}' contains {len(shows)} shows")

    stats.total_plex_library = len(all_plex_shows)
    if len(config.library_names) > 1:
        logger.info(f"Total across all libraries: {len(all_plex_shows)} shows")
    logger.info("-" * 60)

    # ── Build Plex title lookup for overrides ─────────────────────────────
    plex_shows_by_title = {show.title.lower(): show for show in all_plex_shows}

    # ── Match titles ──────────────────────────────────────────────────────
    logger.info("Matching playlist titles to Plex libraries...")
    matched_shows = []

    for title in sorted(playlist.titles):
        # Check manual overrides first
        if title in config.title_overrides:
            override_target = config.title_overrides[title]
            plex_show = plex_shows_by_title.get(override_target.lower())
            if plex_show:
                result = MatchResult(
                    playlist_title=title,
                    plex_show=plex_show,
                    plex_title=plex_show.title,
                    score=100,
                    matched=True,
                )
                matched_shows.append(result)
                logger.info(
                    f"  ✓ '{title}' → '{plex_show.title}' (override)",
                    extra={"action": "match"},
                )
                continue
            else:
                stats.not_found.append(title)
                logger.warning(
                    f"  ✗ '{title}' — override '{override_target}' not found in Plex",
                    extra={"action": "skip"},
                )
                continue

        # Fall back to fuzzy matching
        result = find_best_match(title, all_plex_shows, config.fuzzy_threshold)
        if result.matched:
            matched_shows.append(result)
            if result.playlist_title.lower() != result.plex_title.lower():
                logger.info(
                    f"  ✓ '{title}' → '{result.plex_title}' ({result.score}%)",
                    extra={"action": "match"},
                )
            else:
                logger.info(
                    f"  ✓ '{title}' ({result.score}%)",
                    extra={"action": "match"},
                )
        else:
            stats.not_found.append(title)
            logger.warning(
                f"  ✗ '{title}' — no match (best score: {result.score}%)",
                extra={"action": "skip"},
            )

    logger.info(
        f"Matched {len(matched_shows)}/{len(playlist.titles)} titles"
    )
    logger.info("-" * 60)

    # ── Interactive override builder ──────────────────────────────────────
    if interactive and stats.not_found:
        new_overrides = interactive_resolve(
            stats.not_found, all_plex_shows, plex_shows_by_title, logger,
        )
        if new_overrides:
            # Apply new overrides to this run
            for kodi_title, plex_title in new_overrides.items():
                plex_show = plex_shows_by_title.get(plex_title.lower())
                if plex_show:
                    result = MatchResult(
                        playlist_title=kodi_title,
                        plex_show=plex_show,
                        plex_title=plex_show.title,
                        score=100,
                        matched=True,
                    )
                    matched_shows.append(result)
                    stats.not_found.remove(kodi_title)

            # Save to config file
            if config_path:
                save_overrides_to_config(config_path, new_overrides, logger)

            logger.info("-" * 60)

    # ── Get current collection members across all libraries ───────────────
    current_collection_shows = []
    for library in libraries:
        try:
            collections = library.search(
                title=collection_name, libtype="collection"
            )
            for col in collections:
                if col.title == collection_name:
                    current_collection_shows.extend(col.items())
        except Exception:
            pass

    current_ids = {show.ratingKey for show in current_collection_shows}
    desired_ids = {r.plex_show.ratingKey for r in matched_shows}

    # ── Add missing shows ─────────────────────────────────────────────────
    to_add = [r for r in matched_shows if r.plex_show.ratingKey not in current_ids]
    to_remove = [s for s in current_collection_shows if s.ratingKey not in desired_ids]

    if to_add:
        logger.info("Adding to collection:")
        for result in to_add:
            logger.info(
                f"  + {result.plex_title}",
                extra={"action": "add"},
            )
            if not config.dry_run:
                result.plex_show.addCollection(collection_name)
            stats.added.append(result.plex_title)
    else:
        logger.info("No shows to add.")

    # ── Remove stale shows ────────────────────────────────────────────────
    if to_remove:
        logger.info("Removing from collection:")
        for show in to_remove:
            logger.info(
                f"  - {show.title}",
                extra={"action": "remove"},
            )
            if not config.dry_run:
                show.removeCollection(collection_name)
            stats.removed.append(show.title)
    else:
        logger.info("No shows to remove.")

    # ── Already in sync ───────────────────────────────────────────────────
    already = [r for r in matched_shows if r.plex_show.ratingKey in current_ids]
    stats.already_in_collection = [r.plex_title for r in already]

    return stats


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(stats: SyncStats, logger: logging.Logger, dry_run: bool = False):
    """Print a summary of the sync operation."""
    logger.info("")
    logger.info("=" * 60)
    prefix = "[DRY RUN] " if dry_run else ""
    logger.info(f"  {prefix}Sync Summary")
    logger.info("=" * 60)
    logger.info(f"  Playlist titles:       {stats.total_playlist}")
    logger.info(f"  Plex library size:     {stats.total_plex_library}")
    logger.info(f"  Already in collection: {len(stats.already_in_collection)}")
    logger.info(
        f"  Added:                 {len(stats.added)}",
        extra={"action": "add"} if stats.added else {},
    )
    logger.info(
        f"  Removed:               {len(stats.removed)}",
        extra={"action": "remove"} if stats.removed else {},
    )

    if stats.not_found:
        logger.warning(
            f"  Not found in Plex:     {len(stats.not_found)}",
            extra={"action": "skip"},
        )
        for title in stats.not_found:
            logger.warning(f"    • {title}", extra={"action": "skip"})

    logger.info("=" * 60)


# ── Pushover Notifications ────────────────────────────────────────────────────

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


def build_notification(stats: SyncStats, collection_name: str) -> str | None:
    """
    Build a Pushover notification message from sync stats.

    Returns None if there are no changes to report.
    """
    sections = []

    if stats.added:
        lines = [f"  + {title}" for title in stats.added]
        sections.append("Added:\n" + "\n".join(lines))

    if stats.removed:
        lines = [f"  - {title}" for title in stats.removed]
        sections.append("Removed:\n" + "\n".join(lines))

    if stats.not_found:
        lines = [f"  • {title}" for title in stats.not_found]
        sections.append("Not found in Plex:\n" + "\n".join(lines))

    if not stats.added and not stats.removed:
        return None

    header = f"Collection: {collection_name}"
    return header + "\n\n" + "\n\n".join(sections)


def send_pushover(
    pushover: "PushoverConfig",
    title: str,
    message: str,
    logger: logging.Logger,
) -> None:
    """Send a Pushover notification using urllib (no extra dependencies)."""
    payload = urllib.parse.urlencode({
        "token": pushover.app_token,
        "user": pushover.user_key,
        "title": title,
        "message": message,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(PUSHOVER_API_URL, data=payload, method="POST")
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                logger.info("Pushover notification sent successfully")
            else:
                logger.warning(f"Pushover returned status {resp.status}")
    except Exception as e:
        logger.error(f"Failed to send Pushover notification: {e}")


# ── Interactive Override Builder ───────────────────────────────────────────────

def interactive_resolve(
    unmatched_titles: list[str],
    plex_shows: list,
    plex_shows_by_title: dict,
    logger: logging.Logger,
) -> dict[str, str]:
    """
    Interactively prompt the user to map unmatched titles to Plex shows.

    For each unmatched title, shows the top fuzzy candidates and lets the
    user pick one by number, type a custom Plex title, or skip.

    Returns a dict of new title_overrides {kodi_title: plex_title}.
    """
    new_overrides = {}

    print()
    print("=" * 60)
    print("  Interactive Override Builder")
    print("=" * 60)
    print(f"  {len(unmatched_titles)} unmatched title(s) to resolve")
    print("  For each title, pick a number, type a Plex title,")
    print("  press Enter to skip, or type 'q' to stop.")
    print("=" * 60)

    for i, title in enumerate(unmatched_titles, 1):
        print(f"\n  [{i}/{len(unmatched_titles)}] '{title}'")
        print()

        # Show top candidates
        candidates = find_top_candidates(title, plex_shows, max_results=5)
        for idx, (plex_title, score) in enumerate(candidates, 1):
            print(f"    {idx}. {plex_title} ({score}%)")

        print()
        choice = input("  Pick [1-5], type exact Plex title, Enter=skip, q=quit: ").strip()

        if choice.lower() == "q":
            print("  Stopping interactive mode.")
            break
        elif choice == "":
            print("  Skipped.")
            continue
        elif choice.isdigit() and 1 <= int(choice) <= len(candidates):
            plex_title = candidates[int(choice) - 1][0]
            new_overrides[title] = plex_title
            print(f"  \033[92m✓ '{title}' → '{plex_title}'\033[0m")
        else:
            # Treat as a custom Plex title — validate it exists
            plex_show = plex_shows_by_title.get(choice.lower())
            if plex_show:
                new_overrides[title] = plex_show.title
                print(f"  \033[92m✓ '{title}' → '{plex_show.title}'\033[0m")
            else:
                print(f"  \033[93m⚠ '{choice}' not found in Plex — skipped.\033[0m")

    return new_overrides


def save_overrides_to_config(
    config_path: str,
    new_overrides: dict[str, str],
    logger: logging.Logger,
) -> None:
    """
    Merge new title overrides into the config JSON file and save it.

    Preserves all existing config values and formatting.
    """
    path = Path(config_path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    existing = data.get("title_overrides", {})
    existing.update(new_overrides)
    data["title_overrides"] = existing

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")

    logger.info(f"Saved {len(new_overrides)} new override(s) to {config_path}")


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Kodi2Plex — Sync a Kodi Smart Playlist to a Plex Collection."
    )
    parser.add_argument(
        "-c", "--config",
        default="config.json",
        help="Path to JSON config file (default: config.json)",
    )
    parser.add_argument(
        "-n", "--collection-name",
        default=None,
        help="Collection name (overrides config and playlist name)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying Plex",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Interactively create title overrides for unmatched shows",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Log file path (overrides config)",
    )
    args = parser.parse_args()

    # Load config
    try:
        config = Config.from_file(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except (json.JSONDecodeError, TypeError) as e:
        print(f"Error reading config: {e}", file=sys.stderr)
        sys.exit(1)

    # CLI overrides
    if args.collection_name:
        config.collection_name = args.collection_name
    if args.dry_run:
        config.dry_run = True
    if args.log:
        config.log_file = args.log

    # Setup logging
    logger = setup_logging(config.log_file)

    # Run sync
    try:
        stats = sync_collection(
            config, logger,
            interactive=args.interactive,
            config_path=args.config,
        )
        print_summary(stats, logger, config.dry_run)

        # Send Pushover notification (only on real runs with changes)
        if config.pushover and not config.dry_run:
            collection_name = config.collection_name or config.kodi.playlist
            message = build_notification(stats, collection_name)
            if message:
                send_pushover(
                    config.pushover,
                    "Kodi2Plex Sync",
                    message,
                    logger,
                )
    except FileNotFoundError as e:
        logger.error(f"File error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        logger.debug("Details:", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
