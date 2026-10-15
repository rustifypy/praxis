interface Entity {
  name: string;
  view: boolean;
  members: number;
  description?: string;
}

const pretty = (name: string) => name.replace(/_/g, " ");

/** Suggestions come from what Cube exposes, so they are always answerable. Views first. */
export function Welcome({ entities, onPick }: { entities: Entity[]; onPick: (q: string) => void }) {
  const suggestions = [...entities]
    .sort(
      (a, b) =>
        Number(b.view) - Number(a.view) ||
        Number(Boolean(b.description)) - Number(Boolean(a.description)) ||
        b.members - a.members,
    )
    .slice(0, 4);

  return (
    <div className="welcome">
      <h1>Ask about your data</h1>
      <p>
        Questions become governed Cube queries — never raw SQL. Every step shows up below,
        including the ones the gate rejects.
      </p>

      {suggestions.length > 0 && (
        <>
          <div className="suggest-label">Try one of these</div>
          <div className="suggestions">
            {suggestions.map((entity, i) => (
              <button
                key={entity.name}
                className="suggestion"
                style={{ animationDelay: `${80 * i}ms` }}
                title={entity.description || undefined}
                onClick={() =>
                  onPick(
                    entity.description
                      ? `Summarise the key trends in ${pretty(entity.name)}.`
                      : `What can I analyse in ${pretty(entity.name)}?`,
                  )
                }
              >
                <span className="suggestion-name">
                  {pretty(entity.name)}
                  <span className={`suggestion-kind ${entity.view ? "view" : ""}`}>
                    {entity.view ? "view" : "cube"}
                  </span>
                </span>
                <span className="suggestion-meta">
                  {entity.description || `${entity.members} members`}
                </span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
