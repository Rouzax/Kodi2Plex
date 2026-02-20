#!/usr/bin/env python3
"""
Kodi2Plex: Syncs a Kodi Smart Playlist to a Plex Collection or Playlist.

Reads TV show titles from a Kodi Smart Playlist XML (.xsp) file and
synchronizes them into a Plex collection or playlist using fuzzy title
matching.

Full sync behavior: adds missing shows, removes stale ones.
"""

import argparse
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from plexapi.exceptions import NotFound
from plexapi.server import PlexServer
from thefuzz import fuzz


# ── Configuration ──────────────────────────────────────────────────────────────

VALID_SYNC_MODES = ("collection", "playlist")


@dataclass
class Config:
    """Script configuration loaded from JSON."""
    plex_url: str
    plex_token: str
    library_names: list[str]
    playlist_path: str
    sync_modes: list[str] = field(default_factory=lambda: ["collection"])
    collection_name: str | None = None
    log_file: str | None = None
    fuzzy_threshold: int = 80
    dry_run: bool = False

    def __post_init__(self):
        for mode in self.sync_modes:
            if mode not in VALID_SYNC_MODES:
                raise ValueError(
                    f"Invalid sync_mode '{mode}'. "
                    f"Must be one of: {', '.join(VALID_SYNC_MODES)}"
                )

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

        # Support "sync_mode" (string) and "sync_modes" (list)
        if "sync_mode" in data and "sync_modes" not in data:
            value = data.pop("sync_mode")
            data["sync_modes"] = [value] if isinstance(value, str) else value
        elif "sync_modes" in data:
            value = data["sync_modes"]
            if isinstance(value, str):
                data["sync_modes"] = [value]

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


# ── Playlist Parsing ──────────────────────────────────────────────────────────

@dataclass
class PlaylistInfo:
    """Parsed smart playlist data."""
    name: str
    titles: list[str] = field(default_factory=list)


def parse_playlist(path: str) -> PlaylistInfo:
    """Parse a Kodi Smart Playlist XML file and extract show titles."""
    playlist_path = Path(path)
    if not playlist_path.exists():
        raise FileNotFoundError(f"Playlist file not found: {playlist_path}")

    tree = ET.parse(playlist_path)
    root = tree.getroot()

    name = root.findtext("name", default="Unnamed Playlist")
    titles = []

    for rule in root.findall(".//rule[@field='title']"):
        for value in rule.findall("value"):
            if value.text:
                titles.append(value.text.strip())

    return PlaylistInfo(name=name, titles=titles)


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


# ── Sync Stats ────────────────────────────────────────────────────────────────

@dataclass
class SyncStats:
    """Tracks sync operation statistics."""
    sync_modes: list[str] = field(default_factory=list)
    added: dict[str, list[str]] = field(default_factory=dict)
    removed: dict[str, list[str]] = field(default_factory=dict)
    already_synced: dict[str, list[str]] = field(default_factory=dict)
    not_found: list[str] = field(default_factory=list)
    total_playlist: int = 0
    total_plex_library: int = 0


# ── Collection Sync ───────────────────────────────────────────────────────────

def sync_as_collection(
    plex: PlexServer,
    libraries: list,
    matched_shows: list[MatchResult],
    target_name: str,
    dry_run: bool,
    logger: logging.Logger,
) -> tuple[list[str], list[str], list[str]]:
    """
    Sync matched shows to a Plex collection.

    Returns: (added, removed, already_synced) title lists
    """
    added, removed, already = [], [], []

    # Get current collection members across all libraries
    current_shows = []
    for library in libraries:
        try:
            collections = library.search(title=target_name, libtype="collection")
            for col in collections:
                if col.title == target_name:
                    current_shows.extend(col.items())
        except Exception:
            pass

    current_ids = {show.ratingKey for show in current_shows}
    desired_ids = {r.plex_show.ratingKey for r in matched_shows}

    # Add missing
    to_add = [r for r in matched_shows if r.plex_show.ratingKey not in current_ids]
    if to_add:
        logger.info("Adding to collection:")
        for result in to_add:
            logger.info(f"  + {result.plex_title}", extra={"action": "add"})
            if not dry_run:
                result.plex_show.addCollection(target_name)
            added.append(result.plex_title)
    else:
        logger.info("No shows to add.")

    # Remove stale
    to_remove = [s for s in current_shows if s.ratingKey not in desired_ids]
    if to_remove:
        logger.info("Removing from collection:")
        for show in to_remove:
            logger.info(f"  - {show.title}", extra={"action": "remove"})
            if not dry_run:
                show.removeCollection(target_name)
            removed.append(show.title)
    else:
        logger.info("No shows to remove.")

    # Already in sync
    already = [
        r.plex_title for r in matched_shows
        if r.plex_show.ratingKey in current_ids
    ]

    return added, removed, already


# ── Playlist Sync ─────────────────────────────────────────────────────────────

def sync_as_playlist(
    plex: PlexServer,
    matched_shows: list[MatchResult],
    target_name: str,
    dry_run: bool,
    logger: logging.Logger,
) -> tuple[list[str], list[str], list[str]]:
    """
    Sync matched shows to a Plex playlist.

    Returns: (added, removed, already_synced) title lists
    """
    added, removed, already = [], [], []

    # Find existing playlist
    existing_playlist = None
    try:
        existing_playlist = plex.playlist(target_name)
    except NotFound:
        pass

    desired_shows = [r.plex_show for r in matched_shows]
    desired_ids = {r.plex_show.ratingKey for r in matched_shows}

    if existing_playlist is None:
        # Create new playlist with all matched shows
        if desired_shows:
            logger.info("Creating new playlist:")
            for result in matched_shows:
                logger.info(f"  + {result.plex_title}", extra={"action": "add"})
                added.append(result.plex_title)
            if not dry_run:
                plex.createPlaylist(target_name, items=desired_shows)
        else:
            logger.info("No shows matched — playlist not created.")
    else:
        # Sync existing playlist
        current_items = existing_playlist.items()
        current_ids = {item.ratingKey for item in current_items}

        # Add missing
        to_add = [r for r in matched_shows if r.plex_show.ratingKey not in current_ids]
        if to_add:
            logger.info("Adding to playlist:")
            for result in to_add:
                logger.info(f"  + {result.plex_title}", extra={"action": "add"})
                added.append(result.plex_title)
            if not dry_run:
                existing_playlist.addItems([r.plex_show for r in to_add])
        else:
            logger.info("No shows to add.")

        # Remove stale
        to_remove = [s for s in current_items if s.ratingKey not in desired_ids]
        if to_remove:
            logger.info("Removing from playlist:")
            for show in to_remove:
                logger.info(f"  - {show.title}", extra={"action": "remove"})
                removed.append(show.title)
            if not dry_run:
                existing_playlist.removeItems(to_remove)
        else:
            logger.info("No shows to remove.")

        # Already in sync
        already = [
            r.plex_title for r in matched_shows
            if r.plex_show.ratingKey in current_ids
        ]

    return added, removed, already


# ── Main Sync ─────────────────────────────────────────────────────────────────

def sync(config: Config, logger: logging.Logger) -> SyncStats:
    """
    Main sync logic: parse playlist, match to Plex across all libraries,
    sync to collection and/or playlist based on config.
    """
    stats = SyncStats(sync_modes=config.sync_modes)
    modes_label = " + ".join(m.capitalize() for m in config.sync_modes)

    # ── Parse playlist ────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info(f"  Kodi2Plex — Sync Smart Playlist → Plex {modes_label}")
    logger.info("=" * 60)

    playlist = parse_playlist(config.playlist_path)
    target_name = config.collection_name or playlist.name
    stats.total_playlist = len(playlist.titles)

    logger.info(f"Playlist:    {playlist.name}")
    logger.info(f"Shows:       {len(playlist.titles)}")
    logger.info(f"Sync mode:   {modes_label}")
    logger.info(f"Target:      {target_name}")
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

    # ── Match titles ──────────────────────────────────────────────────────
    logger.info("Matching playlist titles to Plex libraries...")
    matched_shows = []

    for title in sorted(playlist.titles):
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

    logger.info(f"Matched {len(matched_shows)}/{len(playlist.titles)} titles")
    logger.info("-" * 60)

    # ── Sync to each target mode ──────────────────────────────────────────
    for mode in config.sync_modes:
        mode_label = mode.capitalize()
        logger.info(f"Syncing to {mode_label}: {target_name}")

        if mode == "collection":
            added, removed, already = sync_as_collection(
                plex, libraries, matched_shows, target_name,
                config.dry_run, logger,
            )
        else:
            added, removed, already = sync_as_playlist(
                plex, matched_shows, target_name,
                config.dry_run, logger,
            )

        stats.added[mode] = added
        stats.removed[mode] = removed
        stats.already_synced[mode] = already

        if len(config.sync_modes) > 1:
            logger.info("-" * 60)

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

    for mode in stats.sync_modes:
        mode_label = mode.capitalize()
        added = stats.added.get(mode, [])
        removed = stats.removed.get(mode, [])
        already = stats.already_synced.get(mode, [])

        if len(stats.sync_modes) > 1:
            logger.info(f"  --- {mode_label} ---")

        logger.info(f"  Already synced:        {len(already)}")
        logger.info(
            f"  Added:                 {len(added)}",
            extra={"action": "add"} if added else {},
        )
        logger.info(
            f"  Removed:               {len(removed)}",
            extra={"action": "remove"} if removed else {},
        )

    if stats.not_found:
        logger.warning(
            f"  Not found in Plex:     {len(stats.not_found)}",
            extra={"action": "skip"},
        )
        for title in stats.not_found:
            logger.warning(f"    • {title}", extra={"action": "skip"})

    logger.info("=" * 60)


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Kodi2Plex — Sync a Kodi Smart Playlist to a Plex "
                    "Collection or Playlist."
    )
    parser.add_argument(
        "-c", "--config",
        default="config.json",
        help="Path to JSON config file (default: config.json)",
    )
    parser.add_argument(
        "-p", "--playlist",
        default=None,
        help="Path to Kodi Smart Playlist XML (overrides config)",
    )
    parser.add_argument(
        "-n", "--name",
        default=None,
        help="Target collection/playlist name (overrides config)",
    )
    parser.add_argument(
        "-m", "--mode",
        nargs="+",
        choices=VALID_SYNC_MODES,
        default=None,
        help="Sync mode(s): collection, playlist, or both (overrides config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying Plex",
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
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        print(f"Error reading config: {e}", file=sys.stderr)
        sys.exit(1)

    # CLI overrides
    if args.playlist:
        config.playlist_path = args.playlist
    if args.name:
        config.collection_name = args.name
    if args.mode:
        config.sync_modes = args.mode
    if args.dry_run:
        config.dry_run = True
    if args.log:
        config.log_file = args.log

    # Setup logging
    logger = setup_logging(config.log_file)

    # Run sync
    try:
        stats = sync(config, logger)
        print_summary(stats, logger, config.dry_run)
    except FileNotFoundError as e:
        logger.error(f"File error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        logger.debug("Details:", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
