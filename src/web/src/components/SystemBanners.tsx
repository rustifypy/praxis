import type { System } from "../sources";

/**
 * What is wrong machine-wide: docker, dev mode, the shared Cube Store.
 *
 * Its own component because both states of the data page show the same three — before a
 * database is connected and after — and a second copy is one that would drift.
 */
export function SystemBanners({ system }: { system: System }) {
  return (
    <>
      {!system.ok && <p className="banner bad">{system.reason}</p>}
      {system.ok && system.dev_mode && (
        <p className="banner warn">
          Cube dev mode is on, so the container serves its playground and skips JWT
          verification. Fine locally; set <code>CUBEJS_DEV_MODE=false</code> and re-run Connect
          for anything else. The port is bound to localhost either way.
        </p>
      )}
      {system.ok && !system.cubestore && (
        <p className="banner warn">
          Cube Store is not running — queries still work, pre-aggregations will not. Start it
          with <code>make up</code>.
        </p>
      )}
    </>
  );
}
