// Node resolve hook: the browser store (Firebase over https imports) is swapped for an empty in-memory one, so the
// site's promo engine runs under plain Node for checks.
export async function resolve(spec, ctx, next) {
  if (/(^|\/)store\.js$/.test(spec)) return { url: new URL("./stub_store.mjs", import.meta.url).href, shortCircuit: true };
  return next(spec, ctx);
}
