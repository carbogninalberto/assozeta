import multiprocessing

# Server socket
bind = "0.0.0.0:8000"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = 'gthread'
threads = 4
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50
timeout = 120
keepalive = 65

# Process naming
proc_name = 'bakney-api'
pythonpath = '/usr/src/app'

# Logging
errorlog = '-'
loglevel = 'info'
accesslog = '-'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Development
reload = False

# SSL
#keyfile = '/path/to/keyfile'
#certfile = '/path/to/certfile'

# Process management
preload_app = True
worker_tmp_dir = '/dev/shm'  # Use memory for temp files

# Reduce memory leaks
max_requests_jitter = 50     # Randomize worker restarts
worker_abort_on_error = True # Kill workers that use too much memory

# Memory management
worker_memory_limit = '3G'   # Hard memory limit per worker

# Performance tuning
sendfile = True
reuse_port = True

def post_fork(server, worker):
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def pre_fork(server, worker):
    pass

def pre_exec(server):
    server.log.info("Forked child, re-executing.")

def when_ready(server):
    server.log.info("Server is ready. Spawning workers")

def worker_int(worker):
    worker.log.info("worker received INT or QUIT signal")

def worker_abort(worker):
    worker.log.info("worker received SIGABRT signal")