import { useState } from "react";

import type { CheckResult } from "../sources";
import { Badge } from "./Badge";
import { Button } from "./Button";

/**
 * Everything wrong with the draft, under the editor rather than above it.
 *
 * **Errors used to stack on top of the panes**, which meant the act of finding out what was wrong
 * shortened the thing you needed to read to fix it — a failed compile with four member errors
 * pushed CodeMirror down by a third of the screen. Below, the editor keeps its position and the
 * panel takes its own space.
 *
 * Four kinds, in the order they stop you, and they are genuinely different questions:
 * `errors` is the gate refusing before Cube is asked at all; `compile_error` is Cube refusing the
 * whole model; `member_errors` are members that compiled and will fail when queried; `impact` is
 * saved queries elsewhere in the app that this change would break. Only the first two block a
 * publish — the last two are reported and deliberately do not, because a model that compiles with
 * one bad measure still beats one that will not compile.
 */
export function Problems({ check }: { check: CheckResult }) {
  const [show, setShow] = useState(true);

  const members = Object.entries(check.member_errors);
  const broken = check.impact?.broken ?? [];
  const blocking = check.errors.length + (check.compile_error ? 1 : 0);
  const count = blocking + members.length + broken.length;
  if (!count) return null;

  return (
    <section className={`problems ${blocking ? "bad" : "warn"}`}>
      <div className="problems-head">
        <strong>Problems</strong>
        <Badge tone={blocking ? "bad" : "warn"}>{count}</Badge>
        <span className="problems-what">
          {blocking
            ? "the model would not compile"
            : "it compiles — these fail later, and do not block a publish"}
        </span>
        <span className="editor-spacer" />
        <Button variant="link" onClick={() => setShow(!show)}>
          {show ? "Hide" : "Show"}
        </Button>
      </div>

      {show && (
        <div className="problems-body">
          {check.errors.map((line) => (
            <p key={line}>{line}</p>
          ))}
          {check.compile_error && <p>{check.compile_error}</p>}

          {members.map(([member, message]) => (
            <p key={member}>
              <code>{member}</code> {message}
            </p>
          ))}

          {broken.length > 0 && (
            <>
              <p>
                {broken.length} of {check.impact?.checked} saved queries would break:
              </p>
              <ul className="editor-broken">
                {broken.map((b) => (
                  <li key={`${b.kind}:${b.where}:${b.title}`}>
                    Tile <strong>{b.title}</strong> ({b.where}) reads{" "}
                    {b.members.map((m, i) => (
                      <span key={m}>
                        {i > 0 && ", "}
                        <code>{m}</code>
                        {b.nearest[m]?.length ? ` — closest: ${b.nearest[m].join(", ")}` : ""}
                      </span>
                    ))}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  );
}
