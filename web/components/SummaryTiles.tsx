import { formatShare, tileTrend, type Tiles } from "../lib/topicMap";

const tile: React.CSSProperties = {
  flex: "1 1 180px",
  border: "1px solid #d0d7de",
  borderRadius: 8,
  padding: 12,
  background: "#fff",
};

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div style={tile} data-testid="tile">
      <div style={{ fontSize: 12, color: "#57606a" }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 600 }}>{value}</div>
      {hint ? <div style={{ fontSize: 12, color: "#57606a" }}>{hint}</div> : null}
    </div>
  );
}

export function SummaryTiles({ tiles }: { tiles: Tiles }) {
  const trend = tileTrend(tiles.notes_last_30, tiles.notes_prior_30);
  const trendHint =
    trend === null
      ? `${tiles.notes_prior_30} in the 30 days before`
      : `${trend > 0 ? "+" : ""}${trend}% vs ${tiles.notes_prior_30} in the 30 days before`;
  return (
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
      <Tile label="Notes, last 30 days" value={String(tiles.notes_last_30)} hint={trendHint} />
      <Tile label="Notes with links" value={formatShare(tiles.link_share)} hint={`of ${tiles.total_notes} notes`} />
      <Tile label="Evergreens" value={String(tiles.evergreen_count)} />
      <Tile label="Inbox" value={String(tiles.inbox_count)} />
    </div>
  );
}
