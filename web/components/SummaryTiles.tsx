import { formatShare, tileTrend, type Tiles } from "../lib/topicMap";

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="tile" data-testid="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {hint ? <div className="tile-hint">{hint}</div> : null}
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
    <div className="tiles">
      <Tile label="Notes, last 30 days" value={String(tiles.notes_last_30)} hint={trendHint} />
      <Tile label="Notes with links" value={formatShare(tiles.link_share)} hint={`of ${tiles.total_notes} notes`} />
      <Tile label="Evergreens" value={String(tiles.evergreen_count)} />
      <Tile label="Inbox" value={String(tiles.inbox_count)} />
    </div>
  );
}
