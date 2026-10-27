// The Lineage view of a table and everything around it: the selector `+<table>+` (UI spec §6.5).
export function lineageLink(table: string): string {
  return `/lineage?select=%2B${encodeURIComponent(table)}%2B`;
}
