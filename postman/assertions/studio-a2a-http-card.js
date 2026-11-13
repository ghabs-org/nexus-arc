const card=pm.response.json();
pm.test('Authenticated discovery advertises the implemented bindings',()=>{
 pm.response.to.have.status(200);
 pm.expect(card.supportedInterfaces.map(i=>i.protocolBinding)).to.deep.equal(['JSONRPC','HTTP+JSON']);
 pm.expect(card.capabilities.pushNotifications).to.equal(false);
});
pm.test('Unsupported output modes and image capabilities are not advertised',()=>{
 pm.expect(card.defaultOutputModes).to.deep.equal(['text/plain']);
 pm.expect(card.defaultInputModes.some(mode=>mode.startsWith('image/'))).to.equal(false);
});
