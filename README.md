# Secure Distributed Chat System (IAP Assignment 1)

Course: Internet Architecture and Protocols (CS60008)  
Language: Python 3.9+  
Authors: Group-3 (Datta Ksheeraj, Paramananda Bhaskar, Deetosh Kuila)

---

## Overview

This project implements a secure, distributed, thread-based chat system with authentication, chat rooms, publish–subscribe messaging, Redis-backed global state, TLS encryption, and Dockerized deployment.

The system supports multiple server instances that share global state via Redis and Redis Pub/Sub, allowing clients connected to different servers to communicate seamlessly.

This implementation satisfies all requirements of Assignment 1.

---

## Features

- Thread-based TCP chat server (blocking I/O)
- Secure authentication using bcrypt
- User registration for new accounts
- Duplicate login policy: **Force Logout Existing Session**
- Chat rooms with scoped message broadcasting
- Publish–Subscribe messaging model
- Distributed state via Redis
- TLS-encrypted client-server communication
- Dockerized multi-server deployment
- Graceful disconnect handling
- Thread-safe shared state

---

## Architecture

```
Client (TLS)
    ↓
Chat Server Instance (Thread per client)
    ↓
Redis (Global State + Pub/Sub)
    ↑
Other Server Instances
```

Each server instance is stateless with respect to global session and room data. Redis stores all shared information and handles cross-server message distribution.

---

## Redis Schema

| Key | Type | Description |
|-----|------|-------------|
| `users` | Hash | username → bcrypt password hash |
| `user_sessions` | Hash | username → current room |
| `room:<room>` | Set | members of a room |
| `subscriptions:<publisher>` | Set | subscribers of a publisher |
| `global_chat` | Pub/Sub channel | cross-server messages |
| `control_channel` | Pub/Sub channel | force logout events |

---

## Duplicate Login Policy

This system implements:

**Force Logout Existing Session**

If a user logs in while already active:

1. The server publishes a FORCE_LOGOUT message
2. The old client is notified
3. The old connection is terminated
4. The new login succeeds

This guarantees a single active session per user.

---

## Thread Safety

Thread safety is ensured via:

- `threading.Lock` protecting local socket → user mappings
- Redis atomic operations for global state
- single Redis listener thread per server
- cleanup in `finally` blocks

No shared Python state is accessed without locking.

---

## TLS Security

All communication is encrypted using TLS.

- Self-signed certificate
- Client verifies server certificate
- Hostname verification enforced
- Plaintext connections rejected

The certificate includes:

```
CN = localhost
SAN = localhost, 127.0.0.1
```

Clients must connect using:

```
localhost
```

or

```
127.0.0.1
```

to pass TLS verification.

---

## Authentication

### Login

Use existing credentials to log in. The system includes pre-seeded test accounts (see Default Accounts section).

### Registration

New users can register by selecting the `register` option when prompted. The system will:

1. Create a new account with the provided username and password
2. Hash the password using bcrypt
3. Store the credentials in Redis
4. Automatically log in the user after successful registration

Registration fails if:
- Username already exists
- Server error occurs

After registration, the new account persists across all server instances via Redis.

---

## Commands

After login:

```
/join <room>        Join a room
/leave              Return to lobby
/rooms              List active rooms
/subscribe <user>   Subscribe to a publisher
/unsubscribe <user> Unsubscribe from a publisher
/quit               Exit client
```

Messages are broadcast only to:

- users in the same room
- subscribers (publish–subscribe mode)

---

## Message Ordering

Message ordering is preserved per publisher.

Redis Pub/Sub guarantees ordered delivery per channel, and messages from each publisher are serialized through a single channel before distribution.

---

## Setup Instructions

### 1. Generate TLS Certificate

Run this **once** in the project directory to create a self-signed certificate:

```bash
openssl req -x509 -newkey rsa:4096 \
  -keyout server.key \
  -out server.crt \
  -days 365 -nodes \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

This certificate is used by all server containers.  
Clients verify the server using this certificate.

---

### 2. Start the System

To launch Redis and multiple server instances:

```bash
docker-compose up --build --scale chat-server=3
```

This starts:

- Redis
- 3 chat server containers

You may scale to any number of servers (up to ~50) as long as ports `8000–8050` are available:

```bash
docker-compose up --build --scale chat-server=N
```

If you run:

```bash
docker-compose up --build
```

only **one server instance** will start.

---

### 3. Check Running Containers

Verify container ports:

```bash
docker-compose ps
```

This shows which host ports are mapped to server containers.

Example:

```
chat-server_1 -> 8003
chat-server_2 -> 8004
chat-server_3 -> 8005
```

---

### 4. Connect Clients

From the host machine:

```bash
python3 client.py 8003
python3 client.py 8004
python3 client.py 8005
```

Each port corresponds to a different server container.

You can connect multiple clients to any port.

When prompted, choose:
- **`login`** to use an existing account
- **`register`** to create a new account

Then provide username and password.

---

## Default Accounts

The server seeds test accounts automatically:

| Username | Password |
|----------|----------|
| alice | password123 |
| bob | secret456 |
| charlie | hello789 |

Passwords are stored hashed using bcrypt.

**Note:** New users can register their own accounts using the registration flow. Registered accounts are stored in Redis and available to all server instances.

---

## Testing Scenarios

### User Registration Test

1. Connect a client
2. Choose `register`
3. Enter a new username and password
4. Verify successful registration and automatic login
5. Disconnect and reconnect
6. Login with the newly created credentials

This confirms persistent account creation across server instances.

---

### Duplicate Login Test

1. Login as alice on port 8003
2. Login as alice again on port 8004

Expected:

```
[SYSTEM] FORCED_LOGOUT: Logged in from another location.
```

---

### Cross-Server Room Messaging

1. Client A joins room “test” on server 1
2. Client B joins room “test” on server 2
3. Messages are received by both

This confirms Redis-backed distributed state.

---

### Publish–Subscribe Test

1. Bob subscribes to Alice
2. Alice sends messages
3. Bob receives even if in different rooms

---

## Failure Handling

- Unexpected disconnect → cleanup session
- Socket errors handled gracefully
- Redis crash → server exits safely
- Force logout propagates across servers

---

## Docker Structure

```
Dockerfile
docker-compose.yml
server.py
client.py
server.crt
server.key
```

The system is reproducible via a single command:

```
docker-compose up --build
```

---

## Design Decisions

- Threads instead of asyncio -> required by assignment
- Redis Pub/Sub for scalability
- bcrypt for secure password storage
- TLS certificate pinning for client security
- stateless server design for horizontal scaling

---

## Limitations

- Self-signed TLS (testing only)
- Redis is a single point of failure

---

## Conclusion

This implementation provides a secure, distributed, thread-safe chat system with authentication, rooms, publish–subscribe messaging, and encrypted transport, fully satisfying the assignment requirements.

---