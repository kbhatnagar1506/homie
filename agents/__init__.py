import os

import certifi

# python.org builds on macOS ship without CA certs; set this before uAgents opens any HTTPS connection.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
