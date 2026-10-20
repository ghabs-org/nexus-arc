# Bounded enterprise pages

Bedrock knowledge retrieval accepts an optional `next_token` (at most 2048
characters) and returns the provider's next token without automatically fetching
another page. Query and result limits remain scoped to the selected resource.

MongoDB retains its existing bounded query format. Optional `paginate: true`
uses stable ObjectId order and returns `next_after`/`truncated`. Supply `after_id`
for the next page. The selected collection and original filters remain enforced;
page size is 1–100. Pagination requires ObjectId documents and a projection that
retains `_id`; unsupported data returns an explicit error. No mutation retry.
