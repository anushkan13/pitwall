import type { Usage } from "./api";

// Shown under every AI answer when the token meter is switched on (body.show-tokens).
// The numbers are the provider's own usage counts for every agent run behind the answer.
export const costCell = (u?: { total: { cost_usd: number | null; input_tokens: number; output_tokens: number } } | null) =>
  u ? `$${u.total.cost_usd?.toFixed(5) ?? "?"} · ${u.total.input_tokens.toLocaleString()}/${u.total.output_tokens.toLocaleString()}` : "";

const fmt = (n: number) => n.toLocaleString("en-US");
const usd = (n: number | null) => (n == null ? "no price" : `$${n.toFixed(6)}`);

export function TokenMeter({ usage }: { usage?: Usage | null }) {
  if (!usage) return null;
  const t = usage.total;
  return (
    <details className="token-meter">
      <summary>
        Tokens: {fmt(t.input_tokens)} in ({fmt(t.cached_input_tokens)} cached) · {fmt(t.output_tokens)} out ·{" "}
        {t.requests} request{t.requests === 1 ? "" : "s"} · <strong>{usd(t.cost_usd)}</strong>
      </summary>
      <div style={{ overflowX: "auto" }}><table>
        <thead><tr><th>Agent run</th><th>Requests</th><th>Input</th><th>Cached input</th><th>Output</th><th>Cost</th></tr></thead>
        <tbody>
          {usage.runs.map((r, i) => (
            <tr key={i}>
              <td>{r.agent}{r.completed ? "" : " (failed partway)"}</td><td>{r.requests}</td>
              <td>{fmt(r.input_tokens)}</td><td>{fmt(r.cached_input_tokens)}</td><td>{fmt(r.output_tokens)}</td>
              <td>{usd(r.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {usage.prices_per_mtok && (
        <p>
          {usage.model}: ${usage.prices_per_mtok.input} input, ${usage.prices_per_mtok.cached_input} cached input,
          ${usage.prices_per_mtok.output} output per 1M tokens · {usage.price_source}
        </p>
      )}
    </details>
  );
}
