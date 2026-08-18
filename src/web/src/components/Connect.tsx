import type { System } from "../sources";
import { Info } from "./Info";
import { SystemBanners } from "./SystemBanners";

/**
 * The data page before there is a connection.
 *
 * Kept apart from `SourceDetail` rather than folded into it as an early return: that page is
 * about a database that exists — its pipeline, its model, its container — and none of it has
 * anything to say here.
 */
export function Connect({ system, onAdd }: { system: System; onAdd: () => void }) {
  return (
    <div className="sources">
      <header className="sources-head">
        <h1>
          Data
          <Info label="About the data source">
            One Cube container, started from the connection you enter here — credentials
            included, so nothing on disk can drift from it. Everything the agent answers comes
            through it.
          </Info>
        </h1>
      </header>

      <SystemBanners system={system} />

      <div className="sources-empty">
        <h2>No database connected</h2>
        <p>
          Connect one and we will start its Cube container, read its tables, and build a
          semantic model you can ask questions about.
        </p>
        <button className="primary" onClick={onAdd} disabled={!system.ok}>
          Connect a database
        </button>
      </div>
    </div>
  );
}
