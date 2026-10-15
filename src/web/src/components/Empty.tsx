import type { ReactNode } from "react";

/**
 * A surface with nothing on it yet. `tight` is for places with no room: a tile keeping its grid
 * shape, or a list row.
 */
export function Empty({
  title,
  action,
  tight = false,
  children,
}: {
  title?: string;
  action?: ReactNode;
  tight?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={tight ? "empty empty-tight" : "empty"}>
      {title && <h3>{title}</h3>}
      <p>{children}</p>
      {action}
    </div>
  );
}
