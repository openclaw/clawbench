import { readFileSync, writeFileSync } from "node:fs";

const dist = "/app/dist/server-methods-b3jaTRE_.js";

function replaceOnce(text, oldValue, newValue) {
  if (!text.includes(oldValue)) {
    throw new Error(`patch target not found: ${oldValue.slice(0, 80)}`);
  }
  return text.replace(oldValue, newValue);
}

let source = readFileSync(dist, "utf8");

source = replaceOnce(
  source,
  "const agentsHandlers = {\n",
  `let agentConfigMutationQueue = Promise.resolve();
async function runAgentConfigMutation(fn) {
\tconst previous = agentConfigMutationQueue;
\tlet release;
\tagentConfigMutationQueue = new Promise((resolve) => {
\t\trelease = resolve;
\t});
\tawait previous.catch(() => {});
\ttry {
\t\treturn await fn();
\t} finally {
\t\trelease();
\t}
}
const agentsHandlers = {
`,
);

source = replaceOnce(
  source,
  `\t\tconst cfg = context.getRuntimeConfig();
\t\tconst rawName = params.name.trim();`,
  `\t\tconst rawName = params.name.trim();`,
);

source = replaceOnce(
  source,
  `\t\tif (findAgentEntryIndex(listAgentEntries(cfg), agentId) >= 0) {
\t\t\trespond(false, void 0, errorShape(ErrorCodes.INVALID_REQUEST, \`agent "\${agentId}" already exists\`));
\t\t\treturn;
\t\t}
\t\tconst workspaceDir = resolveUserPath(params.workspace.trim());`,
  `\t\tconst workspaceDir = resolveUserPath(params.workspace.trim());`,
);

source = replaceOnce(
  source,
  `\t\tlet nextConfig = applyAgentConfig(cfg, {
\t\t\tagentId,
\t\t\tname: safeName,
\t\t\tworkspace: workspaceDir,
\t\t\tmodel,
\t\t\tidentity: {
\t\t\t\tname: safeName,
\t\t\t\t...emoji ? { emoji: sanitizeIdentityLine(emoji) } : {},
\t\t\t\t...avatar ? { avatar: sanitizeIdentityLine(avatar) } : {}
\t\t\t}
\t\t});
\t\tconst agentDir = resolveAgentDir(nextConfig, agentId);
\t\tnextConfig = applyAgentConfig(nextConfig, {
\t\t\tagentId,
\t\t\tagentDir
\t\t});
\t\tawait ensureAgentWorkspace({
\t\t\tdir: workspaceDir,
\t\t\tensureBootstrapFiles: !Boolean(nextConfig.agents?.defaults?.skipBootstrap)
\t\t});
\t\tawait fs$1.mkdir(resolveSessionTranscriptsDirForAgent(agentId), { recursive: true });
\t\tconst persistedIdentity = normalizeIdentityForFile(resolveAgentIdentity(nextConfig, agentId));
\t\tif (persistedIdentity) {
\t\t\tconst identityContent = await buildIdentityMarkdownOrRespondUnsafe({
\t\t\t\trespond,
\t\t\t\tworkspaceDir,
\t\t\t\tidentity: persistedIdentity
\t\t\t});
\t\t\tif (identityContent === null) return;
\t\t\tif (!await writeWorkspaceFileOrRespond({
\t\t\t\trespond,
\t\t\t\tworkspaceDir,
\t\t\t\tname: "IDENTITY.md",
\t\t\t\tcontent: identityContent
\t\t\t})) return;
\t\t}
\t\tawait replaceConfigFile({
\t\t\tnextConfig,
\t\t\tafterWrite: { mode: "auto" }
\t\t});
\t\trespond(true, {
\t\t\tok: true,
\t\t\tagentId,
\t\t\tname: safeName,
\t\t\tworkspace: workspaceDir,
\t\t\tmodel
\t\t}, void 0);`,
  `\t\tconst result = await runAgentConfigMutation(async () => {
\t\t\tconst cfg = context.getRuntimeConfig();
\t\t\tif (findAgentEntryIndex(listAgentEntries(cfg), agentId) >= 0) {
\t\t\t\trespond(false, void 0, errorShape(ErrorCodes.INVALID_REQUEST, \`agent "\${agentId}" already exists\`));
\t\t\t\treturn null;
\t\t\t}
\t\t\tlet nextConfig = applyAgentConfig(cfg, {
\t\t\t\tagentId,
\t\t\t\tname: safeName,
\t\t\t\tworkspace: workspaceDir,
\t\t\t\tmodel,
\t\t\t\tidentity: {
\t\t\t\t\tname: safeName,
\t\t\t\t\t...emoji ? { emoji: sanitizeIdentityLine(emoji) } : {},
\t\t\t\t\t...avatar ? { avatar: sanitizeIdentityLine(avatar) } : {}
\t\t\t\t}
\t\t\t});
\t\t\tconst agentDir = resolveAgentDir(nextConfig, agentId);
\t\t\tnextConfig = applyAgentConfig(nextConfig, {
\t\t\t\tagentId,
\t\t\t\tagentDir
\t\t\t});
\t\t\tawait ensureAgentWorkspace({
\t\t\t\tdir: workspaceDir,
\t\t\t\tensureBootstrapFiles: !Boolean(nextConfig.agents?.defaults?.skipBootstrap)
\t\t\t});
\t\t\tawait fs$1.mkdir(resolveSessionTranscriptsDirForAgent(agentId), { recursive: true });
\t\t\tconst persistedIdentity = normalizeIdentityForFile(resolveAgentIdentity(nextConfig, agentId));
\t\t\tif (persistedIdentity) {
\t\t\t\tconst identityContent = await buildIdentityMarkdownOrRespondUnsafe({
\t\t\t\t\trespond,
\t\t\t\t\tworkspaceDir,
\t\t\t\t\tidentity: persistedIdentity
\t\t\t\t});
\t\t\t\tif (identityContent === null) return null;
\t\t\t\tif (!await writeWorkspaceFileOrRespond({
\t\t\t\t\trespond,
\t\t\t\t\tworkspaceDir,
\t\t\t\t\tname: "IDENTITY.md",
\t\t\t\t\tcontent: identityContent
\t\t\t\t})) return null;
\t\t\t}
\t\t\tawait replaceConfigFile({
\t\t\t\tnextConfig,
\t\t\t\tafterWrite: { mode: "auto" }
\t\t\t});
\t\t\treturn true;
\t\t});
\t\tif (!result) return;
\t\trespond(true, {
\t\t\tok: true,
\t\t\tagentId,
\t\t\tname: safeName,
\t\t\tworkspace: workspaceDir,
\t\t\tmodel
\t\t}, void 0);`,
);

for (const marker of [
  `\t\t\tawait replaceConfigFile({
\t\t\t\tnextConfig,
\t\t\t\tafterWrite: { mode: "auto" }
\t\t\t});`,
  `\t\tawait replaceConfigFile({
\t\t\tnextConfig,
\t\t\tafterWrite: { mode: "auto" }
\t\t});`,
  `\t\tawait replaceConfigFile({
\t\t\tnextConfig: result.config,
\t\t\tafterWrite: { mode: "auto" }
\t\t});`,
]) {
  source = replaceOnce(
    source,
    marker,
    marker.replace(`{ mode: "auto" }`, `{ mode: "none", reason: "clawbench-agent-lifecycle" }`),
  );
}

writeFileSync(dist, source);
console.log(`patched ${dist}`);
