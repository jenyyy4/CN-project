"""
Multi-threaded HTTP Server (server.py)

Features implemented:
- TCP socket server binding to host/port (defaults: 127.0.0.1:8080)
- Configurable thread-pool size (default 10)
- Connection queue with maxsize (default 100)
- GET and POST support; 405 for other methods
- Serves HTML from resources/ (index.html for /)
- Binary file download for .png, .jpg, .jpeg, .txt as application/octet-stream
- POST /upload accepting application/json and writing uploads/upload_*.json
- Path traversal protection and canonicalization
- Host header validation
- Keep-alive support, timeout=30 seconds, max 100 requests per connection
- Proper HTTP response formatting and error codes
- Logging with timestamps and thread names

Usage: ./server.py [port] [host] [max_threads]
Test: ./server.py 8000 0.0.0.0 20
"""

import sys
import socket
import threading
import queue
import os
import time
import json
import random
import string
from datetime import datetime
from email.utils import formatdate

# ---------------------- Configuration ----------------------
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8080
DEFAULT_THREAD_POOL = 10
LISTEN_BACKLOG = 50
CONN_QUEUE_MAX = 200
MAX_REQUEST_SIZE = 8192
RESOURCES_DIR = os.path.join(os.path.dirname(__file__), 'resources')
UPLOADS_DIR = os.path.join(RESOURCES_DIR, 'uploads')
SERVER_NAME = 'Multi-threaded HTTP Server'
PERSISTENT_CONN_TIMEOUT = 30
PERSISTENT_CONN_MAX_REQUESTS = 100

BINARY_EXTS = {'.png', '.jpg', '.jpeg', '.txt'}

# ---------------------- Utilities ----------------------
def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    thread = threading.current_thread().name
    print(f'[{ts}] [{thread}] {msg}')


def http_date():
    return formatdate(timeval=None, usegmt=True)


def random_id(n=4):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))

# ---------------------- HTTP helper responses ----------------------
def make_response(status_code, reason, headers=None, body=b''):
    headers = headers or {}
    status_line = f'HTTP/1.1 {status_code} {reason}\r\n'
    header_lines = ''.join(f'{k}: {v}\r\n' for k, v in headers.items())
    resp = status_line + header_lines + '\r\n'
    if isinstance(body, str):
        body = body.encode('utf-8')
    return resp.encode('utf-8') + body

# ---------------------- Path validation ----------------------
def is_safe_path(request_path):
    if '..' in request_path or request_path.startswith('//') or '\\' in request_path:
        return False
    if request_path.startswith('/'):
        return True
    if ':' in request_path:
        return False
    return True

def resolve_resource_path(url_path):
    if url_path == '/':
        url_path = '/index.html'
    if '?' in url_path:
        url_path = url_path.split('?', 1)[0]
    if not is_safe_path(url_path):
        raise PermissionError('Path contains forbidden segments')
    rel_path = url_path.lstrip('/')
    if rel_path == '':
        rel_path = 'index.html'
    full = os.path.realpath(os.path.join(RESOURCES_DIR, rel_path))
    if not full.startswith(os.path.realpath(RESOURCES_DIR) + os.sep):
        raise PermissionError('Path traversal detected')
    return full

# ---------------------- Request Parsing ----------------------
def recv_line(conn_file):
    line = conn_file.readline(MAX_REQUEST_SIZE)
    if not line:
        return None
    return line.rstrip('\r\n')

def parse_request(conn_file):
    request_line = recv_line(conn_file)
    if request_line is None:
        return None
    parts = request_line.split()
    if len(parts) != 3:
        raise ValueError('Malformed request line')
    method, path, version = parts
    headers = {}
    while True:
        line = recv_line(conn_file)
        if line is None:
            break
        if line == '':
            break
        if ':' not in line:
            raise ValueError('Malformed header line')
        k, v = line.split(':', 1)
        headers[k.strip().lower()] = v.strip()
    return {'method': method, 'path': path, 'version': version, 'headers': headers}

# ---------------------- Worker / Thread Pool ----------------------
class ThreadPoolServer:
    def __init__(self, host, port, max_threads=DEFAULT_THREAD_POOL):
        self.host = host
        self.port = port
        self.max_threads = max_threads
        self.shutdown_event = threading.Event()
        self.conn_queue = queue.Queue(maxsize=CONN_QUEUE_MAX)
        self.threads = []
        self.active_lock = threading.Lock()
        os.makedirs(UPLOADS_DIR, exist_ok=True)

    def start(self):
        self._start_workers()
        self._start_acceptor()

    def _start_workers(self):
        for i in range(self.max_threads):
            t = threading.Thread(target=self._worker_loop, name=f'Thread-{i+1}', daemon=True)
            t.start()
            self.threads.append(t)
        log(f'HTTP Server started on http://{self.host}:{self.port}')
        log(f'Thread pool size: {self.max_threads}')
        log(f"Serving files from '{RESOURCES_DIR}'")
        log('Press Ctrl+C to stop the server')

    def _start_acceptor(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((self.host, self.port))
            server_sock.listen(LISTEN_BACKLOG)
            server_sock.settimeout(1.0)
            try:
                while not self.shutdown_event.is_set():
                    try:
                        client_sock, addr = server_sock.accept()
                    except socket.timeout:
                        continue
                    log(f'Connection from {addr[0]}:{addr[1]}')
                    try:
                        self.conn_queue.put((client_sock, addr), block=False)
                    except queue.Full:
                        log('Warning: Thread pool saturated, sending 503')
                        try:
                            resp = make_response(503, 'Service Unavailable', headers={
                                'Date': http_date(),
                                'Server': SERVER_NAME,
                                'Content-Length': '0',
                                'Retry-After': '5'
                            })
                            client_sock.sendall(resp)
                        except Exception:
                            pass
                        finally:
                            client_sock.close()
            except KeyboardInterrupt:
                log('Shutdown requested by user')
                self.shutdown_event.set()
            finally:
                log('Closing server...')

    def _worker_loop(self):
        while not self.shutdown_event.is_set():
            try:
                client_sock, addr = self.conn_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.handle_client(client_sock, addr)
            except Exception as e:
                log(f'Exception while handling client: {e}')
            finally:
                try:
                    client_sock.close()
                except Exception:
                    pass
                self.conn_queue.task_done()

    # ---------------------- Main request handling ----------------------
    def handle_client(self, client_sock, addr):
        client_ip, client_port = addr
        thread_name = threading.current_thread().name
        log(f'Connection from {client_ip}:{client_port}')
        client_sock.settimeout(PERSISTENT_CONN_TIMEOUT + 5)
        conn_file = client_sock.makefile('rwb', buffering=0)

        requests_handled = 0
        keep_alive = True
        last_activity = time.time()

        while keep_alive and requests_handled < PERSISTENT_CONN_MAX_REQUESTS:
            if time.time() - last_activity > PERSISTENT_CONN_TIMEOUT:
                log('Connection idle timeout reached, closing')
                break
            try:
                conn_text = client_sock.makefile('r', encoding='iso-8859-1', newline='\r\n')
                req = parse_request(conn_text)
                if req is None:
                    break
            except socket.timeout:
                break
            except Exception as e:
                log(f'Request parse error: {e}')
                err = make_response(400, 'Bad Request', headers={
                    'Date': http_date(), 'Server': SERVER_NAME, 'Content-Length': '0', 'Connection': 'close'
                })
                try:
                    client_sock.sendall(err)
                except Exception:
                    pass
                break

            requests_handled += 1
            last_activity = time.time()

            method = req['method']
            path = req['path']
            version = req['version']
            headers = req['headers']

            log(f"Request: {method} {path} {version}")

            host_hdr = headers.get('host')
            if host_hdr is None:
                log('Host header missing')
                resp = make_response(400, 'Bad Request', headers={'Date': http_date(), 'Server': SERVER_NAME, 'Content-Length': '0', 'Connection': 'close'})
                client_sock.sendall(resp)
                break
            valid_host = f'{self.host}:{self.port}'
            if host_hdr != valid_host and host_hdr not in {f'localhost:{self.port}', f'127.0.0.1:{self.port}'}:
                log(f'Host validation failed: {host_hdr}')
                resp = make_response(403, 'Forbidden', headers={'Date': http_date(), 'Server': SERVER_NAME, 'Content-Length': '0', 'Connection': 'close'})
                client_sock.sendall(resp)
                break
            else:
                log(f'Host validation: {host_hdr} ✓')

            conn_hdr = headers.get('connection')
            if version == 'HTTP/1.0':
                keep_alive = (conn_hdr and conn_hdr.lower() == 'keep-alive')
            else:
                keep_alive = not (conn_hdr and conn_hdr.lower() == 'close')

            try:
                if method == 'GET':
                    self.handle_get(client_sock, path, keep_alive)
                elif method == 'POST':
                    self.handle_post(client_sock, path, headers, conn_text, keep_alive)
                else:
                    resp = make_response(405, 'Method Not Allowed', headers={'Date': http_date(), 'Server': SERVER_NAME, 'Content-Length': '0', 'Connection': 'close'})
                    client_sock.sendall(resp)
                    break
            except PermissionError as pe:
                log(f'Security violation: {pe}')
                resp = make_response(403, 'Forbidden', headers={'Date': http_date(), 'Server': SERVER_NAME, 'Content-Length': '0', 'Connection': 'close'})
                client_sock.sendall(resp)
                break
            except FileNotFoundError:
                resp_body = b'404 Not Found'
                headers_out = {'Date': http_date(), 'Server': SERVER_NAME, 'Content-Length': str(len(resp_body)), 'Connection': 'close'}
                resp = make_response(404, 'Not Found', headers=headers_out, body=resp_body)
                client_sock.sendall(resp)
                break
            except ValueError as ve:
                log(f'Bad request: {ve}')
                resp = make_response(400, 'Bad Request', headers={'Date': http_date(), 'Server': SERVER_NAME, 'Content-Length': '0', 'Connection': 'close'})
                client_sock.sendall(resp)
                break
            except Exception as e:
                log(f'Internal server error: {e}')
                resp = make_response(500, 'Internal Server Error', headers={'Date': http_date(), 'Server': SERVER_NAME, 'Content-Length': '0', 'Connection': 'close'})
                client_sock.sendall(resp)
                break

            if keep_alive:
                last_activity = time.time()
                continue
            else:
                break

        try:
            client_sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

    # ---------------------- Handlers ----------------------
    def handle_get(self, client_sock, path, keep_alive):
        file_path = resolve_resource_path(path)
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            raise FileNotFoundError()

        _, ext = os.path.splitext(file_path)
        ext = ext.lower()
        if ext == '.html':
            with open(file_path, 'rb') as f:
                body = f.read()
            headers = {
                'Date': http_date(),
                'Server': SERVER_NAME,
                'Content-Type': 'text/html; charset=utf-8',
                'Content-Length': str(len(body)),
                'Connection': 'keep-alive' if keep_alive else 'close',
                'Keep-Alive': f'timeout={PERSISTENT_CONN_TIMEOUT}, max={PERSISTENT_CONN_MAX_REQUESTS}'
            }
            resp = make_response(200, 'OK', headers=headers, body=body)
            client_sock.sendall(resp)
            log(f'Sending HTML file: {os.path.basename(file_path)} ({len(body)} bytes)')
            return
        elif ext in BINARY_EXTS:
            fname = os.path.basename(file_path)
            fsize = os.path.getsize(file_path)
            headers = {
                'Date': http_date(),
                'Server': SERVER_NAME,
                'Content-Type': 'application/octet-stream',
                'Content-Length': str(fsize),
                'Content-Disposition': f'attachment; filename="{fname}"',
                'Connection': 'keep-alive' if keep_alive else 'close',
                'Keep-Alive': f'timeout={PERSISTENT_CONN_TIMEOUT}, max={PERSISTENT_CONN_MAX_REQUESTS}'
            }
            header_bytes = make_response(200, 'OK', headers=headers, body=b'')
            client_sock.sendall(header_bytes)
            log(f'Sending binary file: {fname} ({fsize} bytes)')
            with open(file_path, 'rb') as rf:
                while True:
                    chunk = rf.read(8192)
                    if not chunk:
                        break
                    client_sock.sendall(chunk)
            log(f'Response: 200 OK ({fsize} bytes transferred)')
            return
        else:
            raise PermissionError('Unsupported media type')

    def handle_post(self, client_sock, path, headers, keep_alive):
        if path != '/upload':
            raise FileNotFoundError()
        content_type = headers.get('content-type')
        if content_type is None:
            raise ValueError('Missing Content-Type')
        if 'application/json' not in content_type:
            resp = make_response(415, 'Unsupported Media Type', headers={
                'Date': http_date(),
                'Server': SERVER_NAME,
                'Content-Length': '0',
                'Connection': 'close'
            })
            client_sock.sendall(resp)
            return

        cl = headers.get('content-length')
        if cl is None:
            raise ValueError('Missing Content-Length')
        try:
            length = int(cl)
        except Exception:
            raise ValueError('Invalid Content-Length')

        if length > 10 * 1024 * 1024:
            resp = make_response(413, 'Payload Too Large', headers={
                'Date': http_date(),
                'Server': SERVER_NAME,
                'Content-Length': '0',
                'Connection': 'close'
            })
            client_sock.sendall(resp)
            return

        body_bytes = b''
        remaining = length
        while remaining > 0:
            chunk = client_sock.recv(min(8192, remaining))
            if not chunk:
                break
            body_bytes += chunk
            remaining -= len(chunk)

        try:
            body_text = body_bytes.decode('utf-8')
            data = json.loads(body_text)
        except Exception as e:
            log(f'JSON parse error: {e}')
            resp = make_response(400, 'Bad Request', headers={
                'Date': http_date(),
                'Server': SERVER_NAME,
                'Content-Length': '0',
                'Connection': 'close'
            })
            client_sock.sendall(resp)
            return

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        fname = f'upload_{ts}_{random_id(6)}.json'
        relpath = os.path.join('uploads', fname)
        fullpath = os.path.join(UPLOADS_DIR, fname)
        with open(fullpath, 'w', encoding='utf-8') as wf:
            json.dump(data, wf, indent=2)

        resp_body = json.dumps({
            'status': 'success',
            'message': 'File created successfully',
            'filepath': '/' + relpath
        }).encode('utf-8')
        headers_out = {
            'Date': http_date(),
            'Server': SERVER_NAME,
            'Content-Type': 'application/json',
            'Content-Length': str(len(resp_body)),
            'Connection': 'keep-alive' if keep_alive else 'close'
        }
        resp = make_response(201, 'Created', headers=headers_out, body=resp_body)
        client_sock.sendall(resp)
        log(f'Created upload file: {relpath} ({len(resp_body)} bytes)')

# ---------------------- Main ----------------------
def main():
    port = DEFAULT_PORT
    host = DEFAULT_HOST
    max_threads = DEFAULT_THREAD_POOL
    argv = sys.argv[1:]
    if len(argv) >= 1:
        try:
            port = int(argv[0])
        except Exception:
            print('Invalid port; using default 8080')
    if len(argv) >= 2:
        host = argv[1]
    if len(argv) >= 3:
        try:
            max_threads = int(argv[2])
        except Exception:
            print('Invalid thread count; using default')

    os.makedirs(RESOURCES_DIR, exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)

    server = ThreadPoolServer(host, port, max_threads)
    server.start()

if __name__ == '__main__':
    main()
