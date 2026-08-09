import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { annotationsPath, loadReviewProject, validateAnnotationPayload } from "../server/review-project.mjs";

async function fixture(manifest) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "lispmdoc-review-"));
  await fs.writeFile(path.join(directory, "scan.png"), "scan");
  const projectPath = path.join(directory, "project.json");
  await fs.writeFile(projectPath, JSON.stringify(manifest));
  return { directory, projectPath, env: { LISPMDOC_REVIEW_PROJECT: projectPath } };
}

function manifest(assetPath = "scan.png", assetSha256 = "59ad1b2fc74287ded1bba7af67765d23ad4a49f1ae51902cc2ed3f8ebee96cfa") {
  return {
    format_version: "1.0", document_id: "test", assets: { scan: { path: assetPath, sha256: assetSha256 } },
    pages: [{ id: "page-1", reference_asset_id: "scan", regions: [{ id: "r-1" }] }],
  };
}

test("project digest binds exact bytes and annotations have a neighboring path", async (t) => {
  const value = await fixture(manifest());
  t.after(() => fs.rm(value.directory, { recursive: true, force: true }));
  const project = loadReviewProject({ env: value.env });
  assert.equal(project.assets.get("scan").path, path.join(value.directory, "scan.png"));
  assert.equal(annotationsPath(value.projectPath), path.join(value.directory, "project.annotations.json"));
  const checked = validateAnnotationPayload(
    {
      project_sha256: project.projectSha256,
      base_annotations_sha256: null,
      reviewer: "reviewer",
      annotations: {
        pages: {
          "page-1": {
            disposition: "accept",
            regions: { "r-1": { canonical_text: "text", disposition: "needs-fix" } },
          },
        },
      },
    },
    project,
    null,
  );
  assert.equal(checked.reviewer, "reviewer");
});

test("undeclared pages and regions cannot be annotated", async (t) => {
  const value = await fixture(manifest());
  t.after(() => fs.rm(value.directory, { recursive: true, force: true }));
  const project = loadReviewProject({ env: value.env });
  assert.throws(() => validateAnnotationPayload({ project_sha256: project.projectSha256, base_annotations_sha256: null, reviewer: "r", annotations: { pages: { nope: {} } } }, project, null), /unknown page/);
  assert.throws(() => validateAnnotationPayload({ project_sha256: project.projectSha256, base_annotations_sha256: null, reviewer: "r", annotations: { pages: { "page-1": { regions: { nope: {} } } } } }, project, null), /unknown region/);
});

test("absolute, traversal, and symlink escape asset paths are rejected", async (t) => {
  for (const assetPath of ["../outside.png", "/etc/passwd"]) {
    const value = await fixture(manifest(assetPath));
    assert.throws(() => loadReviewProject({ env: value.env }), /relative|escapes/);
    await fs.rm(value.directory, { recursive: true, force: true });
  }
  const value = await fixture(manifest("link.png"));
  await fs.symlink("/etc/passwd", path.join(value.directory, "link.png"));
  assert.throws(() => loadReviewProject({ env: value.env }), /outside/);
  await fs.rm(value.directory, { recursive: true, force: true });
});

test("asset digests are mandatory and verified when loading a project", async (t) => {
  const missing = await fixture({ ...manifest(), assets: { scan: { path: "scan.png" } } });
  t.after(() => fs.rm(missing.directory, { recursive: true, force: true }));
  assert.throws(() => loadReviewProject({ env: missing.env }), /sha256 must be a non-empty string/);

  const stale = await fixture(manifest("scan.png", "0".repeat(64)));
  t.after(() => fs.rm(stale.directory, { recursive: true, force: true }));
  assert.throws(() => loadReviewProject({ env: stale.env }), /does not match/);
});

test("only reviewable media types may be declared", async (t) => {
  const value = await fixture({
    ...manifest(),
    assets: { scan: { path: "scan.png", sha256: "59ad1b2fc74287ded1bba7af67765d23ad4a49f1ae51902cc2ed3f8ebee96cfa", media_type: "text/html" } },
  });
  t.after(() => fs.rm(value.directory, { recursive: true, force: true }));
  assert.throws(() => loadReviewProject({ env: value.env }), /not an allowed review media type/);
});

test("native-PDF review rejects free text and only permits a guarded page decision", async (t) => {
  const nativeManifest = {
    ...manifest(), review_mode: "native-pdf-authority",
    pages: [{ id: "page-1", reference_asset_id: "scan", regions: [{ id: "r-1", role: "prose", word_ids: ["word-000"] }], native_pdf_authority: { default_reading_order: ["r-1"], default_excluded_word_ids: [], findings: [{ id: "finding-1" }] }}],
  };
  const value = await fixture(nativeManifest);
  t.after(() => fs.rm(value.directory, { recursive: true, force: true }));
  const project = loadReviewProject({ env: value.env });
  const payload = {
    project_sha256: project.projectSha256, base_annotations_sha256: null, reviewer: "reviewer",
    annotations: { pages: { "page-1": { regions: { "r-1": { canonical_text: "forbidden" } } } } },
  };
  assert.throws(() => validateAnnotationPayload(payload, project, null), /not allowed in native-PDF review/);
});

test("native-PDF review rejects nested free text and accepts only exact fixed choices", async (t) => {
  const nativeManifest = { ...manifest(), review_mode: "native-pdf-authority", pages: [{ id: "page-1", reference_asset_id: "scan", regions: [{ id: "r-1", role: "prose", word_ids: ["word-000"] }], native_pdf_authority: { default_reading_order: ["r-1"], default_excluded_word_ids: [], findings: [{ id: "finding-1" }] }}] };
  const value = await fixture(nativeManifest); t.after(() => fs.rm(value.directory, { recursive: true, force: true }));
  const project = loadReviewProject({ env: value.env });
  const decision = { regions: [{ id: "r-1", role: "prose", word_ids: ["word-000"] }], reading_order: ["r-1"], excluded_word_ids: [], finding_dispositions: { "finding-1": "accepted" }, region_dispositions: { "r-1": "accept" }, acceptance: { layout: true, reading_order: true, semantics: true, object_extraction: true } };
  const payload = { project_sha256: project.projectSha256, base_annotations_sha256: null, reviewer: "r", annotations: { pages: { "page-1": { native_decision: structuredClone(decision) } } } };
  assert.doesNotThrow(() => validateAnnotationPayload(payload, project, null));
  payload.annotations.pages["page-1"].native_decision.regions[0].text = "forbidden";
  assert.throws(() => validateAnnotationPayload(payload, project, null), /unallowed field/);
});
