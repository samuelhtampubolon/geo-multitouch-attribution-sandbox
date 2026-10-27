// Node runner used by tests/test_pages_build.py: recomputes the embedded reference with
// web/engine.js and prints a JSON report of the largest absolute deviation per quantity.
"use strict";
const fs = require("fs");
const path = require("path");
const E = require(path.join(__dirname, "..", "web", "engine.js"));

const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const { records, channels, reference: ref, spends } = data;
const report = {};
const worst = (name, pairs) => { report[name] = Math.max(0, ...pairs.map(([a, b]) => Math.abs(a - b))); };

for (const order of ["1", "2", "3"]) {
  const r = E.markovAttribution(records, channels, +order);
  worst(`markov${order}.base`, [[r.base, ref.markov[order].base]]);
  worst(`markov${order}.effects`, channels.map((c) => [r.effects[c], ref.markov[order].effects[c]]));
  worst(`markov${order}.shares`, channels.map((c) => [r.shares[c], ref.markov[order].shares[c]]));
}
const full = E.runPipeline(records, { order: 1, halfLife: 2 });
worst("visits", channels.map((c) => [full.visits[c], ref.visits[c]]));
worst("path_length", [[full.pathLength, ref.path_length]]);
const exact = E.shapleyExact(records, channels);
const closed = E.shapleyClosedForm(records, channels);
worst("shapley_exact", channels.map((c) => [exact[c], ref.shapley_phi[c]]));
worst("shapley_closed", channels.map((c) => [closed[c], ref.shapley_phi[c]]));
for (const m of Object.keys(ref.rules)) worst(`rules.${m}`, channels.map((c) => [full.models[m][c], ref.rules[m][c]]));
for (const m of Object.keys(ref.divergence)) {
  worst(`divergence.${m}`, [[full.divergence[m].tv, ref.divergence[m].tv], [full.divergence[m].jsd, ref.divergence[m].jsd]]);
}
const attributed = Object.fromEntries(channels.map((c) => [c, full.models.Markov[c] * full.revenue]));
const opt = E.optimizeBudget(attributed, spends, ref.optimizer.budget, ref.optimizer.beta, ref.optimizer.trust);
worst("optimizer.allocation", channels.map((c) => [opt.allocation[c], ref.optimizer.allocation[c]]));
worst("optimizer.scalars", [[opt.lambda, ref.optimizer.lambda], [opt.revenueNow, ref.optimizer.revenue_now], [opt.revenueOpt, ref.optimizer.revenue_opt]]);

const ssrfMismatches = ref.ssrf.filter(([url, blocked]) => (E.ssrfCheck(url).verdict === "blocked") !== blocked).map(([u]) => u);
const sanitizeMismatches = ref.sanitize.filter(([input, out]) => E.sanitizeText(input) !== out).map(([i, o]) => ({ input: i, python: o, js: E.sanitizeText(i) }));
const ci = E.bootstrapMarkov(records, channels, 1, 60, 7);
const ciOk = channels.every((c) => ci[c][0] <= full.models.Markov[c] + 0.01 && full.models.Markov[c] - 0.01 <= ci[c][1]);

process.stdout.write(JSON.stringify({ report, ssrfMismatches, sanitizeMismatches, ciOk }));
