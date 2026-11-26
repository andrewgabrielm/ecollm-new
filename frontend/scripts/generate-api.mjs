import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";

const root = fileURLToPath(new URL("../../", import.meta.url));
if (process.argv.slice(2).some((value) => value !== "--check")) process.exit(2);
const result = spawnSync(
  `${root}.venv/bin/python`,
  ["scripts/export_openapi.py"],
  { cwd: root, encoding: "utf8", env: { ...process.env, PYTHONPATH: root } },
);
if (result.status !== 0) {
  process.stderr.write(
    "Actual API export failed. Use the root .venv and locked dependencies.\n",
  );
  process.exit(1);
}
const schema = JSON.parse(
  readFileSync(`${root}evaluation/results/openapi.json`, "utf8"),
);
const output =
  "// Generated from the actual FastAPI OpenAPI schema. Run make generate-api.\n" +
  astToString(
    await openapiTS(schema, { alphabetize: true, defaultNonNullable: false }),
  );
const destination = `${root}frontend/src/api/generated.ts`;
if (process.argv.includes("--check")) {
  let current = "";
  try {
    current = readFileSync(destination, "utf8");
  } catch {
    /* Missing output is drift. */
  }
  if (current !== output) {
    process.stderr.write(
      "API types drifted. Run make generate-api and review the change.\n",
    );
    process.exit(1);
  }
  process.stdout.write("API types match the actual backend schema.\n");
} else {
  writeFileSync(destination, output);
  process.stdout.write(
    "Generated frontend/src/api/generated.ts from actual routes.\n",
  );
}
