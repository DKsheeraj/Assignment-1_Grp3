import socket
import threading
import bcrypt

HOST = '127.0.0.1'
PORT = 8000


clients = {}       # {socket: username}
client_rooms = {}  # {socket: room_name}
rooms = {"lobby": set()} # {room_name: {sockets}}
clients_lock = threading.Lock()
# Mock database: {username: hashed_password}
user_db = {
    "alice": bcrypt.hashpw("password123".encode(), bcrypt.gensalt()),
    "bob": bcrypt.hashpw("secret456".encode(), bcrypt.gensalt())
}

subscriptions = {} 
subscriptions_lock = threading.Lock()
clients_lock = threading.Lock()

def handle_authentication(client_socket):
    try:
        data = client_socket.recv(1024).decode('utf-8').split()
        if len(data) == 3 and data[0] == "LOGIN":
            username = data[1]
            password = data[2].encode()

            if username in user_db and bcrypt.checkpw(password, user_db[username]):
                
                with clients_lock:
                    existing_socket = None
                    # Search for an existing session with this username
                    for sock, user in clients.items():
                        if user == username:
                            existing_socket = sock
                            break
                    
                    if existing_socket:
                        try:
                            # Notify the old client before disconnection 
                            existing_socket.send("FORCED_LOGOUT: New login detected.".encode())
                            existing_socket.close()
                        except:
                            pass
                        # Remove from active clients to make room for the new one 
                        clients.pop(existing_socket)

                client_socket.send("AUTH_SUCCESS".encode())
                return username
            
            client_socket.send("AUTH_FAILED: Invalid credentials.".encode())
    except Exception as e:
        print(f"Auth error: {e}")
    return None

def handle_client(client_socket, addr):
    username = handle_authentication(client_socket)
    if not username:
        client_socket.close()
        return

    # Problem 4: Default lobby on login 
    with clients_lock:
        clients[client_socket] = username
        client_rooms[client_socket] = "lobby"
        rooms["lobby"].add(client_socket)
    
    broadcast(f"{username} joined the lobby", room="lobby") 

    try:
        while True:
            data = client_socket.recv(1024).decode('utf-8')
            if not data: break
            
            # Problem 4: Command Handling 
            if data.startswith("/join "):
                new_room = data.split(" ")[1]
                switch_room(client_socket, username, new_room)
            elif data == "/rooms":
                list_rooms(client_socket)
            elif data.startswith("/leave"):
                switch_room(client_socket, username, "lobby")
            elif data.startswith("/subscribe "):
                parts = data.split(" ", 1)
                if len(parts) > 1:
                    subscribe_to(client_socket, username, parts[1])
            
            elif data.startswith("/unsubscribe "):
                parts = data.split(" ", 1)
                if len(parts) > 1:
                    unsubscribe_from(client_socket, username, parts[1])
            
            # --- Default: Normal Messaging ---
            else:
                current_room = client_rooms[client_socket]
                print(f"[LOG] {username} in {current_room} sent: {data}")
                
                # 1. Broadcast within current room
                broadcast(f"[{current_room}] {username}: {data}", sender_socket=client_socket, room=current_room)
                
                # 2. Multicast to all individual subscribers 
                multicast_to_subscribers(username, data, sender_socket=client_socket)

    except (ConnectionResetError, ConnectionAbortedError):
        # Graceful handling of the Force Logout or client crash 
        print(f"[INFO] Connection with {username} closed.")
    finally:
        cleanup_client(client_socket)

def broadcast(message, sender_socket=None, room=None):
    """Messages broadcast only within current room"""
    # Create a local copy of sockets to send to so we can release the lock quickly
    targets = []
    with clients_lock:
        target_set = rooms.get(room, []) if room else clients.keys()
        targets = list(target_set)

    for sock in targets:
        if sock != sender_socket:
            try:
                sock.send(message.encode('utf-8'))
            except:
                pass

def switch_room(client_socket, username, new_room):
    """Moves a client between rooms"""
    old_room = None
    
    with clients_lock:
        old_room = client_rooms.get(client_socket)
        if old_room:
            rooms[old_room].remove(client_socket)
        
        if new_room not in rooms:
            rooms[new_room] = set()
        
        rooms[new_room].add(client_socket)
        client_rooms[client_socket] = new_room

    # Call broadcasts AFTER releasing the lock to avoid deadlock
    if old_room:
        broadcast(f"{username} left {old_room}", room=old_room)
    
    client_socket.send(f"[SYSTEM] Switched to room: {new_room}".encode('utf-8'))
    broadcast(f"{username} joined {new_room}", room=new_room)


def list_rooms(client_socket):
    """Sends a list of all active rooms to the requesting client."""
    with clients_lock:
        room_names = list(rooms.keys())
        response = f"[SYSTEM] Available rooms: {', '.join(room_names)}"
        try:
            client_socket.send(response.encode('utf-8'))
        except:
            pass

def cleanup_client(client_socket):
    with clients_lock:
        if client_socket in clients:
            username = clients[client_socket]
            room = client_rooms[client_socket]
            rooms[room].remove(client_socket)
            clients.pop(client_socket)
            client_rooms.pop(client_socket)
            broadcast(f"{username} left the chat", room=room)
        client_socket.close()

    with subscriptions_lock:
        for pub_name in subscriptions:
            subscriptions[pub_name].discard(client_socket)

def subscribe_to(client_socket, subscriber_name, publisher_name):
    with subscriptions_lock:
        if publisher_name not in subscriptions:
            subscriptions[publisher_name] = set()
        subscriptions[publisher_name].add(client_socket)
    client_socket.send(f"[SYSTEM] Subscribed to {publisher_name}".encode('utf-8'))

def unsubscribe_from(client_socket, subscriber_name, publisher_name):
    with subscriptions_lock:
        if publisher_name in subscriptions:
            subscriptions[publisher_name].discard(client_socket)
    client_socket.send(f"[SYSTEM] Unsubscribed from {publisher_name}".encode('utf-8'))

def multicast_to_subscribers(publisher_name, message, sender_socket):
    """Sends the message to everyone subscribed to this specific user."""
    target_subs = []
    with subscriptions_lock:
        if publisher_name in subscriptions:
            target_subs = list(subscriptions[publisher_name])
            
    for sub_sock in target_subs:
        try:
            # We don't send it if the subscriber is already in the same room 
            # (they already got it via broadcast)
            if sub_sock != sender_socket:
                sub_sock.send(f"[PUB-SUB] {publisher_name}: {message}".encode('utf-8')) 
        except:
            pass

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Allows the port to be reused immediately after the server stops
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()
    print(f"[LISTENING] Server is listening on {HOST}:{PORT}")

    while True:
        client_socket, addr = server.accept()
        thread = threading.Thread(target=handle_client, args=(client_socket, addr))
        thread.start()
        print(f"[ACTIVE THREADS] {threading.active_count() - 1}")
if __name__ == "__main__":
    start_server()