# Geographic information and rulesets

These native tools close the two formerly planned official catalog equivalents.
They reuse existing Studio tools, workflow nodes, artifact storage, citations and
Nexus permissions. Browsing the integration catalog installs or enables nothing.

## Geographic information

In private server runtime settings, explicitly opt in:

```yaml
research:
  geographic: true
```

No user workspace or running server is changed by installing this code. The
geographic tools remain unavailable until this setting is enabled. Tool grants
remain explicit on each agent or workflow. Tool IDs are workspace-specific and
end in `:city_to_coordinates`, `:city_to_timezone` and `:get_weather`.

- **City coordinates:** `{"city":"Rome","country_code":"IT","limit":1}`.
- **Timezone:** the same arguments return IANA zone, current local time, UTC
  offset and daylight-saving status.
- **Weather:** `{"city":"Rome","country_code":"IT","forecast_days":3}` returns
  current conditions and daily forecast with units and the selected location.

City searches allow 1–5 results. Weather permits 1–7 forecast days and uses the
first matched location, explicitly included in the result. Specify a country
code to narrow ambiguous names. No result is not an alternate-provider trigger.

This adapter explicitly selects Open-Meteo's public personal/non-commercial
service. It does not borrow coding-subscription authentication, send account
credentials, select a different provider, follow redirects or support commercial
customer/key endpoints. Location names leave the server only after the tool is
enabled and invoked. Source URLs, retrieval times and digests accompany results
and become run citations. Responses remain untrusted data.

The underlying request client retains its response/time bounds and private-network
checks. Invalid coordinates, zones, missing weather fields/units and nonfinite
JSON fail visibly. There is no silent provider fallback after service failure.

Provider contracts: [Open-Meteo geocoding](https://open-meteo.com/en/docs/geocoding-api)
and [weather forecast](https://open-meteo.com/en/docs), inspected 21 October 2026.
IANA timezone calculations use the host's installed timezone database.

## Versioned ruleset lookup

Upload a private JSON artifact through Studio's existing file library. The
`ruleset_lookup` tool accepts its ID, optional revision and typed parameters:

```json
{
  "artifact_id": "travel-policy",
  "revision": 1,
  "parameters": {"country": "Italy"}
}
```

Example document, compatible with the frozen SAM decision-data shape:

```json
{
  "decision_data": {
    "name": "travel_policy",
    "description": "Travel review policy",
    "grouping_parameter": "country",
    "input_parameters": [
      {"name": "country", "type": "string", "required": true}
    ],
    "decision_tree_groups": {
      "Italy": {
        "decision_trees": [
          {
            "name": "Travel review",
            "description": "Approval requirements",
            "decision_logic": "Request operator approval before booking travel."
          }
        ]
      }
    }
  }
}
```

A document may contain `decision_data` directly or use the wrapper above.
Supported parameter types are string, boolean, integer and finite number.
Grouping uses a declared string parameter and matches case-insensitively.
Unknown inputs, missing required inputs and duplicate/ambiguous groups fail.

An unmatched group may use an explicitly supplied default group. Name it
`_default`, or set `default_group_key` in the wrapper to another existing group.
Results disclose `group` and `used_default`. If no match/default exists, the
lookup fails; it never invents policy. Optional boolean
`include_topics_in_description` and `include_groups_in_description` expose
`available_topics` and `available_groups` in returned metadata. The default group
is excluded from the latter list.

The result contains structured trees and formatted `decision_logic` for agent
reasoning. Decision logic is never executed. Sources identify exact artifact ID,
revision, filename and digest and are cited on the current run. Select a revision
explicitly when queued work must retain a particular policy. Omitting revision
reads the current version at invocation. Replacing a file does not change old
source citations or explicit-revision lookups.

Current and historical records both pass existing owner checks. Missing and
unauthorized artifacts share a generic source-unavailable error. Private artifact
storage is the default; configured cloud stores retain their selected retrieval
behavior. This tool does not grant public links or cross-user access.

Bounds are 256 KiB source, 100 groups, 100 total trees, 32 input parameters,
16 KiB per logic string and 128 KiB output. Unsupported schema fields fail rather
than being silently discarded. No bulk state migration or optional package is
required. The plugin's original dynamic function naming is represented by one
explicitly selected Nexus tool and versioned artifact ID.

Frozen sources: [geographic plugin](https://github.com/SolaceLabs/solace-agent-mesh-core-plugins/tree/7599710a38318a336c47cf605e4774d4add24d8e/sam-geo-information)
and [ruleset plugin](https://github.com/SolaceLabs/solace-agent-mesh-core-plugins/tree/7599710a38318a336c47cf605e4774d4add24d8e/sam-ruleset-lookup-tool).
No Solace runtime is required.

## Example and checks

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_geography_rulesets.py
```

The consumer runs both tools in an actual Nexus workflow with a synthetic
geographic client. It sends no external HTTP request; public-host DNS validation
still occurs. It performs no booking or other mutation outside its disposable
workspace.

Real HTTP stand-in acceptance uses the documented provider fields:

```sh
postman simulation run postman/simulations/studio-geography.sim.yaml --port 49820 --output ndjson
```

While that owned process is running, in another terminal:

```sh
NEXUS_GEOGRAPHY_SIM_URL=http://127.0.0.1:49820/geography PYTHONPATH=. python -m pytest tests/studio_geography_http_acceptance.py -q -o addopts=''
```

Stop the simulation with Ctrl-C afterward. These tests cover current/forecast
results, source construction, outages, redirects, invalid schemas, empty results
and recovery over real localhost HTTP. They do not establish live Open-Meteo
availability, accuracy or service entitlement. Physical-device and personal HTTPS
VM checks remain part of final operational acceptance.

Local contract: [studio-geography-rulesets.md](../postman/contracts/studio-geography-rulesets.md).
