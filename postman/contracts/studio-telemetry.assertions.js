pm.test('Studio telemetry endpoints expose valid token summaries, OTLP traces, and Prometheus metrics', () => {
    pm.response.to.have.status(200);
    const contentType = pm.response.headers.get('Content-Type') || '';

    if (contentType.includes('application/json')) {
        const body = pm.response.json();
        if (body.resourceSpans) {
            // OpenTelemetry OTLP JSON validation
            pm.expect(body.resourceSpans).to.be.an('array');
            pm.expect(body.resourceSpans.length).to.be.above(0);
            const scopeSpans = body.resourceSpans[0].scopeSpans;
            pm.expect(scopeSpans).to.be.an('array');
            const spans = scopeSpans[0].spans;
            pm.expect(spans).to.be.an('array');
            pm.expect(spans.length).to.be.above(0);

            const rootSpan = spans.find(s => s.name && s.name.startsWith('run:'));
            pm.expect(rootSpan).to.be.an('object');
            pm.expect(rootSpan.traceId).to.be.a('string');
            pm.expect(rootSpan.spanId).to.be.a('string');

            const invSpan = spans.find(s => s.name && s.name.startsWith('agent:'));
            if (invSpan) {
                pm.expect(invSpan.parentSpanId).to.eql(rootSpan.spanId);
                const attrs = invSpan.attributes.reduce((acc, a) => {
                    acc[a.key] = a.value;
                    return acc;
                }, {});
                pm.expect(attrs['gen_ai.system']).to.exist;
            }
        } else if (body.run_id) {
            // Token usage summary validation
            pm.expect(body.run_id).to.be.a('string');
            pm.expect(body.total_prompt_tokens).to.be.a('number');
            pm.expect(body.total_completion_tokens).to.be.a('number');
            pm.expect(body.total_tokens).to.be.a('number');
            pm.expect(body.invocations_total).to.be.a('number');
            pm.expect(body.by_model).to.be.an('object');
            pm.expect(body.by_provider).to.be.an('object');
            pm.expect(body.by_agent).to.be.an('object');
        }
    } else {
        // Prometheus metrics validation
        const text = pm.response.text();
        pm.expect(text).to.include('# TYPE nexus_runs_total counter');
        pm.expect(text).to.include('# TYPE nexus_invocations_total counter');
        pm.expect(text).to.include('# TYPE nexus_prompt_tokens_total counter');
    }
});
