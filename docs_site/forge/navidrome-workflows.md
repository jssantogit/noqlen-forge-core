# Navidrome Workflows

Forge includes Navidrome-oriented workflows for careful local-library and server-adjacent maintenance. These workflows are not real-time integration and do not require a separate Noqlen Anchor Core component today.

## Ratings

Ratings workflows focus on backup, diff, and restore. Backup and diff are read-oriented. Restore should be reviewed as a plan first and applied only after identity matching, warnings, and counts are understood.

## Playlists

Playlist workflows include backup, status-style inspection through reports and command output, export, and push. Exports write requested playlist files. Push operations write to Navidrome only when explicitly applied.

## Reports And Review

Use reports and review flows to inspect mismatches, missing files, and unsafe changes before writing tags, database rows, playlist files, or server state.

## Future Anchor Context

Noqlen Anchor Core is planned as future Navidrome and local server management work. It is not required for the current Forge workflows documented here.
