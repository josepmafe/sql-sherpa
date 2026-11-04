/** A build's start as "2026-10-26 14:05 UTC", the same wording on Lineage's status line and Home's strip. */
export function formatBuildStart(startedAt: string): string {
  return `${new Date(startedAt).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}
