"""Example 2: Exception handling."""


def safe_divide(a, b):
    """Division with exception handling."""
    try:
        result = a / b
        return result
    except ZeroDivisionError:
        print("Cannot divide by zero!")
        return None
    except TypeError as e:
        print(f"Type error: {e}")
        return None
    finally:
        print("Division attempt completed.")


def parse_int(value):
    """Parse integer with multiple exception types."""
    try:
        num = int(value)
        if num < 0:
            raise ValueError("Negative numbers not allowed")
        return num
    except ValueError as ve:
        print(f"Value error: {ve}")
        return 0
    except Exception as e:
        print(f"Unexpected error: {e}")
        return -1


def process_list(items):
    """Process a list with nested try-except."""
    results = []
    for item in items:
        try:
            try:
                val = float(item)
                if val > 1000:
                    raise OverflowError("Value too large")
                results.append(val)
            except ValueError:
                results.append(None)
        except OverflowError:
            results.append(float('inf'))
    return results


class DatabaseConnection:
    """Simulate a database connection with context manager."""

    def __init__(self, host):
        self.host = host
        self.connected = False

    def __enter__(self):
        try:
            self.connect()
            return self
        except ConnectionError:
            print(f"Failed to connect to {self.host}")
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.connected:
            self.disconnect()
        return False

    def connect(self):
        if not self.host:
            raise ConnectionError("No host specified")
        self.connected = True
        print(f"Connected to {self.host}")

    def disconnect(self):
        self.connected = False
        print(f"Disconnected from {self.host}")

    def query(self, sql):
        if not self.connected:
            raise RuntimeError("Not connected")
        print(f"Executing: {sql}")
        return []


if __name__ == "__main__":
    print(safe_divide(10, 2))
    print(safe_divide(10, 0))
    print(parse_int("42"))
    print(parse_int("abc"))
    print(process_list(["1", "2", "abc", "9999"]))

    with DatabaseConnection("localhost") as db:
        db.query("SELECT * FROM users")
