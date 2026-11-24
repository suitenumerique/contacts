"""Gunicorn configuration for the Contacts backend."""

# pylint: disable=invalid-name
bind = ["0.0.0.0:8000"]
name = "contacts"
python_path = "/app"

# Run
graceful_timeout = 90
timeout = 90
workers = 3
# Gunicorn creates its control socket (for gunicornc) in the working
# directory. The container user cannot write to /app, so gunicorn logs an
# error at each start. We do not use gunicornc.
control_socket_disable = True

# Logging
# Using '-' for the access log file makes gunicorn log accesses to stdout
accesslog = "-"
# Using '-' for the error log file makes gunicorn log errors to stderr
errorlog = "-"
loglevel = "info"
