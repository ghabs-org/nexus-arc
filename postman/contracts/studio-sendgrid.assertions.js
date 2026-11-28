pm.test('Catalog exposes the disabled versioned SendGrid operation without credentials', function () {
  pm.response.to.have.status(200);
  const record = pm.response.json().items.find(item => item.name === 'SendGrid');
  pm.expect(record.data.enabled).to.eql(false);
  pm.expect(record.data.auth.type).to.eql('bearer');
  pm.expect(record.data.auth.token_env).to.eql('SENDGRID_API_KEY');
  pm.expect(record.data.auth.token).to.eql(undefined);
  const spec = record.data.spec;
  pm.expect(spec.info.version).to.eql('2');
  pm.expect(spec.security).to.eql([{sendgrid:[]}]);
  pm.expect(spec.paths['/v3/mail/send'].post.operationId).to.eql('sendMail');
  pm.expect(spec.components.schemas.Personalization.required).to.eql(['to']);
  const body = spec.paths['/v3/mail/send'].post.requestBody.content['application/json'].schema;
  pm.expect(body.properties.template_id.type).to.eql('string');
  pm.expect(body.anyOf[0].required).to.eql(['template_id']);
});
