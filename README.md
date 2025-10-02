# Multi-threaded HTTP Server

## Description

This project implements a multi-threaded HTTP server in Python that can handle multiple client connections simultaneously using a thread pool. The server supports GET and POST methods, serves static HTML pages, and can transfer binary files like .png and .jpg.

---

## Features

- TCP socket server with configurable host and port (default: 127.0.0.1:8080)
- Configurable thread pool size (default: 10 threads)
- Connection queue with max size 100
- HTTP request handling:
   - GET and POST supported
   - Returns 405 Method Not Allowed for other methods
- Serves HTML and binary files from resources/
- Thread-safe request handling using a fixed-size thread pool

---

## Build and Run Instructions

### **1. Clone the repository**
```
git clone <repository-url>
cd <repository-folder>
```

### **2. Ensure Python 3 is installed**
```
python3 --version
```

### **3. Run the server**
```
python3 server.py
```

### **4. Access the server**
Open browser and go to:
http://127.0.0.1:8080/

---

## Binary Transfer Implementation

- Binary files (images like .png, .jpg) are read in binary mode (rb) to ensure no data corruption.
- The server sets the correct Content-Type and Content-Length headers before sending the file.
- This ensures clients (browsers) can properly render or download the files.

---

## Thread Pool Architecture
- A fixed-size thread pool handles incoming client requests.
- Worker threads wait for connections from a queue (queue.Queue(maxsize=100)).
- Each client connection is submitted to the queue and processed by an available worker.
- Benefits:
  - Limits the number of concurrent threads
  - Reduces overhead of creating/destroying threads for each connection
  - Prevents server crashes under high load

---

## Security Measures Implemented
- Supports only GET and POST methods; all others return 405 Method Not Allowed.
- Prevents directory traversal attacks by restricting file access to the resources/ folder.
- Sends proper HTTP headers to prevent caching of sensitive responses (where applicable).
- Handles exceptions in worker threads to prevent server crashes.

---

## Known Limitations
- Does not support HTTPS (HTTP only).
- Limited to static files; dynamic content processing is not implemented.
- No persistent connections (Connection: keep-alive not supported).
- Thread pool size is fixed; no dynamic scaling under extreme load.
- Logging is minimal; advanced logging is not implemented.
