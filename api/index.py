import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import app
from vercel_python_wsgi import make_wsgi_app

# Vercel expects a handler called 'app' in this file
app = make_wsgi_app(app)
