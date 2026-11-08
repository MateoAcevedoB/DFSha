"""Arranque TLS estricto de los servidores internos."""
import os
import ssl
import uvicorn

if __name__ == '__main__':
    role = os.environ['ROLE']
    config=uvicorn.Config('dfsha.api:create_app' if role == 'cn' else 'dfsha.datanode:create_app',
        factory=True, host='0.0.0.0', port=8000,
        ssl_certfile=os.environ['TLS_CERT'], ssl_keyfile=os.environ['TLS_KEY'],
        ssl_ca_certs=os.environ['TLS_CA'], ssl_cert_reqs=ssl.CERT_REQUIRED)
    config.load()
    config.ssl.minimum_version=ssl.TLSVersion.TLSv1_3
    uvicorn.Server(config).run()
