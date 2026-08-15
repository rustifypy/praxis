import { useEffect, useState } from "react";

import * as api from "../api";
import type { FilePreview } from "../api";
import { Collapse } from "./Collapse";

/**
 * What a script wrote, below the answer that used it.
 *
 * Built on the model of `ResultBlock`: the transcript's job is to put the evidence next to the
 * claim, and a chart the agent drew is evidence. The files themselves are never in the event —
 * they are on disk in the workspace, so this renders from it and the browser fetches an image
 * the same way it fetches any other.
 *
 * A CSV is evidence in the same sense, and a download link is not a preview: the numbers are
 * the half that gets checked, and a click that leaves the page to check them is a click nobody
 * makes. So anything readable opens as a table or a head of text right here, `/preview` decides
 * which, and the download stays for the rest of it.
 *
 * Which extensions render inline is the server's rule (`web/files.INLINE` for images,
 * `TABULAR`/`READABLE` for the rest); this list is the same question asked of the same names.
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

      {rest.map((path) => (
        <Wrote key={path} chatId={chatId} path={path} />
      ))}
    </div>
  );
}

/**
 * One non-image file, open on arrival.
 *
 * Open rather than folded because the complaint this answers is that the data was one click
 * away and therefore unread. The cost is a request per file when a chat is reopened — which is
 * what `files` has always cost, since the paths are all the event carries and an image already
 * fetches itself. The body is bounded instead, so a long file does not push the transcript.
 *
 * It reads what is on disk *now*, so a script that overwrites `data.csv` changes what an older
 * block shows. True of the images above it for the same reason, and the run that wrote each
 * version is still in the trace above.
 */
function Wrote({ chatId, path }: { chatId: string; path: string }) {
  const [open, setOpen] = useState(true);
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [failed, setFailed] = useState("");

  useEffect(() => {
    if (!open || preview || failed) return;
    let live = true;
    api.filePreview(chatId, path).then(
      (read) => live && setPreview(read),
      (err) => live && setFailed(String(err)),
    );
    return () => {
      live = false;
    };
  }, [open, preview, failed, chatId, path]);

  const foldable = preview?.kind !== "none";

  return (
    <div className="wrote-file">
      <div className="wrote-file-head">
        <button
          className="wrote-toggle"
          onClick={() => foldable && setOpen(!open)}
          disabled={!foldable}
        >
          {foldable && <span className="caret">{open ? "▾" : "▸"}</span>}
          <span className="wrote-name">{path}</span>
        </button>
        <span className="wrote-meta">{preview ? shape(preview) : ""}</span>
        <a className="wrote-get" href={api.chatFileUrl(chatId, path)} download>
          Download
        </a>
      </div>

      <Collapse open={open && foldable}>
        <div className="wrote-body">
          {failed && <p className="res-failed">{failed}</p>}
          {!preview && !failed && <p className="wrote-note">Reading…</p>}

          {preview?.kind === "table" && <PreviewTable preview={preview} />}
          {preview?.kind === "text" && (
            <pre className="wrote-text">
              {preview.text.trimEnd() || "This file is empty."}
              {preview.truncated ? "\n\n… download for the rest." : ""}
            </pre>
          )}
        </div>
      </Collapse>
    </div>
  );
}

/** The header's summary: enough to know whether what is below is the whole file. */
function shape(preview: FilePreview): string {
  const size =
    preview.bytes < 1024
      ? `${preview.bytes} B`
      : preview.bytes < 1024 * 1024
        ? `${Math.round(preview.bytes / 1024)} kB`
        : `${(preview.bytes / 1024 / 1024).toFixed(1)} MB`;
  if (preview.kind !== "table") return size;
  const rows = `${preview.rows.length.toLocaleString()}${preview.truncated ? "+" : ""}`;
  const columns = preview.columns.length;
  return `${rows} × ${columns} · ${size}`;
}

/**
 * The head of a CSV as a table.
 *
 * Not `ResultBlock`'s: that one sorts, pages and formats against `ResultColumn` metadata Cube
 * supplied, and none of it exists for bytes a script wrote. Every cell here is the string that
 * is in the file — the only judgement made is which columns are numbers, and that is made from
 * the values rather than from a name.
 */
function PreviewTable({ preview }: { preview: FilePreview }) {
  const numeric = preview.columns.map(
    (_, i) =>
      preview.rows.some((row) => row[i] !== "") &&
      preview.rows.every((row) => row[i] === "" || Number.isFinite(Number(row[i]))),
  );

  return (
    <>
      <div className="res-scroll">
        <table className="res-table wrote-table">
          <thead>
            <tr>
              {preview.columns.map((name, i) => (
                <th key={i} className={numeric[i] ? "res-num" : undefined}>
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j} className={numeric[j] ? "res-num" : undefined}>
                    {cell === "" ? "—" : cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {preview.truncated && (
        <p className="res-note">
          The first {preview.rows.length.toLocaleString()} rows. Download the file for the rest.
        </p>
      )}
    </>
  );
}
