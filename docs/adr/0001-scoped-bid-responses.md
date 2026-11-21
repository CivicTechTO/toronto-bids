# ADR 0001: Store scoped bid responses separately from legacy bid rows

Keep existing `bid` rows and their archive IDs unchanged. Store source-labelled bid scopes, response observations, and price variants in separate child tables (`bid_scope`, `bid_response`, `bid_response_price`).

For reviewed scoped reports, write one unpriced participation row to `bid` per proven supplier alongside the detailed child rows, and count participating firms, submissions, and source table cells separately.

## Rationale

- Widening the legacy price-based `bid` key would leave scopes implicit and alter historical bid counts.
- Counting every lot or price column as a separate firm would inflate competition metrics.
- Explicit "No Bid" cells, unidentified text fragments, unscoped outcomes, and cancelled lots stay distinct in the child tables. Scoped prices and outcomes do not apply to the whole procurement, and percentage discounts or unit rates are not converted into dollar totals.
