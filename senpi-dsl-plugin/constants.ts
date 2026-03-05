/**
 * Plugin and script constants. Single source of truth for IDs, defaults, and CLI subcommands.
 */

export const PLUGIN_ID = "senpi-dsl" as const;

export const DEFAULT_STATE_DIR = "/data/workspace/dsl";
export const DEFAULT_SCHEDULE = "*/3 * * * *";

/** Subcommands for the DSL CLI */
export const SUBCOMMANDS = [
  "add-dsl",
  "update-dsl",
  "pause-dsl",
  "resume-dsl",
  "delete-dsl",
  "status-dsl",
] as const;

export type Subcommand = (typeof SUBCOMMANDS)[number];

/** Directory name under stateDir that holds archived state (excluded from discovery). */
export const ARCHIVE_DIR_NAME = "archive";
