import json
from pathlib import Path
import runpy
import ssl
import shutil
import threading
from http.server import HTTPServer,BaseHTTPRequestHandler
import httpx
import pytest
from dfsha.blocks import sign,verify

@pytest.fixture(scope='module')
def pki(tmp_path_factory):
    root=tmp_path_factory.mktemp('tls')
    (root/'scripts').mkdir()
    shutil.copyfile(Path(__file__).parents[1]/'scripts/setup_secure.py',root/'scripts/setup_secure.py')
    runpy.run_path(str(root/'scripts/setup_secure.py'))
    return root/'runtime'

@pytest.fixture
def tls_server(pki):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.end_headers();self.wfile.write(self.connection.version().encode())
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version=ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(str(pki/'tls/gateway/server.crt'),str(pki/'tls/gateway/server.key'))
    context.load_verify_locations(str(pki/'trust/ca.crt'));context.verify_mode=ssl.CERT_REQUIRED
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'https://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close();thread.join(5)

def test_real_tls13_and_client_certificate(pki,tls_server):
    context=ssl.create_default_context(cafile=str(pki/'trust/ca.crt'))
    context.load_cert_chain(str(pki/'tls/client/server.crt'),str(pki/'tls/client/server.key'))
    assert httpx.get(tls_server,verify=context,timeout=5,trust_env=False).text=='TLSv1.3'

def test_client_without_certificate_rejected(pki,tls_server):
    context=ssl.create_default_context(cafile=str(pki/'trust/ca.crt'))
    with pytest.raises(httpx.HTTPError):httpx.get(tls_server,verify=context,timeout=5,trust_env=False)

def test_untrusted_server_rejected(pki,tls_server):
    context=ssl.create_default_context()
    context.load_cert_chain(str(pki/'tls/client/server.crt'),str(pki/'tls/client/server.key'))
    with pytest.raises(httpx.HTTPError):httpx.get(tls_server,verify=context,timeout=5,trust_env=False)

def test_receipt_keys_are_node_specific(tmp_path,monkeypatch):
    (tmp_path/'dn1.key').write_text('1'*64);(tmp_path/'dn2.key').write_text('2'*64)
    monkeypatch.setenv('RECEIPT_KEYS_DIR',str(tmp_path))
    token=sign('fallback','block-receipt',{'node':'dn1'})
    assert verify('fallback','block-receipt',token)['node']=='dn1'
    import jwt
    forged=jwt.encode({'node':'dn2','iat':1,'exp':9999999999,'iss':'dfsha','aud':'block-receipt'},'1'*64,algorithm='HS256')
    with pytest.raises(jwt.InvalidSignatureError):verify('fallback','block-receipt',forged)
