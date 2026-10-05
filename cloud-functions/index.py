from flask import Flask
from backend import app as application
app = Flask(__name__)
app.wsgi_app = application.wsgi_app
