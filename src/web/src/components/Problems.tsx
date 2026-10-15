import { useState } from "react";

import type { CheckResult } from "../sources";
import { Badge } from "./Badge";
import { Button } from "./Button";

/**
 * Everything wrong with the draft, below the editor so it doesn't push it down. Only `errors`
 * (the gate) and `compile_error` (Cube) block a publish; member errors and impact are reported.
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
