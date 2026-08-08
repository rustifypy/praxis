import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import type { ServedEntity, ServedMember, ServedModel as Model } from "../sources";
import { Info } from "./Info";

/**
 * What Cube has compiled, as the agent sees it.
 *
 * Deliberately not a YAML viewer: the files on disk are what was authored, this is what
 * survived compilation.
 *
 * Master–detail rather than accordions. Expanding a cube in place pushed every row below
 * it down the page, so comparing two of them meant losing your position twice; with
 * thirteen entities and seventy members the list never held still. The list is now a
 * fixed column and the members render in their own box beside it, so clicking through
 * entities moves nothing.
 *
 * Views and cubes are separated because they are not two flavours of the same thing. A
 * view is the curated surface a question should reach; a cube is the table underneath,
 * and reaching across two of them is the case the gate can only warn about.
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
  const [picked, setPicked] = useState<string | null>(null);
  const needle = filter.trim().toLowerCase();

  // An entity whose every member is filtered out goes too, or a search reads as "found
  // in 13 places" when it was found in one.
  const shown = useMemo(() => {
    const entities = model?.entities ?? [];
    const ordered = [...entities].sort((a, b) => a.name.localeCompare(b.name));
    const kept = needle
      ? ordered.filter(
          (e) => e.members.some((m) => matches(m, needle)) || e.name.toLowerCase().includes(needle),
        )
      : ordered;
    return { views: kept.filter((e) => e.is_view), cubes: kept.filter((e) => !e.is_view) };
  }, [model, needle]);

  const listed = useMemo(() => [...shown.views, ...shown.cubes], [shown]);

  /**
   * What the box opens on when nothing has been clicked.
   *
   * Views first, so it lands on the curated surface rather than whichever cube sorts
   * first. Under a filter it lands on the strongest match instead — searching "revenue"
   * and opening the one view that does not mention it is a worse answer than the list
   * already gave. The *list* stays in name order either way: re-ranking rows while
   * someone types moves the thing they are reaching for.
   */
  const fallback = useMemo(() => {
    if (!listed.length) return null;
    if (!needle) return listed[0];
    const score = (e: ServedEntity) =>
      (e.name.toLowerCase().includes(needle) ? 1000 : 0) +
      e.members.filter((m) => matches(m, needle)).length;
    return [...listed].sort((a, b) => score(b) - score(a))[0];
  }, [listed, needle]);

  const selected = listed.find((e) => e.name === picked) ?? fallback;

  // A filter that excludes the open entity would otherwise leave the box showing
  // something the list no longer offers.
  useEffect(() => {
    if (picked && !listed.some((e) => e.name === picked)) setPicked(null);
  }, [picked, listed]);

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

      <div className="served-split">
        <div className="served-list">
          <Section
            kind="views"
            title="Views"
            subtitle="ask here"
            count={shown.views.length}
            info="A view is the curated surface: it re-exports members from one or more cubes under names chosen for the business, with the joins already decided. The grounding rules point the agent here first, because a query inside a single view cannot pick a wrong join path."
            empty={
              totals.views === 0 ? (
                <p className="served-empty">
                  None yet. Run <strong>Build views</strong> — until then every question has
                  to reach into the cubes directly, and the gate can only warn about joins,
                  not choose them.
                </p>
              ) : (
                <p className="served-empty">No view matches.</p>
              )
            }
          >
            {shown.views.map((entity) => (
              <Row
                key={entity.name}
                entity={entity}
                on={selected?.name === entity.name}
                onPick={() => setPicked(entity.name)}
              />
            ))}
          </Section>

          <Section
            kind="cubes"
            title="Cubes"
            subtitle="the tables underneath"
            count={shown.cubes.length}
            info="One cube per table, with its keys and joins. Queryable directly, but a question spanning two cubes depends on the join path the gate happens to compile — which is what views exist to settle. A cube no view exposes is reachable only this way."
            empty={<p className="served-empty">No cube matches.</p>}
          >
            {shown.cubes.map((entity) => (
              <Row
                key={entity.name}
                entity={entity}
                on={selected?.name === entity.name}
                onPick={() => setPicked(entity.name)}
              />
            ))}
          </Section>
        </div>

        <div className="served-detail">
          {selected ? (
            <Detail entity={selected} needle={needle} />
          ) : (
            <p className="served-empty">Nothing matches “{filter}”.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({
  kind,
  title,
  subtitle,
  count,
  info,
  empty,
  children,
}: {
  kind: "views" | "cubes";
  title: string;
  subtitle: string;
  count: number;
  info: string;
  empty: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`served-section ${kind}`}>
      <header className="served-section-head">
        <h4>{title}</h4>
        <span className="served-section-sub">{subtitle}</span>
        <Info label={`About ${title.toLowerCase()}`}>{info}</Info>
        <span className="served-section-count">{count}</span>
      </header>
      {count === 0 ? empty : children}
    </section>
  );
}

/** One line in the list. Never grows — that is the point of the split. */
function Row({
  entity,
  on,
  onPick,
}: {
  entity: ServedEntity;
  on: boolean;
  onPick: () => void;
}) {
  const measures = entity.members.filter((m) => m.kind === "measure").length;
  const dimensions = entity.members.filter((m) => m.kind === "dimension").length;

  return (
    <button
      className={`served-row ${on ? "on" : ""} ${entity.is_view ? "view" : ""}`}
      onClick={onPick}
      aria-current={on}
    >
      <span className="served-row-name">{entity.title || entity.name}</span>
      <span className="served-row-counts">
        {measures}m · {dimensions}d
      </span>
      {!entity.is_view && entity.exposed_by.length === 0 && (
        <span className="served-row-flag" title="Reachable only by querying this cube directly">
          no view
        </span>
      )}
    </button>
  );
}

function Detail({ entity, needle }: { entity: ServedEntity; needle: string }) {
  const members = needle ? entity.members.filter((m) => matches(m, needle)) : entity.members;
  const hidden = entity.members.length - members.length;
  // Only meaningful once some view reaches this cube: marking every member of a cube no
  // view touches repeats what the row already says, on every line.
  const partial = !entity.is_view && entity.exposed_by.length > 0;

  return (
    <>
      <header className="served-detail-head">
        <span className={`served-tag ${entity.is_view ? "view" : ""}`}>
          {entity.is_view ? "view" : "cube"}
        </span>
        <h4>{entity.title || entity.name}</h4>
        <code className="served-detail-id">{entity.name}</code>
      </header>

      <p className="served-rel">
        {entity.is_view ? (
          <>Projects over {entity.draws_from.join(", ") || "nothing"}.</>
        ) : entity.exposed_by.length > 0 ? (
          <>Exposed by {entity.exposed_by.join(", ")}.</>
        ) : (
          <span className="warn">
            No view exposes this cube — a question can only reach it by querying it
            directly, where the join path is whatever the gate compiles.
          </span>
        )}
      </p>

      {entity.description && <p className="served-desc">{entity.description}</p>}
      {entity.ai_context && (
        <p className="served-ai">
          <span className="served-ai-tag">ai_context</span>
          {entity.ai_context}
        </p>
      )}
      {entity.folders.length > 0 && (
        <p className="served-folders">Folders: {entity.folders.map((f) => f.name).join(", ")}</p>
      )}
      {hidden > 0 && (
        <p className="served-folders">
          {hidden} member{hidden === 1 ? "" : "s"} hidden by the filter.
        </p>
      )}

      {KINDS.map(({ key, label }) => {
        const group = members.filter((m) => m.kind === key);
        if (!group.length) return null;
        return (
          <div key={key} className="served-group">
            <h4>{label}</h4>
            <ul className="served-members">
              {group.map((m) => (
                <Member key={m.id} member={m} flagUnexposed={partial} />
              ))}
            </ul>
          </div>
        );
      })}
    </>
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
            problems. A cube no view reaches at all is flagged on the row instead. */}
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
