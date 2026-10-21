# Geographic and ruleset tool contract

Existing authenticated tool-run endpoints and allowlists remain authoritative.
No new authentication, scheduler or API route is introduced.

`research.geographic: true` explicitly enables Open-Meteo personal/non-commercial
location and forecast services. Default is off. `city_to_coordinates` and
`city_to_timezone` take `city` (2–160 characters), optional `country_code` (two
letters), and `limit` (1–5, default 1). Timezone results use IANA zoneinfo and
include current UTC offset and DST. `get_weather` takes the same location fields
and `forecast_days` (1–7, default 1). Results identify the selected city, units,
source URLs, retrieval time and digest. Ambiguous names are narrowed by country;
weather uses the first named result explicitly returned as `location`.

Fixed Open-Meteo HTTPS endpoints use the existing bounded research HTTP client.
Requests carry no user/account credentials. Redirects, non-success status,
invalid provider schemas, coordinates, zones and nonfinite data fail explicitly;
there is no alternate provider fallback. Public free-service terms apply;
commercial customer/key endpoints are not part of this adapter.

`ruleset_lookup` takes `artifact_id`, optional positive `revision`, and a
`parameters` object. The selected owned UTF-8 JSON artifact contains the frozen
SAM `decision_data` shape: name, description, grouping_parameter,
input_parameters and decision_tree_groups. It may be wrapped in `decision_data`
with optional `default_group_key` (default `_default`). Group selection is
case-insensitive and uses an explicitly configured default group if no match.
Required typed parameters are checked; unknown keys and duplicate groups fail.
Results return structured trees plus formatted `decision_logic`, the exact
selected group/default flag and pinned source evidence. Rule logic is text,
never Python, templates, SQL or workflow instructions to execute.

Bounds: 256 KiB ruleset source, 100 groups, 100 trees total, 32 parameters,
16 KiB per logic string and 128 KiB output. Both current and historical artifact
records pass existing owner authorization. Artifact IDs and revisions are cited
on the current run; unauthorized and missing sources share a generic error.
Old queued runs using explicit revisions and old citations remain readable.
Browsing the catalog performs no service request, installation or activation.

Sources: frozen plugin README/config/source at official catalog commit
`7599710a38318a336c47cf605e4774d4add24d8e`; Open-Meteo geocoding and forecast
documentation inspected 21 October 2026. The Nexus source storage/UI differs
from SAM configuration loading while preserving grouped text lookup semantics.
