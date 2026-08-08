import type { ReactNode } from "react";
import { useMemo, useState } from "react";

import type { ServedEntity, ServedMember, ServedModel as Model } from "../sources";
import { Collapse } from "./Collapse";
import { Info } from "./Info";

/**
 * What Cube has compiled, as the agent sees it.
 *
 * Deliberately not a YAML viewer: the files on disk are what was authored, this is what
 * survived compilation.
 *
 * Views and cubes are separated because they are not two flavours of the same thing. A
 * view is the curated surface a question should reach; a cube is the table underneath,
 * and reaching across two of them is the case the gate can only warn about. Presented as
 * one list distinguished by a tag, that hierarchy was invisible — and the member totals
 * were wrong, because a view re-exports cube members and both copies were counted.
 */

const KINDS = [
  { key: "measure", label: "Measures" },
  { key: "dimension", label: "Dimensions" },
  { key: "segment", label: "Segments" },
] as const;

function matches(member: ServedMember, needle: string): boolean {
  if (!needle) return true;
  const hay = [member.id, member.title, member.description, member.ai_context]
    .join(" ")
    .toLowerCase();
  return hay.includes(needle);
}

export function ServedModel({
  model,
  loading,
  onRefresh,
}: {
  model: Model | null;
  loading: boolean;
  onRefresh: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const needle = filter.trim().toLowerCase();

  // An entity whose every member is filtered out goes too, or a search reads as "found
  // in 13 places" when it was found in one.
  const shown = useMemo(() => {
    const entities = model?.entities ?? [];
    const ordered = [...entities].sort((a, b) => a.name.localeCompare(b.name));
    const kept = needle
      ? ordered
          .map((e) => ({ ...e, members: e.members.filter((m) => matches(m, needle)) }))
          .filter((e) => e.members.length > 0 || e.name.toLowerCase().includes(needle))
      : ordered;
    return { views: kept.filter((e) => e.is_view), cubes: kept.filter((e) => !e.is_view) };
  }, [model, needle]);

  // Counted over cubes only. A view's members are the same members re-exported, so
  // flattening every entity reported a model roughly twice its real size.
  const totals = useMemo(() => {
    const entities = model?.entities ?? [];
    const cubeMembers = entities.filter((e) => !e.is_view).flatMap((e) => e.members);
    return {
      views: entities.filter((e) => e.is_view).length,
      cubes: entities.filter((e) => !e.is_view).length,
      members: cubeMembers.length,
      exposed: cubeMembers.filter((m) => m.exposed_by.length > 0).length,
      described: cubeMembers.filter((m) => m.description).length,
    };
  }, [model]);

  if (loading && !model) return <p className="served-empty">Reading /v1/meta…</p>;

  if (model && !model.serving) {
    return (
      <div className="served">
        <p className="banner bad">
          Cube did not answer <code>/v1/meta</code>: {model.error || "no response"}. Either
          it is still booting, or the model failed to compile — a compile failure leaves
          this endpoint empty while the container itself stays perfectly healthy, which is
          why the dot above can be green. The container output says which.
        </p>
        <button onClick={onRefresh}>Try again</button>
      </div>
    );
  }

  if (!model) return <p className="served-empty">Nothing read yet.</p>;

  if (totals.cubes + totals.views === 0) {
    return (
      <p className="served-empty">
        Cube answered, but is serving no cubes. Run <strong>Build cubes</strong> to generate
        a model.
      </p>
    );
  }

  return (
    <div className="served">
      <div className="served-head">
        <span className="served-totals">
          {totals.cubes} cube{totals.cubes === 1 ? "" : "s"}, {totals.members} members ·{" "}
          {totals.views === 0
            ? "no views"
            : `${totals.views} view${totals.views === 1 ? "" : "s"} expose ${totals.exposed}`}{" "}
          ·{" "}
          {totals.described === totals.members
            ? "all described"
            : `${totals.described}/${totals.members} described`}
        </span>
        <input
          className="served-filter"
          placeholder="Filter members…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <button onClick={onRefresh} disabled={loading}>
          {loading ? "Reading…" : "Refresh"}
        </button>
      </div>

      {needle && shown.views.length === 0 && shown.cubes.length === 0 && (
        <p className="served-empty">Nothing matches “{filter}”.</p>
      )}

      <Section
        kind="views"
        title="Views"
        subtitle="what a question should reach"
        count={shown.views.length}
        hidden={Boolean(needle) && shown.views.length === 0}
        info="A view is the curated surface: it re-exports members from one or more cubes under names chosen for the business, with the joins already decided. The grounding rules point the agent here first, because a query inside a single view cannot pick a wrong join path."
        empty={
          totals.views === 0 ? (
            <p className="served-empty">
              None yet. Run <strong>Build views</strong> — until then every question has to
              reach into the cubes directly, and the gate can only warn about joins, not
              choose them.
            </p>
          ) : null
        }
      >
        {shown.views.map((entity) => (
          <Entity
            key={entity.name}
            entity={entity}
            open={Boolean(needle) || open === entity.name}
            onToggle={() => setOpen(open === entity.name ? null : entity.name)}
          />
        ))}
      </Section>

      <Section
        kind="cubes"
        title="Cubes"
        subtitle="the tables underneath"
        count={shown.cubes.length}
        hidden={Boolean(needle) && shown.cubes.length === 0}
        info="One cube per table, with its keys and joins. Queryable directly, but a question spanning two cubes depends on the join path the gate happens to compile — which is what views exist to settle. A cube no view exposes is reachable only this way."
      >
        {shown.cubes.map((entity) => (
          <Entity
            key={entity.name}
            entity={entity}
            open={Boolean(needle) || open === entity.name}
            onToggle={() => setOpen(open === entity.name ? null : entity.name)}
          />
        ))}
      </Section>
    </div>
  );
}

function Section({
  kind,
  title,
  subtitle,
  count,
  info,
  hidden,
  empty,
  children,
}: {
  kind: "views" | "cubes";
  title: string;
  subtitle: string;
  count: number;
  info: string;
  hidden: boolean;
  empty?: ReactNode;
  children: ReactNode;
}) {
  if (hidden) return null;
  return (
    <section className={`served-section ${kind}`}>
      <header className="served-section-head">
        <h4>{title}</h4>
        <span className="served-section-sub">{subtitle}</span>
        <Info label={`About ${title.toLowerCase()}`}>{info}</Info>
        <span className="served-section-count">{count}</span>
      </header>
      {count === 0 && empty ? empty : children}
    </section>
  );
}

function Entity({
  entity,
  open,
  onToggle,
}: {
  entity: ServedEntity;
  open: boolean;
  onToggle: () => void;
}) {
  const counts = KINDS.map(({ key, label }) => {
    const n = entity.members.filter((m) => m.kind === key).length;
    return n ? `${n} ${n === 1 ? label.slice(0, -1).toLowerCase() : label.toLowerCase()}` : "";
  }).filter(Boolean);

  // Only meaningful once some view reaches this cube: marking every member of a cube no
  // view touches repeats what the entity header already says, on every row.
  const partial = !entity.is_view && entity.exposed_by.length > 0;

  return (
    <section className={`served-entity ${entity.is_view ? "view" : ""}`}>
      <button className="served-entity-head" onClick={onToggle} aria-expanded={open}>
        <span className="served-name">{entity.title || entity.name}</span>
        <span className="served-count">{counts.join(" · ")}</span>
        {entity.is_view ? (
          <span className="served-from">over {entity.draws_from.join(", ") || "nothing"}</span>
        ) : entity.exposed_by.length > 0 ? (
          <span className="served-from">in {entity.exposed_by.join(", ")}</span>
        ) : (
          <span className="served-from warn" title="Reachable only by querying this cube directly">
            no view
          </span>
        )}
      </button>

      <Collapse open={open}>
        <div className="served-body">
          {entity.description && <p className="served-desc">{entity.description}</p>}
          {entity.ai_context && (
            <p className="served-ai">
              <span className="served-ai-tag">ai_context</span>
              {entity.ai_context}
            </p>
          )}

          {entity.folders.length > 0 && (
            <p className="served-folders">
              Folders: {entity.folders.map((f) => f.name).join(", ")}
            </p>
          )}

          {KINDS.map(({ key, label }) => {
            const members = entity.members.filter((m) => m.kind === key);
            if (!members.length) return null;
            return (
              <div key={key} className="served-group">
                <h4>{label}</h4>
                <ul className="served-members">
                  {members.map((m) => (
                    <Member key={m.id} member={m} flagUnexposed={partial} />
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </Collapse>
    </section>
  );
}

function Member({ member, flagUnexposed }: { member: ServedMember; flagUnexposed: boolean }) {
  return (
    <li className="served-member">
      <span className="served-member-head">
        <code className="served-member-name">{member.name}</code>
        {member.type && (
          // Operators go in the tooltip; nine per member on screen buries the descriptions.
          <span
            className="served-type"
            title={
              member.operators.length
                ? `Filters accepted: ${member.operators.join(", ")}`
                : undefined
            }
          >
            {member.type}
          </span>
        )}
        {member.granularities.length > 0 && (
          <span className="served-grains">{member.granularities.join(" · ")}</span>
        )}
        {/* A view member is a rename of a cube member. Saying which one is the difference
            between reading the view as a new model and reading it as a projection. */}
        {member.alias && <span className="served-alias">← {member.alias}</span>}
        {/* Neutral, not amber. In practice these are the surrogate keys a view omits on
            purpose, so styling it as a warning would report a healthy model as a dozen
            problems. A cube no view reaches at all is the actionable case, and that is
            flagged on the entity instead. */}
        {flagUnexposed && member.exposed_by.length === 0 && (
          <span className="served-alias" title="No view re-exports this member">
            not in a view
          </span>
        )}
      </span>
      {member.description && <span className="served-member-desc">{member.description}</span>}
      {member.ai_context && (
        <span className="served-member-ai">
          <span className="served-ai-tag">ai_context</span>
          {member.ai_context}
        </span>
      )}
    </li>
  );
}
