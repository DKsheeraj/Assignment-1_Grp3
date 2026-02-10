import socket
import threading
import ssl
import sys
import os

# Default Config
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8000

def receive_messages(client_socket):
    while True:
        try:
            message = client_socket.recv(1024).decode('utf-8')
            if not message:
                break
            
            if message.startswith("FORCED_LOGOUT"):
                print(f"\n[SYSTEM] {message}")
                client_socket.close()
                os._exit(0) # Force exit immediately
            
            print(f"\n{message}")
        except:
            print("\n[SYSTEM] Disconnected from server.")
            os._exit(0)

def start_client(host, port):
    # Create an SSL context that REQUIRES verification
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    
    # Load the server's public certificate (Pinning)
    # The client must have the 'server.crt' file in the same folder
    try:
        context.load_verify_locations('server.crt')
    except FileNotFoundError:
        print("Error: 'server.crt' not found. Cannot verify server.")
        return

    # Enforce verification
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True

    raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print(f"Connecting to {host}:{port} securely...")
    
    try:
        client_socket = context.wrap_socket(raw_socket, server_hostname=host)
        client_socket.connect((host, port))
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    try:
        username = input("Username: ")
        password = input("Password: ")
        client_socket.send(f"LOGIN {username} {password}".encode())

        response = client_socket.recv(1024).decode()
        if not response.startswith("AUTH_SUCCESS"):
            print(f"Login failed: {response}")
            return

        print("Login successful! Commands: /join <room>, /leave, /rooms, /subscribe <user>")
        
        threading.Thread(target=receive_messages, args=(client_socket,), daemon=True).start()
        
        while True:
            msg = input()
            if msg.lower() == '/quit':
                break
            client_socket.send(msg.encode())
            
    except KeyboardInterrupt:
        pass
    finally:
        client_socket.close()

if __name__ == "__main__":    
    # Defaults
    target_host = DEFAULT_HOST
    target_port = DEFAULT_PORT

    # handle arguments: python client.py [host] [port]
    if len(sys.argv) == 2:
        # User provided only port: python client.py 8001
        target_port = int(sys.argv[1])
    elif len(sys.argv) == 3:
        # User provided host and port: python client.py 192.168.1.5 8002
        target_host = sys.argv[1]
        target_port = int(sys.argv[2])

    start_client(target_host, target_port)