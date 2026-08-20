const STORE = process.env.MODEL_STORE_URL;
const TOKEN = process.env.MODEL_STORE_TOKEN;
const DEFAULT_REF = process.env.MODEL_REF || 'main';
const TTL_MS = Number(process.env.MODEL_VERSION_TTL_MS || 5000);

const seen = new Map();

const refOf = (ctx) => (ctx && ctx.securityContext && ctx.securityContext.ref) || DEFAULT_REF;

const get = async (path) => {
  const res = await fetch(`${STORE}${path}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  if (!res.ok) throw new Error(`model-store ${path}: ${res.status}`);
  return res.json();
};

const version = async (ref) => {
  const cached = seen.get(ref);
  if (cached && Date.now() - cached.at < TTL_MS) return cached.sha;
  try {
    const { sha } = await get(`/version?ref=${encodeURIComponent(ref)}`);
    seen.set(ref, { sha, at: Date.now() });
    return sha;
  } catch (err) {
    // schemaVersion runs on every request, so a store outage has to degrade to a stale
    // model rather than taking Cube down with it.
    if (cached) return cached.sha;
    throw err;
  }
};

const files = async (ref) => {
  const body = await get(`/files?ref=${encodeURIComponent(ref)}`);
  return Object.entries(body.files).map(([fileName, content]) => ({ fileName, content }));
};

module.exports = {
  contextToAppId: (ctx) => `cube_${refOf(ctx)}`,
  schemaVersion: (ctx) => version(refOf(ctx)),
  // The factory is called once per app id; the dataSchemaFiles it returns runs on every
  // recompile. Capturing ref is safe only because the app id is derived from it.
  repositoryFactory: (ctx) => {
    const ref = refOf(ctx);
    return { dataSchemaFiles: () => files(ref) };
  },
};
