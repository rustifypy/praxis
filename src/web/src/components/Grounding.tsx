import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { GroundingArtefact } from "../sources";
import { Section } from "./Section";

/**
 * Grounding, as sections of the Serving list rather than a panel of its own.
 *
 * It sits under Views and Cubes because it is the same question continued. Those two say
 * what a question can reach; these say what the agent already believes before the
 * question arrives — the definitions, the house method, the queries someone has checked.
 * Reading an answer you doubt means looking at both, and a separate panel made that two
 * places to look and one of them easy to forget.
 *
 * Read through the agent's own loader server-side, so this cannot list a file the agent
 * ignores: it is the `*.md` glob's output, not a directory listing. That matters more
 * here than it looks — a `.yml` in this directory is not ignored by Cube, it fails the
 * whole model's compile.
 *
 * The one thing a row must carry is `always` versus `agent_requested`, because that is
 * not a category, it is a cost: an `always` rule is in the system prompt of every turn on
 * this source, and an `agent_requested` one is a name in a catalogue until something asks
 * for it.
 */

const KINDS = [
  {
    key: "rule" as const,
    section: "rules",
    title: "Rules",
    subtitle: "what it takes as given",
    info: "What the agent treats as settled. A rule with type: always is concatenated into the system prompt of every turn against this source — that is the expensive half, and where a metric definition belongs. type: agent_requested puts only the name and description in the prompt; the body loads when the agent calls load_grounding for it.",
    empty:
      "No rules. The agent answers with none of your definitions — “revenue” means whatever it infers from member names.",
  },
  {
    key: "skill" as const,
    section: "skills",
    title: "Skills",
    subtitle: "reusable workflows",
    info: "A named procedure for a recurring analysis — the steps, the order, and what to report. Offered by description and loaded on demand, so a skill costs one line in the prompt until something actually uses it.",
    empty: "No skills. Multi-step analyses are re-derived from scratch every time they are asked for.",
  },
  {
    key: "certified" as const,
    section: "certified",
    title: "Certified queries",
    subtitle: "known-good JSON",
    info: "A query someone has checked, matched to the question by its user_request phrasings and handed over by search_model. The only grounding the agent gets without asking for it and without it sitting in every prompt.",
    empty:
      "No certified queries. Every question is planned from the model alone, including the ones asked every week.",
  },
];

export function matchesArtefact(artefact: GroundingArtefact, needle: string): boolean {
  if (!needle) return true;
  return [artefact.name, artefact.description, artefact.user_request, artefact.body]
    .join(" ")
    .toLowerCase()
    .includes(needle);
}

/** The three grounding sections of the list column. */
export function GroundingSections({
  all,
  shown,
  selected,
  onPick,
  note,
  isOpen,
  onToggle,
}: {
  all: GroundingArtefact[];
  shown: GroundingArtefact[];
  selected: string | null;
  onPick: (file: string) => void;
  /** Shown in place of the per-kind empty copy when there is no grounding at all — the
   *  directory path, so an unexpectedly empty list has a diagnosis attached. */
  note: string;
  isOpen: (section: string) => boolean;
  onToggle: (section: string) => void;
}) {
  const bare = all.length === 0;

  return (
    <>
      {KINDS.map((kind) => {
        const group = shown.filter((a) => a.kind === kind.key);
        const total = all.filter((a) => a.kind === kind.key).length;
        return (
          <Section
            key={kind.key}
            kind={kind.section}
            title={kind.title}
            subtitle={kind.subtitle}
            count={group.length}
            info={kind.info}
            open={isOpen(kind.section)}
            onToggle={() => onToggle(kind.section)}
            empty={
              <p className="served-empty">
                {total > 0 ? "No match." : kind.empty}
                {/* Once, under the last section rather than three times. An empty
                    grounding directory changes no error and no status dot — it changes
                    answers — so the path it looked in is the only diagnosis there is. */}
                {bare && kind.key === "certified" && note && (
                  <span className="ground-path">
                    Nothing in <code>{note}</code>
                  </span>
                )}
              </p>
            }
          >
            {group.map((artefact) => (
              <button
                key={artefact.file}
                className={`served-row ${selected === artefact.file ? "on" : ""} ${
                  artefact.kind === "rule" ? "view" : ""
                }`}
                onClick={() => onPick(artefact.file)}
                aria-current={selected === artefact.file}
              >
                <span className="served-row-name">{artefact.name}</span>
                {/* Only on the half that costs something. Badging `agent_requested` too
                    would put a badge on every row and leave the expensive half
                    indistinguishable from the free one. */}
                {artefact.loading === "always" && (
                  <span
                    className="ground-always"
                    title="Concatenated into the system prompt of every turn"
                  >
                    always
                  </span>
                )}
              </button>
            ))}
          </Section>
        );
      })}
    </>
  );
}

/** One artefact in the detail box, beside the entities. */
export function ArtefactDetail({
  artefact,
  root,
}: {
  artefact: GroundingArtefact;
  root: string;
}) {
  return (
    <>
      <header className="served-detail-head">
        <span className={`served-tag ${artefact.kind === "rule" ? "view" : ""}`}>
          {artefact.kind}
        </span>
        <h4>{artefact.name}</h4>
        <code className="served-detail-id" title={root ? `${root}/${artefact.file}` : undefined}>
          {artefact.file}
        </code>
      </header>

      <p className="served-rel">
        {artefact.kind === "rule" &&
          (artefact.loading === "always" ? (
            <>In the system prompt of every turn against this source.</>
          ) : (
            <>
              Offered as a name and description; the body below reaches the agent only when
              it calls <code>load_grounding("{artefact.name}")</code>.
            </>
          ))}
        {artefact.kind === "skill" && (
          <>
            Offered by description; loaded when the agent calls{" "}
            <code>load_grounding("{artefact.name}")</code>.
          </>
        )}
        {artefact.kind === "certified" &&
          (artefact.user_request ? (
            <>Surfaced by search_model when the question resembles: {artefact.user_request}</>
          ) : (
            <span className="warn">
              No <code>user_request</code> in its frontmatter, so nothing matches it to a
              question and the agent will never see it.
            </span>
          ))}
      </p>

      {artefact.description && <p className="served-desc">{artefact.description}</p>}

      {/* Rendered as the markdown it is. These are written to be read; a preformatted
          blob would undo the reason they are markdown in the first place. */}
      <div className="ground-body">
        <Markdown remarkPlugins={[remarkGfm]}>{artefact.body}</Markdown>
      </div>
    </>
  );
}
