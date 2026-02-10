import socket
import threading
import sys
HOST = '127.0.0.1'
PORT = 8000

def receive_messages(client_socket):
    while True:
        try:
            message = client_socket.recv(1024).decode('utf-8')
            if not message:
                break
            
            # Check for force logout signal from server
            if message.startswith("FORCED_LOGOUT"):
                print(f"\n[SYSTEM] {message}")
                print("Disconnected by server.")
                client_socket.close()
                sys.exit()
            else:
                print(f"\n{message}")
        except:
            break

def start_client():
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(('127.0.0.1', 8000))

    # Authentication Phase 
    username = input("Username: ")
    password = input("Password: ")
    auth_command = f"LOGIN {username} {password}"
    client.send(auth_command.encode('utf-8'))

    response = client.recv(1024).decode('utf-8')
    if response != "AUTH_SUCCESS":
        print(f"Login failed: {response}")
        return

    print("Login successful! Entering chat...")

    # Start threads for communication
    threading.Thread(target=receive_messages, args=(client,), daemon=True).start()
    
    try:
        while True:
            msg = input()
            client.send(msg.encode('utf-8'))
    except KeyboardInterrupt:
        client.close()

if __name__ == "__main__":
    start_client()