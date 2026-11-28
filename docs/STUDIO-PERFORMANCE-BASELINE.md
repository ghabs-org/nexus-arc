# Nexus Studio Performance Baseline

Recorded: **2 November 2026**
Target: **Nexus Studio Responsive Web App & Desktop Shell**
Environment: **macOS 15 (Apple Silicon), Chrome 156 (Headless / DevTools Protocol), Loopback HTTP 127.0.0.1:5095**
Status: **Phase 1 Validation Accepted (WEB-02)**

---

## 1. Measurement Methodology and Tooling

- **Tooling:** Chrome DevTools Protocol (`performance_start_trace`, `performance.getEntriesByType`), Navigation Timing Level 2, Paint Timing API, PerformanceObserver Layout Shift.
- **Hardware / Host:** Apple Silicon Mac, loopback networking (`127.0.0.1`), CPU throttling 1x, network unthrottled.
- **Data Profiles:**
  - **Cold Shell:** unprimed static bundle fetch across 9 essential assets.
  - **Warm Shell:** cached service worker and memory cache evaluation.
  - **Stress Table:** 100 historical execution runs loaded into `#activity` table.
  - **Stress Graph Canvas:** 20 nodes, 19 edges DAG canvas layout and SVG rendering.
  - **Viewports Tested:** Desktop (`1280x720`), Tablet (`1024x768`), Phone (`390x844`), Small Phone (`360x780`).
- **Lab vs Field Distinction:**
  - All figures below are **lab observations** under controlled reproducible conditions.
  - No Chrome User Experience Report (CrUX) field data exists for local loopback workspaces.
  - Accessibility, SEO, and Best Practices audit scores (previously measured at 100/100) are quality indicators, **not** runtime performance results.

---

## 2. Core Web Vitals and Page Load Metrics (Lab)

| Metric | Measured Value | Acceptance Budget | Status | Notes |
|---|---|---|---|---|
| **Time to First Byte (TTFB)** | 1.9 ms (HTTP), 35 ms (Trace reload) | < 100 ms | **PASS** | Loopback Python ThreadingHTTPServer |
| **First Contentful Paint (FCP)** | 112 ms | < 1,000 ms (Budget: 1,500 ms) | **PASS** | Minimal render-blocking assets, local fonts |
| **Largest Contentful Paint (LCP)** | 85 ms | < 1,200 ms (Budget: 2,500 ms) | **PASS** | Shell header and navigation rail paint |
| **Cumulative Layout Shift (CLS)** | 0.00 (DOM), 0.01 (Trace reload) | < 0.05 (Budget: 0.10) | **PASS** | Zero shift; immediate geometry resize (DT-10) |
| **DOMContentLoaded** | 98.6 ms | < 500 ms | **PASS** | Synchronous scripts parsed and executed |
| **Load Event** | 115.9 ms | < 1,000 ms | **PASS** | Full document and subresource resolution |

---

## 3. Core Static Asset Footprint and Latency

| Asset Path | Content Type | Transferred Bytes | Local Fetch Latency | Render Blocking Impact |
|---|---|---|---|---|
| `/` | `text/html; charset=utf-8` | ~16,400 B | 1.8 ms | Initial document |
| `/studio.css` | `text/css; charset=utf-8` | ~45,000 B | 1.2 ms | Critical stylesheet |
| `/theme.js` | `text/javascript; charset=utf-8` | ~3,000 B | 0.9 ms | Early theme detection (no inline script) |
| `/studio.js` | `text/javascript; charset=utf-8` | ~97,000 B | 2.1 ms | Core framework shell |
| `/operations.js` | `text/javascript; charset=utf-8` | ~98,500 B | 2.0 ms | Operations and evaluation tools |
| `/graph.js` | `text/javascript; charset=utf-8` | ~29,200 B | 1.1 ms | Workflow canvas and undo/redo |
| `/workbench.js` | `text/javascript; charset=utf-8` | ~46,200 B | 1.4 ms | Workbench integration helpers |
| `/features.js` | `text/javascript; charset=utf-8` | ~110,000 B | 2.3 ms | Feature management and feedback |
| `/api/bootstrap` | `application/json; charset=utf-8` | ~420 B | 1.5 ms | CSRF token and workspace capability handshake |

---

## 4. UI View and Tab Navigation Latency

Measured across consecutive view switches with full DOM reflow verification:

| Target View | View Hash | Measured Transition Latency | Budget | Status |
|---|---|---|---|---|
| **Chat** | `#chat` | 21.7 ms | < 100 ms | **PASS** |
| **Workflows** | `#workflows` | 21.4 ms | < 100 ms | **PASS** |
| **Library** | `#library` | 20.8 ms | < 100 ms | **PASS** |
| **Activity** | `#activity` | 21.8 ms | < 100 ms | **PASS** |
| **Evaluations** | `#evaluations` | 21.1 ms | < 100 ms | **PASS** |

---

## 5. Stress Rendering Benchmarks

### 5.1 Large History Table (100 Runs)
- **Data Size:** 100 execution records (22,523 bytes JSON payload).
- **Backend Fetch Latency:** 17 - 19 ms.
- **Client DOM Render Time:** 81.1 ms.
- **Budget:** < 200 ms.
- **Result:** **PASS**. Zero layout jitter or unresponsive thread warnings.

### 5.2 Large Workflow Graph Canvas (20 Nodes, 19 Edges)
- **Data Size:** 20 nodes, 19 edges DAG (4,045 bytes JSON payload).
- **Backend Fetch Latency:** 6 - 7 ms.
- **SVG Canvas Layout & Render Time:** 9.8 ms.
- **Canvas Dimensions Computed:** `750 x 220` with viewBox dynamically clamped.
- **Undo / Redo Initial State:** Correctly disabled (`#graph-undo: disabled`).
- **Budget:** < 150 ms.
- **Result:** **PASS**. Smooth SVG path generation and node placement.

---

## 6. Viewport Adaptability and Layout Bounds

Tested across all four declared viewport sizes with zero horizontal overflow:
- `1280x720` (Desktop): `scrollWidth == clientWidth` (1280px). Zero overflow.
- `1024x768` (Tablet): `scrollWidth == clientWidth` (1024px). Zero overflow.
- `390x844` (Mobile Phone): `scrollWidth == clientWidth`. Zero overflow.
- `360x780` (Compact Mobile): `scrollWidth == clientWidth`. Zero overflow.

---

## 7. Conclusions and Acceptance

1. All lab metrics comfortably clear performance budgets by a factor of 2x to 10x.
2. FCP (112 ms) and LCP (85 ms) confirm that eliminating CDN font dependencies and inline styles was highly effective.
3. CLS (0.00 - 0.01) validates the instant detail-dialog resize token implementation (`DT-10`).
4. Stress rendering for both 100-run tables and 20-node SVG graphs executes well within single-frame / interactive time bounds (< 85 ms).
5. **No performance defects or regressions detected.** Task `WEB-02` accepted.
