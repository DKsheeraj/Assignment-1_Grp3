import socket
import threading
import ssl
import bcrypt
import redis
import json
import os
import logging
import time

# --- LOGGING SETUP---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
HOST = '0.0.0.0'
PORT = int(os.environ.get("PORT", 8000))
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))

# --- REDIS CONNECTION ---
try:
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    r.ping()
    logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
except redis.ConnectionError:
    logger.error("Could not connect to Redis. Exiting.")
    exit(1)

# --- LOCAL STATE ---
local_clients = {}       # {socket: username}
local_clients_lock = threading.Lock()

# --- DB INITIALIZATION ---
def init_db():
    """Seeds the Redis database with users if they don't exist."""
    # Only seed if the 'users' hash doesn't exist to avoid overwriting
    if not r.exists("users"):
        users = {
            "alice": bcrypt.hashpw("password123".encode(), bcrypt.gensalt()).decode(),
            "bob": bcrypt.hashpw("secret456".encode(), bcrypt.gensalt()).decode(),
            "charlie": bcrypt.hashpw("hello789".encode(), bcrypt.gensalt()).decode()
        }
        r.hmset("users", users)
        logger.info("Seeded user database into Redis.")

def handle_redis_messages():
    """Listens for global messages and control commands."""
    pubsub = r.pubsub()
    pubsub.subscribe('global_chat', 'control_channel')
    
    for message in pubsub.listen():
        if message['type'] == 'message':
            try:
                channel = message['channel']
                data = json.loads(message['data'])
                
                if channel == "control_channel":
                    handle_control_message(data)
                elif channel == "global_chat":
                    handle_chat_message(data)
            except json.JSONDecodeError:
                logger.error("Failed to decode Redis message")

def handle_control_message(data):
    """Handles Force Logout"""
    if data.get('type') == "FORCE_LOGOUT":
        target_user = data.get('target')
        target_socket = None
        
        with local_clients_lock:
            for sock, user in local_clients.items():
                if user == target_user:
                    target_socket = sock
                    break
        
        if target_socket:
            try:
                target_socket.send("FORCED_LOGOUT: Logged in from another location.".encode())
                target_socket.close() # This triggers exception in handle_client -> cleanup
                logger.info(f"Force logged out local user: {target_user}")
            except Exception as e:
                logger.error(f"Error closing socket for {target_user}: {e}")

def handle_chat_message(data):
    """Relays global messages to local clients."""
    msg_type = data.get('type')
    sender = data.get('sender')
    content = data.get('content')
    room = data.get('room')

    with local_clients_lock:
        active_sockets = list(local_clients.items())

    for sock, username in active_sockets:
        if username == sender: continue

        try:
            should_send = False
            # Room Broadcast
            if msg_type == "BROADCAST":
                # Check user's room in Redis to ensure consistency
                user_room = r.hget("user_sessions", username)
                if user_room == room:
                    should_send = True
            
            # Pub/Sub
            elif msg_type == "PUBSUB":
                if r.sismember(f"subscriptions:{sender}", username):
                    should_send = True

            if should_send:
                sock.send(content.encode())
        except Exception:
            pass # Socket might be closed, cleanup handles this

def handle_registration(client_socket, username, password):
    """
    Registers a new user in Redis.
    """
    try:
        # Check if username already exists
        if r.hexists("users", username):
            client_socket.send("REGISTER_FAILED: Username already exists.".encode())
            logger.info(f"Registration failed for {username} - already exists")
            return False
        
        # Hash password and store in Redis
        hashed_password = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        r.hset("users", username, hashed_password)
        
        client_socket.send("REGISTER_SUCCESS".encode())
        logger.info(f"New user registered: {username}")
        return True
        
    except Exception as e:
        logger.error(f"Registration error: {e}")
        client_socket.send("REGISTER_FAILED: Server error.".encode())
        return False

def handle_authentication(client_socket):
    """
    Authenticates against Redis and handles Force Logout.
    Also supports user registration.
    """
    try:
        data = client_socket.recv(1024).decode('utf-8').strip().split()
        
        # Handle REGISTER command
        if len(data) == 3 and data[0] == "REGISTER":
            username = data[1]
            password = data[2]
            
            if handle_registration(client_socket, username, password):
                # After successful registration, wait for login
                return handle_authentication(client_socket)
            else:
                return None
        
        # Handle LOGIN command
        elif len(data) == 3 and data[0] == "LOGIN":
            username = data[1]
            password = data[2]

            # 1. Fetch hash from Redis
            stored_hash = r.hget("users", username)
            
            if stored_hash and bcrypt.checkpw(password.encode(), stored_hash.encode()):
                
                # 2. Check for existing session (Duplicate Login Policy)
                if r.hexists("user_sessions", username):
                    logger.info(f"Duplicate login for {username}. Forcing logout.")
                    r.publish("control_channel", json.dumps({
                        "type": "FORCE_LOGOUT", 
                        "target": username
                    }))
                    # Small sleep to allow the other server to close the socket
                    time.sleep(0.5)

                # 3. Register new session
                r.hset("user_sessions", username, "lobby")
                client_socket.send("AUTH_SUCCESS".encode())
                logger.info(f"User {username} logged in.")
                return username
            
            client_socket.send("AUTH_FAILED: Invalid credentials.".encode())
    except Exception as e:
        logger.error(f"Auth error: {e}")
    return None

def handle_client(client_socket, addr):
    username = handle_authentication(client_socket)
    if not username:
        client_socket.close()
        return

    with local_clients_lock:
        local_clients[client_socket] = username

    # Initial join to lobby
    r.sadd("room:lobby", username)
    logger.info(f"{username} joined the lobby") # log for initial join
    publish_message("BROADCAST", username, f"{username} joined the lobby", room="lobby")

    try:
        while True:
            data = client_socket.recv(1024).decode('utf-8')
            if not data: break
            
            # --- COMMANDS ---
            if data.startswith("/join "):
                new_room = data.split(" ")[1]
                switch_room(client_socket, username, new_room)
            
            elif data.startswith("/leave"):
                switch_room(client_socket, username, "lobby")

            elif data == "/rooms":
                keys = r.keys("room:*")
                room_names = [k.split(":")[1] for k in keys]
                logger.info(f"{username} requested active rooms list")
                client_socket.send(f"[SYSTEM] Active Rooms: {', '.join(room_names)}".encode())

            elif data.startswith("/subscribe "):
                target = data.split(" ")[1]
                r.sadd(f"subscriptions:{target}", username)
                logger.info(f"{username} subscribed to {target}")
                client_socket.send(f"[SYSTEM] Subscribed to {target}".encode())
            
            elif data.startswith("/unsubscribe "):
                target = data.split(" ")[1]
                r.srem(f"subscriptions:{target}", username)
                logger.info(f"{username} unsubscribed from {target}")
                client_socket.send(f"[SYSTEM] Unsubscribed from {target}".encode())

            # --- MESSAGING ---
            else:
                current_room = r.hget("user_sessions", username)
                if current_room:
                    logger.info(f"[{current_room}] {username}: {data}")
                    publish_message("BROADCAST", username, f"[{current_room}] {username}: {data}", room=current_room)
                    publish_message("PUBSUB", username, f"[PUB-SUB] {username}: {data}")

    except (ConnectionResetError, ConnectionAbortedError):
        logger.info(f"Client {username} disconnected unexpectedly.")
    finally:
        cleanup_client(client_socket, username)

def switch_room(client_socket, username, new_room):
    old_room = r.hget("user_sessions", username)
    
    # Update Redis
    if old_room:
        r.srem(f"room:{old_room}", username) # Correct use of srem
        publish_message("BROADCAST", username, f"{username} left {old_room}", room=old_room)
    
    r.sadd(f"room:{new_room}", username)
    r.hset("user_sessions", username, new_room)

    logger.info(f"{username} switched room from {old_room} to {new_room}")

    client_socket.send(f"[SYSTEM] Joined room: {new_room}".encode())
    publish_message("BROADCAST", username, f"{username} joined {new_room}", room=new_room)

def publish_message(msg_type, sender, content, room=None):
    message = {"type": msg_type, "sender": sender, "content": content, "room": room}
    r.publish("global_chat", json.dumps(message))

def cleanup_client(client_socket, username):
    """Removes user from Redis and Local state."""
    with local_clients_lock:
        if client_socket in local_clients:
            local_clients.pop(client_socket)

    # Redis Cleanup
    if r.hexists("user_sessions", username):
        current_room = r.hget("user_sessions", username)
        r.srem(f"room:{current_room}", username) # Remove from set
        r.hdel("user_sessions", username)        # Remove session
        
        publish_message("BROADCAST", username, f"{username} left the chat", room=current_room)
        logger.info(f"Cleaned up session for {username}")

    try:
        client_socket.close()
    except:
        pass

def start_server():
    init_db() # Seed users
    
    # Start Redis Listener
    threading.Thread(target=handle_redis_messages, daemon=True).start()

    # SSL Setup
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile="server.crt", keyfile="server.key")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen()

    logger.info(f"Server listening on {HOST}:{PORT} (SSL Enabled)")

    while True:
        try:
            client, addr = server_socket.accept()
            secure_sock = context.wrap_socket(client, server_side=True)
            threading.Thread(target=handle_client, args=(secure_sock, addr)).start()
        except Exception as e:
            logger.error(f"Accept error: {e}")

if __name__ == "__main__":
    start_server()
