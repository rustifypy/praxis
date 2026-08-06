interface Entity {
  name: string;
  view: boolean;
  members: number;
  description?: string;
}

const pretty = (name: string) => name.replace(/_/g, " ");

/**
 * Suggestions are built from what Cube actually exposes rather than hardcoded, so they
 * are never questions this model cannot answer. Views first — they are the curated
 * surface, and the grounding rules generally point the agent at them anyway.
 */
export function Welcome({ entities, onPick }: { entities: Entity[]; onPick: (q: string) => void }) {
  const suggestions = [...entities]
    .sort((a, b) => Number(b.view) - Number(a.view) || b.members - a.members)
    .slice(0, 4);

  return (
    <div className="welcome">
      <h1>Ask about your data</h1>
      <p>
        Questions become governed Cube queries — never raw SQL. Every step shows up
        below, including the ones the gate rejects.
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
                onClick={() => onPick(`What can I analyse in ${pretty(entity.name)}?`)}
              >
                <span className="suggestion-name">{pretty(entity.name)}</span>
                <span className="suggestion-meta">
                  {entity.view ? "view" : "cube"} · {entity.members} members
                </span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
