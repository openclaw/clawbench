import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const packageRoot = "/app";
const extensionsDir = join(packageRoot, "dist", "extensions");
const stageBase = process.env.OPENCLAW_PLUGIN_STAGE_DIR || "/home/node/.openclaw/plugin-runtime-deps";
const pluginIds = ["browser", "memory-core"];
const extraSpecs = ["tslog@^4.10.2"];

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function pathHash(value) {
  return createHash("sha256").update(value).digest("hex").slice(0, 12);
}

function sanitizePathSegment(value) {
  return value.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
}

function collectSpecs() {
  const specsByName = new Map();
  for (const spec of extraSpecs) {
    const atIndex = spec.lastIndexOf("@");
    specsByName.set(spec.slice(0, atIndex), spec);
  }
  for (const pluginId of pluginIds) {
    const packageJson = readJson(join(extensionsDir, pluginId, "package.json"));
    for (const deps of [packageJson.dependencies, packageJson.optionalDependencies]) {
      if (!deps || typeof deps !== "object") {
        continue;
      }
      for (const [name, version] of Object.entries(deps)) {
        if (typeof version !== "string" || version.trim() === "" || version.startsWith("workspace:")) {
          continue;
        }
        const spec = `${name}@${version}`;
        const previous = specsByName.get(name);
        if (previous && previous !== spec) {
          throw new Error(`conflicting runtime dependency spec for ${name}: ${previous} vs ${spec}`);
        }
        specsByName.set(name, spec);
      }
    }
  }
  return [...specsByName.values()].sort((left, right) => left.localeCompare(right));
}

const packageJson = readJson(join(packageRoot, "package.json"));
const version = sanitizePathSegment(String(packageJson.version || "unknown"));
const installRoot = join(stageBase, `openclaw-${version}-${pathHash(packageRoot)}`);
const specs = collectSpecs();

mkdirSync(installRoot, { recursive: true });
writeFileSync(
  join(installRoot, "package.json"),
  `${JSON.stringify({ name: "openclaw-runtime-deps-install", private: true }, null, 2)}\n`,
);

if (specs.length > 0) {
  execFileSync(
    "npm",
    ["install", "--ignore-scripts", "--legacy-peer-deps", "--package-lock=false", "--save=false", ...specs],
    {
      cwd: installRoot,
      stdio: "inherit",
      env: {
        ...process.env,
        npm_config_cache: join(installRoot, ".openclaw-npm-cache"),
      },
    },
  );
}

writeFileSync(
  join(installRoot, ".openclaw-runtime-deps.json"),
  `${JSON.stringify({ specs }, null, 2)}\n`,
);

console.log(`pre-staged ${specs.length} eval plugin runtime deps in ${installRoot}`);
