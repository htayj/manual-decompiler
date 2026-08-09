import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";

const MAX_BODY_BYTES = 1024 * 1024;
const SHA256 = /^[a-f0-9]{64}$/;
const ASSET_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const DISPOSITIONS = new Set(["accept", "reject", "needs-fix"]);
const MEDIA_TYPES = new Set([
  "image/svg+xml",
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "application/pdf",
]);

export class ReviewProjectError extends Error {}

export function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

export function annotationsPath(projectPath) {
  const parsed = path.parse(projectPath);
  return path.join(parsed.dir, `${parsed.name}.annotations.json`);
}

function object(value, label) {
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new ReviewProjectError(`${label} must be an object`);
  }
  return value;
}

function string(value, label) {
  if (typeof value !== "string" || !value.trim()) {
    throw new ReviewProjectError(`${label} must be a non-empty string`);
  }
  return value;
}

function projectPathFromEnv(env) {
  const configured = env.LISPMDOC_REVIEW_PROJECT;
  if (!configured) {
    throw new ReviewProjectError(
      "LISPMDOC_REVIEW_PROJECT is required and must name a review-project JSON file",
    );
  }
  return path.resolve(configured);
}

function safeAssetPath(rootRealPath, projectDirectory, candidate, label) {
  if (typeof candidate !== "string" || !candidate || candidate.includes("\0")) {
    throw new ReviewProjectError(`${label}.path must be a non-empty relative path`);
  }
  if (path.isAbsolute(candidate)) {
    throw new ReviewProjectError(`${label}.path must be relative`);
  }
  const resolved = path.resolve(projectDirectory, candidate);
  const relative = path.relative(rootRealPath, resolved);
  if (relative === "" || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new ReviewProjectError(`${label}.path escapes the project directory`);
  }
  let realPath;
  try {
    realPath = fs.realpathSync(resolved);
  } catch (error) {
    throw new ReviewProjectError(`${label}.path cannot be resolved: ${error.message}`);
  }
  const realRelative = path.relative(rootRealPath, realPath);
  if (
    realRelative === "" ||
    realRelative === ".." ||
    realRelative.startsWith(`..${path.sep}`) ||
    path.isAbsolute(realRelative)
  ) {
    throw new ReviewProjectError(`${label}.path resolves outside the project directory`);
  }
  const stat = fs.statSync(realPath);
  if (!stat.isFile()) {
    throw new ReviewProjectError(`${label}.path must be a regular file`);
  }
  return realPath;
}

function assetDefinition(value, rootRealPath, projectDirectory, id) {
  const definition = typeof value === "string" ? { path: value } : object(value, `assets.${id}`);
  const assetPath = safeAssetPath(rootRealPath, projectDirectory, definition.path, `assets.${id}`);
  const expectedSha256 = string(definition.sha256, `assets.${id}.sha256`);
  if (!SHA256.test(expectedSha256)) {
    throw new ReviewProjectError(`assets.${id}.sha256 must be a lower-case SHA-256 digest`);
  }
  const actualSha256 = sha256(fs.readFileSync(assetPath));
  if (actualSha256 !== expectedSha256) {
    throw new ReviewProjectError(`assets.${id}.sha256 does not match its exact file bytes`);
  }
  return {
    path: assetPath,
    sha256: expectedSha256,
    mediaType: assetMediaType(definition.media_type, assetPath, id),
  };
}

function assetMediaType(declared, assetPath, id) {
  const inferred = mimeType({ path: assetPath });
  const mediaType = declared ?? inferred;
  if (!MEDIA_TYPES.has(mediaType)) {
    throw new ReviewProjectError(`assets.${id}.media_type is not an allowed review media type`);
  }
  return mediaType;
}

function pageIds(manifest, assetIds) {
  if (!Array.isArray(manifest.pages) || manifest.pages.length === 0) {
    throw new ReviewProjectError("pages must be a non-empty array");
  }
  const ids = new Set();
  const regionsByPage = new Map();
  for (const [index, rawPage] of manifest.pages.entries()) {
    const page = object(rawPage, `pages[${index}]`);
    const id = string(page.id, `pages[${index}].id`);
    if (ids.has(id)) throw new ReviewProjectError(`duplicate page id: ${id}`);
    ids.add(id);
    for (const viewKey of ["reference_asset_id", "generated_asset_id", "overlay_asset_id"]) {
      if (page[viewKey] !== undefined && !assetIds.has(page[viewKey])) {
        throw new ReviewProjectError(`pages[${index}].${viewKey} names an undeclared asset`);
      }
    }
    const regions = page.regions ?? [];
    if (!Array.isArray(regions)) throw new ReviewProjectError(`pages[${index}].regions must be an array`);
    const regionIds = new Set();
    for (const [regionIndex, rawRegion] of regions.entries()) {
      const region = object(rawRegion, `pages[${index}].regions[${regionIndex}]`);
      const regionId = string(region.id, `pages[${index}].regions[${regionIndex}].id`);
      if (regionIds.has(regionId)) {
        throw new ReviewProjectError(`duplicate region id ${regionId} on page ${id}`);
      }
      regionIds.add(regionId);
    }
    regionsByPage.set(id, regionIds);
  }
  return { ids, regionsByPage };
}

/** Load and validate a manifest. All asset paths are resolved and checked here. */
export function loadReviewProject({ env = process.env } = {}) {
  const projectPath = projectPathFromEnv(env);
  let raw;
  try {
    raw = fs.readFileSync(projectPath);
  } catch (error) {
    throw new ReviewProjectError(`cannot read review project: ${error.message}`);
  }
  let manifest;
  try {
    manifest = JSON.parse(raw.toString("utf8"));
  } catch (error) {
    throw new ReviewProjectError(`review project is not valid JSON: ${error.message}`);
  }
  object(manifest, "review project");
  const rootRealPath = fs.realpathSync(path.dirname(projectPath));
  const assetsField = object(manifest.assets, "assets");
  const assets = new Map();
  for (const [id, definition] of Object.entries(assetsField)) {
    if (!ASSET_ID.test(id)) throw new ReviewProjectError(`invalid asset id: ${id}`);
    assets.set(id, assetDefinition(definition, rootRealPath, path.dirname(projectPath), id));
  }
  const { ids: pageIdsSet, regionsByPage } = pageIds(manifest, assets);
  return {
    projectPath,
    projectDirectory: rootRealPath,
    raw,
    projectSha256: sha256(raw),
    manifest,
    assets,
    pageIds: pageIdsSet,
    regionsByPage,
  };
}

export function mimeType(asset) {
  if (asset.mediaType) return asset.mediaType;
  switch (path.extname(asset.path).toLowerCase()) {
    case ".svg": return "image/svg+xml";
    case ".png": return "image/png";
    case ".jpg":
    case ".jpeg": return "image/jpeg";
    case ".webp": return "image/webp";
    case ".gif": return "image/gif";
    case ".pdf": return "application/pdf";
    default: return "application/octet-stream";
  }
}

function annotationError(message) {
  return new ReviewProjectError(`invalid annotations: ${message}`);
}

function reviewField(value, label) {
  if (value !== undefined && (typeof value !== "string" || value.length > 100_000)) {
    throw annotationError(`${label} must be a string no longer than 100000 characters`);
  }
}

function nativeDecision(value, page, label) {
  const decision = object(value, `${label}.native_decision`);
  const expectedKeys = new Set(["regions", "reading_order", "excluded_word_ids", "finding_dispositions", "region_dispositions", "acceptance"]);
  for (const key of Object.keys(decision)) if (!expectedKeys.has(key)) throw annotationError(`${label}.native_decision.${key} is not allowed`);
  for (const key of expectedKeys) if (!(key in decision)) throw annotationError(`${label}.native_decision.${key} is required`);
  const declared = page.native_pdf_authority;
  if (!declared || !Array.isArray(page.regions) || !Array.isArray(declared.default_reading_order) || !Array.isArray(declared.default_excluded_word_ids) || !Array.isArray(declared.findings)) throw annotationError("native-PDF manifest lacks a guarded decision contract");
  if (!Array.isArray(decision.regions) || decision.regions.length !== page.regions.length) throw annotationError(`${label}.native_decision.regions must exactly match the fixed manifest regions`);
  for (let index = 0; index < page.regions.length; index += 1) {
    const candidate = object(decision.regions[index], `${label}.native_decision.regions[${index}]`);
    for (const key of Object.keys(candidate)) if (!["id", "role", "word_ids"].includes(key)) throw annotationError("native region has an unallowed field");
    const fixed = page.regions[index];
    if (candidate.id !== fixed.id || candidate.role !== fixed.role || !Array.isArray(candidate.word_ids) || JSON.stringify(candidate.word_ids) !== JSON.stringify(fixed.word_ids)) throw annotationError("native region must exactly retain fixed Poppler IDs and role");
  }
  if (!Array.isArray(decision.reading_order) || JSON.stringify(decision.reading_order) !== JSON.stringify(declared.default_reading_order)) throw annotationError("native reading_order must retain the fixed prefill");
  if (!Array.isArray(decision.excluded_word_ids) || JSON.stringify(decision.excluded_word_ids) !== JSON.stringify(declared.default_excluded_word_ids)) throw annotationError("native exclusions must retain the fixed prefill");
  const findings = new Set(declared.findings.map((item) => item.id));
  const regionIds = new Set(page.regions.map((item) => item.id));
  for (const [kind, expected, allowed] of [["finding_dispositions", findings, new Set(["accepted", "not-applicable", "needs-follow-up"])], ["region_dispositions", regionIds, new Set(["accept", "reject", "needs-fix"])]]) {
    const map = object(decision[kind], `${label}.native_decision.${kind}`);
    if (Object.keys(map).length !== expected.size) throw annotationError(`native ${kind} must be exhaustive`);
    for (const [id, disposition] of Object.entries(map)) if (!expected.has(id) || !allowed.has(disposition)) throw annotationError(`native ${kind} has an unknown ID or disposition`);
  }
  const acceptance = object(decision.acceptance, `${label}.native_decision.acceptance`);
  const gates = ["layout", "reading_order", "semantics", "object_extraction"];
  if (Object.keys(acceptance).length !== gates.length || gates.some((gate) => typeof acceptance[gate] !== "boolean")) throw annotationError("native acceptance must contain exactly four booleans");
  return decision;
}

/** Validate that an annotation can only address manifest-declared pages/regions. */
export function validateAnnotationPayload(value, project, expectedAnnotationsSha256) {
  const payload = object(value, "annotation payload");
  if (payload.project_sha256 !== project.projectSha256) {
    throw annotationError("project_sha256 does not match the exact loaded project bytes");
  }
  const reviewer = string(payload.reviewer, "reviewer").trim();
  if (reviewer.length > 200) throw annotationError("reviewer is too long");
  if (payload.base_annotations_sha256 !== expectedAnnotationsSha256) {
    const expected = expectedAnnotationsSha256 ?? null;
    throw Object.assign(annotationError("annotations changed; reload before saving"), { status: 409, expected });
  }
  const annotations = object(payload.annotations, "annotations");
  const pages = object(annotations.pages, "annotations.pages");
  for (const [pageId, pageValue] of Object.entries(pages)) {
    if (!project.pageIds.has(pageId)) throw annotationError(`unknown page: ${pageId}`);
    const page = object(pageValue, `annotations.pages.${pageId}`);
    const native = project.manifest.review_mode === "native-pdf-authority";
    if (native) {
      const allowed = new Set(["disposition", "notes", "native_decision"]);
      for (const key of Object.keys(page)) if (!allowed.has(key)) throw annotationError(`pages.${pageId}.${key} is not allowed in native-PDF review`);
      if (page.native_decision !== undefined) {
        nativeDecision(page.native_decision, project.manifest.pages.find((item) => item.id === pageId), `pages.${pageId}`);
      }
      if (page.regions !== undefined) throw annotationError("native-PDF review uses one guarded page decision, not region text annotations");
    }
    if (page.disposition !== undefined && !DISPOSITIONS.has(page.disposition)) {
      throw annotationError(`pages.${pageId}.disposition is invalid`);
    }
    reviewField(page.notes, `pages.${pageId}.notes`);
    const regions = object(page.regions ?? {}, `annotations.pages.${pageId}.regions`);
    for (const [regionId, regionValue] of Object.entries(regions)) {
      if (!project.regionsByPage.get(pageId).has(regionId)) {
        throw annotationError(`unknown region ${regionId} on page ${pageId}`);
      }
      const region = object(regionValue, `annotations.pages.${pageId}.regions.${regionId}`);
      if (region.disposition !== undefined && !DISPOSITIONS.has(region.disposition)) {
        throw annotationError(`region ${regionId}.disposition is invalid`);
      }
      reviewField(region.canonical_text, `region ${regionId}.canonical_text`);
      reviewField(region.notes, `region ${regionId}.notes`);
    }
  }
  return { reviewer, annotations };
}

async function readBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw Object.assign(new ReviewProjectError("request body is too large"), { status: 413 });
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch (error) {
    throw new ReviewProjectError(`request body is not JSON: ${error.message}`);
  }
}

async function readAnnotations(project) {
  const destination = annotationsPath(project.projectPath);
  try {
    const raw = await fsp.readFile(destination);
    return { value: JSON.parse(raw.toString("utf8")), sha256: sha256(raw) };
  } catch (error) {
    if (error.code === "ENOENT") return { value: null, sha256: null };
    throw new ReviewProjectError(`cannot read annotations: ${error.message}`);
  }
}

async function writeAtomically(destination, value) {
  const directory = path.dirname(destination);
  const temp = path.join(directory, `.${path.basename(destination)}.${crypto.randomUUID()}.tmp`);
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  try {
    await fsp.writeFile(temp, bytes, { mode: 0o600, flag: "wx" });
    await fsp.rename(temp, destination);
  } finally {
    await fsp.rm(temp, { force: true }).catch(() => {});
  }
  return sha256(bytes);
}

function json(response, status, value, headers = {}) {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    ...headers,
  });
  response.end(JSON.stringify(value));
}

function error(response, errorValue) {
  const status = errorValue.status ?? 400;
  json(response, status, { error: errorValue.message, expected_annotations_sha256: errorValue.expected });
}

function currentProject() {
  // Reload on every request so an edited manifest cannot silently retain an old
  // asset allow-list or accept annotations bound to earlier bytes.
  return loadReviewProject();
}

export function reviewProjectPlugin() {
  return {
    name: "lispmdoc-review-project",
    configureServer(server) {
      server.middlewares.use("/api", async (request, response, next) => {
        try {
          const project = currentProject();
          const url = new URL(request.url, "http://localhost");
          if (request.method === "GET" && url.pathname === "/project") {
            response.writeHead(200, {
              "Content-Type": "application/json; charset=utf-8",
              "Cache-Control": "no-store",
              "X-Content-Type-Options": "nosniff",
              "X-Lispmdoc-Project-SHA256": project.projectSha256,
            });
            response.end(project.raw);
            return;
          }
          if (request.method === "GET" && url.pathname === "/annotations") {
            const annotations = await readAnnotations(project);
            json(response, 200, {
              project_sha256: project.projectSha256,
              annotations_sha256: annotations.sha256,
              value: annotations.value,
            });
            return;
          }
          if (request.method === "GET" && url.pathname.startsWith("/assets/")) {
            const assetId = decodeURIComponent(url.pathname.slice("/assets/".length));
            const asset = project.assets.get(assetId);
            if (!asset) {
              json(response, 404, { error: "undeclared asset" });
              return;
            }
            // Re-resolve to defend against a subsequently swapped symlink. Read
            // the exact bytes after that check, verify them, then send that same
            // buffer so the digest describes the bytes actually served.
            const real = fs.realpathSync(asset.path);
            const relative = path.relative(project.projectDirectory, real);
            if (relative === "" || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
              throw new ReviewProjectError("declared asset now resolves outside the project directory");
            }
            const bytes = await fsp.readFile(real);
            if (sha256(bytes) !== asset.sha256) {
              throw Object.assign(new ReviewProjectError("declared asset bytes no longer match their SHA-256"), { status: 409 });
            }
            response.writeHead(200, {
              "Content-Type": mimeType(asset),
              "Cache-Control": "no-store",
              "X-Content-Type-Options": "nosniff",
              "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:",
            });
            response.end(bytes);
            return;
          }
          if (request.method === "POST" && url.pathname === "/annotations") {
            const existing = await readAnnotations(project);
            const payload = await readBody(request);
            const { reviewer, annotations } = validateAnnotationPayload(payload, project, existing.sha256);
            const persisted = {
              format_version: "1.0",
              project_sha256: project.projectSha256,
              document_id: typeof project.manifest.document_id === "string" ? project.manifest.document_id : null,
              reviewer,
              saved_at: new Date().toISOString(),
              annotations,
            };
            const annotationsSha256 = await writeAtomically(annotationsPath(project.projectPath), persisted);
            json(response, 201, { annotations_sha256: annotationsSha256, value: persisted });
            return;
          }
          next();
        } catch (caught) {
          error(response, caught instanceof Error ? caught : new ReviewProjectError("unexpected review server error"));
        }
      });
    },
  };
}
