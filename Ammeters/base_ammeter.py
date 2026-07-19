import socket
import time
import random
from abc import ABC, abstractmethod

NotImplementedErrorMsg = "Subclasses must implement this property."

class AmmeterEmulatorBase(ABC):
    def __init__(self, port: int):
        self.port = port
        random.seed(time.time())  # Seed the random number generator for each instance

    def start_server(self):
        """
        Starts the server to listen for client requests.
        The server will run indefinitely, handling one client request at a time.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # BUG FIX: without SO_REUSEADDR, the OS can hold this port in a TIME_WAIT
            # state for a short period after a previous server on the same port closes
            # (even in a completely separate process run). Re-binding during that window
            # raises "OSError: [Errno 98] Address already in use" and crashes this thread,
            # which then makes every client request to this ammeter fail with
            # "Connection refused" since nothing is listening. This is especially likely
            # if scripts using these emulators are run back-to-back in quick succession.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('localhost', self.port))
            s.listen()
            print(f"{self.__class__.__name__} is running on port {self.port}")
            while True:
                conn, addr = s.accept()
                with conn:
                    print(f"Connected by {addr}")
                    try:
                        data = conn.recv(1024)
                        if data != self.get_current_command:
                            conn.sendall(b"ERROR: unknown command")
                            continue

                        # Call the specific measure_current() method defined in subclasses.
                        current = self.measure_current()
                        conn.sendall(str(current).encode('utf-8'))
                    except Exception as exc:
                        # A bad request or emulator fault must not terminate the server thread.
                        print(f"Request failed on port {self.port}: {exc}")
                        try:
                            conn.sendall(f"ERROR: {exc}".encode("utf-8"))
                        except OSError:
                            pass

    @property
    @abstractmethod
    def get_current_command(self) -> bytes:
        """
        This property must be implemented by each subclass to provide the specific
        command to get the current measurement.
        """
        raise NotImplementedError(NotImplementedErrorMsg)

    @abstractmethod
    def measure_current(self) -> float:
        """
        This method must be implemented by each subclass to provide the specific
        logic for current measurement.
        """
        raise NotImplementedError(NotImplementedErrorMsg)

