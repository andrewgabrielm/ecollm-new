import type { ChatResponse } from "../api/client";
import { Markdown } from "./Markdown";

export function Answer({ result }: { result: ChatResponse }) {
  return (
    <article className="answer">
      <Markdown text={result.answer} />
      <div className="answer-chips">
        <span className="pill">{result.model_id}</span>
        <span className="pill subtle-pill">{result.route}</span>
        <span className="pill subtle-pill">
          {result.reason.replaceAll("_", " ")}
        </span>
        <span className="pill subtle-pill">
          {Math.round(result.timings.total_ms)} ms
        </span>
        {result.restored && (
          <span className="pill subtle-pill">restored locally</span>
        )}
        {Boolean(result.privacy_counts?.length) && (
          <span className="pill subtle-pill">
            masked{" "}
            {result.privacy_counts
              ?.map(
                (item) =>
                  `${item.entity_type.replaceAll("_", " ").toLowerCase()} ×${item.count}`,
              )
              .join(", ")}
          </span>
        )}
      </div>
      <details>
        <summary>Request details</summary>
        <dl className="details">
          <div>
            <dt>Privacy</dt>
            <dd>
              {result.restored
                ? "Masked input · restored locally"
                : "Inspection complete"}
            </dd>
          </div>
          <div>
            <dt>Cache</dt>
            <dd>{result.cache_status}</dd>
          </div>
          <div>
            <dt>Cost</dt>
            <dd>
              {result.estimated_cost_usd == null
                ? "Unavailable"
                : `$${result.estimated_cost_usd.toFixed(4)}`}
            </dd>
          </div>
          <div>
            <dt>Model version</dt>
            <dd>{result.model_version}</dd>
          </div>
          <div>
            <dt>Request</dt>
            <dd>{result.request_id}</dd>
          </div>
          <div>
            <dt>Router</dt>
            <dd>{result.router_type}</dd>
          </div>
          <div>
            <dt>Generation</dt>
            <dd>
              {result.timings.generation_ms == null
                ? "Unavailable"
                : `${Math.round(result.timings.generation_ms)} ms`}
            </dd>
          </div>
          {result.generation_metrics && (
            <>
              <div>
                <dt>Input / output tokens</dt>
                <dd>
                  {result.generation_metrics.input_tokens ?? "Unavailable"} /{" "}
                  {result.generation_metrics.output_tokens ?? "Unavailable"}
                </dd>
              </div>
              <div>
                <dt>Observed output rate</dt>
                <dd>
                  {result.generation_metrics.output_tokens_per_second == null
                    ? "Unavailable"
                    : `${result.generation_metrics.output_tokens_per_second.toFixed(1)} token/s`}
                </dd>
              </div>
            </>
          )}
        </dl>
      </details>
    </article>
  );
}
