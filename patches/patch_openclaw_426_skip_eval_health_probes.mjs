import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const distDir = "/app/dist";

function findServerImpl() {
  for (const name of readdirSync(distDir)) {
    if (!name.startsWith("server.impl-") || !name.endsWith(".js")) {
      continue;
    }
    const path = join(distDir, name);
    const source = readFileSync(path, "utf8");
    if (source.includes("function startGatewayMaintenanceTimers(params)")) {
      return path;
    }
  }
  throw new Error("server.impl dist chunk not found");
}

function findReadOnlyChannelPlugins() {
  for (const name of readdirSync(distDir)) {
    if (!name.startsWith("read-only-") || !name.endsWith(".js")) {
      continue;
    }
    const path = join(distDir, name);
    const source = readFileSync(path, "utf8");
    if (source.includes("function resolveReadOnlyChannelPluginsForConfig(cfg, options = {})")) {
      return path;
    }
  }
  throw new Error("read-only channel plugins dist chunk not found");
}

function replaceOnce(text, oldValue, newValue) {
  if (!text.includes(oldValue)) {
    throw new Error(`patch target not found: ${oldValue.slice(0, 120)}`);
  }
  return text.replace(oldValue, newValue);
}

const dist = findServerImpl();
let source = readFileSync(dist, "utf8");

source = replaceOnce(
  source,
  `\tconst healthInterval = setInterval(() => {
\t\tparams.refreshGatewayHealthSnapshot({ probe: true }).catch((err) => params.logHealth.error(\`refresh failed: \${formatError(err)}\`));
\t}, HEALTH_REFRESH_INTERVAL_MS);
\tparams.refreshGatewayHealthSnapshot({ probe: true }).catch((err) => params.logHealth.error(\`initial refresh failed: \${formatError(err)}\`));`,
  `\tconst shouldRefreshHealth = !(isTruthyEnvValue(process.env.OPENCLAW_SKIP_CHANNELS) || isTruthyEnvValue(process.env.OPENCLAW_SKIP_PROVIDERS));
\tconst healthInterval = setInterval(() => {
\t\tif (!shouldRefreshHealth) return;
\t\tparams.refreshGatewayHealthSnapshot({ probe: true }).catch((err) => params.logHealth.error(\`refresh failed: \${formatError(err)}\`));
\t}, HEALTH_REFRESH_INTERVAL_MS);
\tif (shouldRefreshHealth) params.refreshGatewayHealthSnapshot({ probe: true }).catch((err) => params.logHealth.error(\`initial refresh failed: \${formatError(err)}\`));`,
);

source = replaceOnce(
  source,
  `\t\t\t\trefreshHealthSnapshot({ probe: true }).catch((err) => logHealth.error(\`post-connect health refresh failed: \${formatError(err)}\`));`,
  `\t\t\t\tif (!(isTruthyEnvValue(process.env.OPENCLAW_SKIP_CHANNELS) || isTruthyEnvValue(process.env.OPENCLAW_SKIP_PROVIDERS))) refreshHealthSnapshot({ probe: true }).catch((err) => logHealth.error(\`post-connect health refresh failed: \${formatError(err)}\`));`,
);

source = replaceOnce(
  source,
  `\tawait measureStartup(params.startupTrace, "sidecars.channels", async () => {
\t\tif (!skipChannels) try {
\t\t\tawait prewarmConfiguredPrimaryModel({
\t\t\t\tcfg: params.cfg,
\t\t\t\tlog: params.log
\t\t\t});
\t\t\tawait params.startChannels();
\t\t} catch (err) {
\t\t\tparams.logChannels.error(\`channel startup failed: \${String(err)}\`);
\t\t}
\t\telse params.logChannels.info("skipping channel start (OPENCLAW_SKIP_CHANNELS=1 or OPENCLAW_SKIP_PROVIDERS=1)");
\t});`,
  `\tawait measureStartup(params.startupTrace, "sidecars.channels", async () => {
\t\ttry {
\t\t\tawait prewarmConfiguredPrimaryModel({
\t\t\t\tcfg: params.cfg,
\t\t\t\tlog: params.log
\t\t\t});
\t\t} catch (err) {
\t\t\tparams.logChannels.error(\`model warmup failed: \${String(err)}\`);
\t\t}
\t\tif (!skipChannels) try {
\t\t\tawait params.startChannels();
\t\t} catch (err) {
\t\t\tparams.logChannels.error(\`channel startup failed: \${String(err)}\`);
\t\t}
\t\telse params.logChannels.info("skipping channel start (OPENCLAW_SKIP_CHANNELS=1 or OPENCLAW_SKIP_PROVIDERS=1)");
\t});`,
);

source = replaceOnce(
  source,
  `\t\tstopModelPricingRefresh: !params.minimalTestGateway && !isVitestRuntimeEnv() ? startGatewayModelPricingRefresh({
\t\t\tconfig: params.cfgAtStart,
\t\t\t...params.pluginLookUpTable ? { pluginLookUpTable: params.pluginLookUpTable } : {}
\t\t}) : () => {}`,
  `\t\tstopModelPricingRefresh: !params.minimalTestGateway && !isVitestRuntimeEnv() && !(isTruthyEnvValue(process.env.OPENCLAW_SKIP_CHANNELS) || isTruthyEnvValue(process.env.OPENCLAW_SKIP_PROVIDERS)) ? startGatewayModelPricingRefresh({
\t\t\tconfig: params.cfgAtStart,
\t\t\t...params.pluginLookUpTable ? { pluginLookUpTable: params.pluginLookUpTable } : {}
\t\t}) : () => {}`,
);

writeFileSync(dist, source);
console.log(`patched ${dist}`);

const readOnlyDist = findReadOnlyChannelPlugins();
let readOnlySource = readFileSync(readOnlyDist, "utf8");

readOnlySource = replaceOnce(
  readOnlySource,
  `function resolveReadOnlyChannelPluginsForConfig(cfg, options = {}) {
\tconst env = options.env ?? process.env;
\tconst workspaceDir = resolveReadOnlyWorkspaceDir(cfg, options);`,
  `function resolveReadOnlyChannelPluginsForConfig(cfg, options = {}) {
\tconst env = options.env ?? process.env;
\tconst skipReadOnlyChannelPlugins = (value) => typeof value === "string" && /^(1|true|yes|on)$/i.test(value.trim());
\tif (skipReadOnlyChannelPlugins(env.OPENCLAW_SKIP_CHANNELS) || skipReadOnlyChannelPlugins(env.OPENCLAW_SKIP_PROVIDERS)) return { plugins: [], configuredChannelIds: [], missingConfiguredChannelIds: [] };
\tconst workspaceDir = resolveReadOnlyWorkspaceDir(cfg, options);`,
);

writeFileSync(readOnlyDist, readOnlySource);
console.log(`patched ${readOnlyDist}`);
