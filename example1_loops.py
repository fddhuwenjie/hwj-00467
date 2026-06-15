"""Example 1: Loops and conditional branches."""


def fibonacci(n):
    """Compute Fibonacci numbers with a loop and conditionals."""
    if n <= 0:
        return 0
    elif n == 1:
        return 1

    a, b = 0, 1
    for i in range(2, n + 1):
        temp = a + b
        a = b
        b = temp

    return b


def factorial(n):
    """Compute factorial with a while loop."""
    result = 1
    while n > 1:
        result *= n
        n -= 1
    return result


def classify_number(x):
    """Classify a number with nested conditionals."""
    if x > 0:
        if x > 100:
            category = "large_positive"
        else:
            category = "small_positive"
    elif x < 0:
        if x < -100:
            category = "large_negative"
        else:
            category = "small_negative"
    else:
        category = "zero"

    unused_var = 42
    return category


class Counter:
    """A simple counter class."""

    def __init__(self, start=0):
        self.count = start

    def increment(self, amount=1):
        for i in range(amount):
            self.count += 1
        return self.count

    def reset(self):
        self.count = 0


if __name__ == "__main__":
    print(fibonacci(10))
    print(factorial(5))
    print(classify_number(42))
    c = Counter()
    c.increment(5)
    print(c.count)
