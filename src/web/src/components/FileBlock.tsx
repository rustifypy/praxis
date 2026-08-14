import * as api from "../api";

/**
 * What a script wrote, below the answer that used it.
 *
 * Built on the model of `ResultBlock`: the transcript's job is to put the evidence next to the
 * claim, and a chart the agent drew is evidence. The files themselves are never in the event —
 * they are on disk in the workspace, so this renders links into it and the browser fetches an
 * image the same way it fetches any other.
 *
 * Which extensions render inline is the server's rule (`web/files.INLINE`); this list is the
 * same question asked of the same names, and anything else is a download.
 */

const IMAGES = [".png", ".jpg", ".jpeg", ".gif", ".webp"];

export function FileBlock({
  chatId,
  label,
  paths,
}: {
  chatId: string;
  label: string;
  paths: string[];
}) {
  const images = paths.filter((p) => IMAGES.some((ext) => p.toLowerCase().endsWith(ext)));
  const rest = paths.filter((p) => !images.includes(p));

  return (
    <div className="wrote">
      <div className="wrote-head">
        <span className="wrote-label">
          {paths.length === 1 ? "1 file" : `${paths.length} files`} from {label}
        </span>
      </div>

      {images.map((path) => (
        <a key={path} className="wrote-figure" href={api.chatFileUrl(chatId, path)} target="_blank">
          <img src={api.chatFileUrl(chatId, path)} alt={path} />
          <span className="wrote-caption">{path}</span>
        </a>
      ))}

      {rest.length > 0 && (
        <ul className="wrote-list">
          {rest.map((path) => (
            <li key={path}>
              <a href={api.chatFileUrl(chatId, path)} download>
                {path}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
