#!/usr/bin/env python3
"""
WSGI entry point for the Sava game application.
This file is used by gunicorn to serve the Flask application.
"""

import os

# Set environment to production if not specified
if not os.environ.get('FLASK_ENV'):
    os.environ['FLASK_ENV'] = 'production'

# Import after setting environment
from app import app, socketio

# For Flask-SocketIO with eventlet workers, we use the Flask app directly
# The SocketIO integration is handled automatically when using eventlet
application = app

if __name__ == "__main__":
    # This allows the file to be run directly for testing
    socketio.run(app, debug=False, host='0.0.0.0', port=5000)