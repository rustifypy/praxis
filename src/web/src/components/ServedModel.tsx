import { useEffect, useMemo, useState } from "react";

import type { ServedEntity, ServedMember, ServedModel as Model, SourceGrounding } from "../sources";
import { ArtefactDetail, GroundingSections, matchesArtefact } from "./Grounding";
import { Section } from "./Section";

/**
 * Everything the agent brings to a question: what Cube compiled, and what this source
 * already told it.
 *
 * Deliberately not a YAML viewer for the model half — the files on disk are what was
 * authored, this is what survived compilation.
 *
 * Master–detail rather than accordions. Expanding a cube in place pushed every row below
 * it down the page, so comparing two of them meant losing your position twice; with
 * thirteen entities and seventy members the list never held still. The list is now a
 * fixed column and the members render in their own box beside it, so clicking through
 * entities moves nothing.
 *
 * Views and cubes are separated because they are not two flavours of the same thing. A
 * view is the curated surface a question should reach; a cube is the table underneath,
 * and reaching across two of them is the case the gate can only warn about. Grounding
 * follows them in the same column rather than in a panel of its own: it answers the
 * continuation of the same question, and split across two panels one of them goes
 * unread.
 *
 * The two halves fail independently, which is the one thing this component has to keep
 * straight. The model needs the container up and Cube compiling; grounding is markdown on
 * this machine and reads perfectly well when Cube is down. So a model failure is a banner
 * over an empty Views and Cubes, never over an empty list.
 */

type Picked = { type: "entity"; id: string } | { type: "artefact"; id: string };

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
  grounding,
  loading,
  onRefresh,
  unavailable,
}: {
  model: Model | null;
  grounding: SourceGrounding | null;
  loading: boolean;
  onRefresh: () => void;
  /** Why the model side cannot be read at all — a stopped container. Distinct from a
   *  Cube that answered badly, and it must not take grounding down with it. */
  unavailable?: string;
}) {
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<Picked | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
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

  const artefacts = useMemo(() => {
    const all = grounding?.artefacts ?? [];
    return needle ? all.filter((a) => matchesArtefact(a, needle)) : all;
  }, [grounding, needle]);

  /**
   * What the box opens on under a filter, and only under a filter.
   *
   * With the sections shut by default there is nothing to open on arrival — a detail box
   * volunteering the first view would make Views the one section that is never collapsed.
   * A search is different: it has to land on the strongest match, because searching
   * "revenue" and opening the one view that does not mention it is a worse answer than
   * the list already gave. The *list* stays in name order regardless — re-ranking rows
   * while someone types moves the thing they are reaching for.
   *
   * Entities win over grounding when both match: with a model present the model is what
   * you came for, and an artefact opens when the filter has left nothing else — or when
   * there is no model at all, which is what a stopped container looks like from here.
   */
  const fallback: Picked | null = useMemo(() => {
    if (!needle) return null;
    if (listed.length) {
      const score = (e: ServedEntity) =>
        (e.name.toLowerCase().includes(needle) ? 1000 : 0) +
        e.members.filter((m) => matches(m, needle)).length;
      return { type: "entity", id: [...listed].sort((a, b) => score(b) - score(a))[0].name };
    }
    return artefacts.length ? { type: "artefact", id: artefacts[0].file } : null;
  }, [listed, artefacts, needle]);

  const alive = (pick: Picked) =>
    pick.type === "entity"
      ? listed.some((e) => e.name === pick.id)
      : artefacts.some((a) => a.file === pick.id);

  const selected = picked && alive(picked) ? picked : fallback;
  const entity = selected?.type === "entity" ? listed.find((e) => e.name === selected.id) : null;
  const artefact =
    selected?.type === "artefact" ? artefacts.find((a) => a.file === selected.id) : null;

  /**
   * Sections are shut until asked for, with one exception that is not a preference: a
   * filter must reveal what it matched. Typing into a column of shut headers and watching
   * only the counts change is a search that appears to do nothing.
   *
   * Nothing else forces a section open — collapsing one while its row is in the detail box
   * is allowed, and leaves the detail alone. The two panes are not a single widget.
   */
  const isOpen = (section: string) => Boolean(needle) || expanded.has(section);

  const toggle = (section: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });

  // A filter that excludes the open row would otherwise leave the box showing something
  // the list no longer offers.
  useEffect(() => {
    if (picked && !alive(picked)) setPicked(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [picked, listed, artefacts]);

  // Counted over cubes only. A view's members are the same members re-exported, so
  // flattening every entity reported a model roughly twice its real size.
  const totals = useMemo(() => {
    const entities = model?.entities ?? [];
    const cubeMembers = entities.filter((e) => !e.is_view).flatMap((e) => e.members);
    const files = grounding?.artefacts ?? [];
    return {
      views: entities.filter((e) => e.is_view).length,
      cubes: entities.filter((e) => !e.is_view).length,
      members: cubeMembers.length,
      exposed: cubeMembers.filter((m) => m.exposed_by.length > 0).length,
      described: cubeMembers.filter((m) => m.description).length,
      grounded: files.length,
      // The half with a standing cost, which is the only grounding number worth a
      // headline: these are in the system prompt of every turn.
      always: files.filter((a) => a.kind === "rule" && a.loading === "always").length,
    };
  }, [model, grounding]);

  /**
   * Why Views and Cubes are empty, or null when they are not.
   *
   * These were four early returns, each of which also hid grounding — which is on local
   * disk and had nothing to do with any of them. Now they are the sections' empty copy,
   * and the list always renders.
   */
  const modelNote =
    unavailable ??
    (loading && !model
      ? "Reading /v1/meta…"
      : !model
        ? "Nothing read yet."
        : !model.serving
          ? "Cube is not serving a model — see above."
          : totals.cubes + totals.views === 0
            ? "Cube answered, but is serving no cubes."
            : null);

  if (!model && !grounding && loading) return <p className="served-empty">Reading…</p>;

  return (
    <div className="served">
      <div className="served-head">
        <span className="served-totals">
          {modelNote && !model?.serving ? (
            <>Model unavailable</>
          ) : (
            <>
              {totals.cubes} cube{totals.cubes === 1 ? "" : "s"}, {totals.members} member
              {totals.members === 1 ? "" : "s"} ·{" "}
              {totals.views === 0
                ? "no views"
                : `${totals.views} view${totals.views === 1 ? "" : "s"} expose ${totals.exposed}`}
            </>
          )}{" "}
          ·{" "}
          {grounding === null
            ? "grounding unread"
            : totals.grounded === 0
              ? "no grounding"
              : `${totals.grounded} grounding file${totals.grounded === 1 ? "" : "s"}${
                  totals.always ? `, ${totals.always} in every prompt` : ""
                }`}
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

      {/* Above the split, not instead of it. A compile failure leaves /v1/meta empty while
          the container stays perfectly healthy, which is why the dot above can be green —
          and why this needs saying rather than showing an empty list. */}
      {model && !model.serving && !unavailable && (
        <p className="banner bad">
          Cube did not answer <code>/v1/meta</code>: {model.error || "no response"}. Either
          it is still booting, or the model failed to compile. The container output says
          which. Grounding below is unaffected — it is read from disk, not from Cube.
        </p>
      )}

      <div className="served-split">
        <div className="served-list">
          <Section
            kind="views"
            title="Views"
            subtitle="ask here"
            count={shown.views.length}
            open={isOpen("views")}
            onToggle={() => toggle("views")}
            info="A view is the curated surface: it re-exports members from one or more cubes under names chosen for the business, with the joins already decided. The grounding rules point the agent here first, because a query inside a single view cannot pick a wrong join path."
            empty={
              <p className="served-empty">
                {modelNote ??
                  (totals.views === 0
                    ? "None yet. Run Build views — until then every question has to reach into the cubes directly, and the gate can only warn about joins, not choose them."
                    : "No view matches.")}
              </p>
            }
          >
            {shown.views.map((entity) => (
              <Row
                key={entity.name}
                entity={entity}
                on={selected?.type === "entity" && selected.id === entity.name}
                onPick={() => setPicked({ type: "entity", id: entity.name })}
              />
            ))}
          </Section>

          <Section
            kind="cubes"
            title="Cubes"
            subtitle="the tables underneath"
            count={shown.cubes.length}
            open={isOpen("cubes")}
            onToggle={() => toggle("cubes")}
            info="One cube per table, with its keys and joins. Queryable directly, but a question spanning two cubes depends on the join path the gate happens to compile — which is what views exist to settle. A cube no view exposes is reachable only this way."
            empty={<p className="served-empty">{modelNote ?? "No cube matches."}</p>}
          >
            {shown.cubes.map((entity) => (
              <Row
                key={entity.name}
                entity={entity}
                on={selected?.type === "entity" && selected.id === entity.name}
                onPick={() => setPicked({ type: "entity", id: entity.name })}
              />
            ))}
          </Section>

          <GroundingSections
            all={grounding?.artefacts ?? []}
            shown={artefacts}
            selected={selected?.type === "artefact" ? selected.id : null}
            onPick={(file) => setPicked({ type: "artefact", id: file })}
            note={grounding && grounding.artefacts.length === 0 ? grounding.root : ""}
            isOpen={isOpen}
            onToggle={toggle}
          />
        </div>

        <div className="served-detail">
          {entity ? (
            <Detail entity={entity} needle={needle} />
          ) : artefact ? (
            <ArtefactDetail artefact={artefact} root={grounding?.root ?? ""} />
          ) : (
            <p className="served-empty">
              {filter
                ? `Nothing matches “${filter}”.`
                : "Open a section and pick a view, cube or grounding file."}
            </p>
          )}
        </div>
      </div>
    </div>
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
