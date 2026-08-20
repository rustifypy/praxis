import type { System } from "../sources";
import { Info } from "./Info";
import { SystemBanners } from "./SystemBanners";

/**
 * The data page before there is a connection.
 *
 * Kept apart from `SourceDetail` rather than folded into it as an early return: that page is
 * about a database that exists — its deployment, its pipeline, its model — and none of it has
 * anything to say here.
 */
export function Connect({ system, onAdd }: { system: System; onAdd: () => void }) {
  return (
    <div className="sources">
      <header className="sources-head">
        <h1>
          Data
          <Info label="About the data source">
            One Cube deployment, run wherever you want it. Enter the connection here and the
            portal hands back the environment file to start it with; paste its address back and
            everything the agent answers comes through it.
          </Info>
        </h1>
      </header>

      <SystemBanners system={system} />

      <div className="sources-empty">
        <h2>No database connected</h2>
        <p>
          Enter its connection and you get an environment file to run Cube with — here, or on a
          server. Then its tables are read and turned into a semantic model you can ask
          questions about.
        </p>
        <button className="primary" onClick={onAdd}>
          Connect a database
        </button>
      </div>
    </div>
  );
}
