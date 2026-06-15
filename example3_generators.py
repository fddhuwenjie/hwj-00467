"""Example 3: Generators and closures."""


def countdown(n):
    """Simple generator that counts down."""
    while n > 0:
        yield n
        n -= 1


def fibonacci_gen(limit):
    """Fibonacci number generator."""
    a, b = 0, 1
    while a < limit:
        yield a
        a, b = b, a + b


def make_counter():
    """Closure that creates a counter function."""
    count = 0

    def counter():
        nonlocal count
        count += 1
        return count

    return counter


def make_accumulator(initial=0):
    """Closure that accumulates values."""
    total = initial

    def accumulate(value=None):
        nonlocal total
        if value is not None:
            total += value
        return total

    return accumulate


def memoize(func):
    """Decorator that memoizes function results (closure example)."""
    cache = {}

    def wrapper(*args):
        if args not in cache:
            result = func(*args)
            cache[args] = result
        return cache[args]

    wrapper.cache = cache
    return wrapper


@memoize
def fibonacci_recursive(n):
    """Recursive Fibonacci with memoization."""
    if n <= 1:
        return n
    return fibonacci_recursive(n - 1) + fibonacci_recursive(n - 2)


def chain_generators(*gens):
    """Generator that chains multiple generators."""
    for gen in gens:
        yield from gen


def filter_gen(predicate, iterable):
    """Generator that filters elements."""
    for item in iterable:
        if predicate(item):
            yield item


def map_gen(func, iterable):
    """Generator that maps a function over elements."""
    for item in iterable:
        yield func(item)


class RangeIterator:
    """Custom iterator class."""

    def __init__(self, start, end, step=1):
        self.current = start
        self.end = end
        self.step = step

    def __iter__(self):
        return self

    def __next__(self):
        if self.current >= self.end:
            raise StopIteration
        value = self.current
        self.current += self.step
        return value


def coroutine_example():
    """Simple coroutine example using yield."""
    print("Coroutine started")
    while True:
        value = yield
        if value is None:
            break
        print(f"Received: {value}")
    print("Coroutine ended")


def pipeline():
    """Data processing pipeline with generators and closures."""
    data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    def is_even(x):
        return x % 2 == 0

    def square(x):
        return x * x

    evens = filter_gen(is_even, data)
    squares = map_gen(square, evens)
    return list(squares)


if __name__ == "__main__":
    print("Countdown:")
    for num in countdown(5):
        print(num)

    print("\nFibonacci:")
    for num in fibonacci_gen(100):
        print(num)

    print("\nCounter:")
    c = make_counter()
    print(c(), c(), c())

    print("\nAccumulator:")
    acc = make_accumulator(10)
    print(acc(5), acc(3), acc())

    print("\nMemoized Fibonacci:")
    print(fibonacci_recursive(10))

    print("\nPipeline:")
    print(pipeline())
