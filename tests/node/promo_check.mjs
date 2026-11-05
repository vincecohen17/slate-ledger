// Checks for the site's promo engine under Node (no browser, no board):
//   node tests/node/promo_check.mjs corpus            the plain-words reader against tests/promo_corpus.json
//   node tests/node/promo_check.mjs promo <file.json> one promo as the site would store it: kind, fields, what the
//                                                     Promos page will say, and what a $10 bet settles to with it
// Board-dependent fields (targetLabel: a player or team on today's board) are skipped.
import { register } from "node:module";
import { readFileSync } from "node:fs";
register("./stub_hooks.mjs", import.meta.url);
const P = await import("../../site/js/promo.js");
const [mode, arg] = process.argv.slice(2);
const SKIP = new Set(["targetLabel", "target"]);
if (mode === "corpus") {
  const corpus = JSON.parse(readFileSync(new URL("../promo_corpus.json", import.meta.url)));
  let pass = 0; const fails = [];
  for (const c0 of corpus) {           // entries are [text, expected fields]
    const c = Array.isArray(c0) ? { text: c0[0], expect: c0[1] } : c0;
    const got = P.parsePromo(c.text || c.input), want = c.expect || c.expected || {};
    const bad = Object.entries(want).filter(([k, v]) => !SKIP.has(k) && JSON.stringify(got?.[k]) !== JSON.stringify(v));
    if (bad.length) fails.push({ text: c.text || c.input, bad: bad.map(([k, v]) => `${k}: want ${JSON.stringify(v)} got ${JSON.stringify(got?.[k])}`) });
    else pass++;
  }
  console.log(JSON.stringify({ pass, total: corpus.length, fails }, null, 1));
  process.exit(fails.length ? 1 : 0);
} else if (mode === "promo") {
  const raw = JSON.parse(readFileSync(arg, "utf8"));
  const p = P.normalize(raw), k = P.kindOf(p.kind);
  const problems = [];
  if (!P.KINDS.some((x) => x.key === p.kind)) problems.push(`unknown kind ${p.kind}`);
  for (const [f, lab] of P.missingFields(p)) problems.push(`missing ${f} (${lab})`);
  if (!p.venue) problems.push("missing venue");
  if (!p.id) problems.push("missing id");
  let text = null; try { text = P.describe(p); } catch (e) { problems.push(`describe failed: ${e.message}`); }
  const settle = {};
  for (const [lab, b] of [["+100 $10", { stake: 10, dec: 2, am: 100 }], ["-150 $10", { stake: 10, dec: 1 + 100 / 150, am: -150 }]])
    for (const st of ["won", "lost"]) { try { settle[`${lab} ${st}`] = Math.round(P.settleProfit({ ...b, promoId: p.id }, p, st) * 100) / 100; } catch (e) { problems.push(`settle failed: ${e.message}`); } }
  console.log(JSON.stringify({ ok: !problems.length, kind: p.kind, kindName: k.name, fields: k.fields, describe: text, settle, problems, normalized: p }, null, 1));
  process.exit(problems.length ? 1 : 0);
} else {
  console.log("usage: promo_check.mjs corpus | promo <file.json>");
  process.exit(2);
}
