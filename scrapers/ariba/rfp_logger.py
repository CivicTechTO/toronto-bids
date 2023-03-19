import functools
import logging

# Inject this later
logging.basicConfig(filename='python.log', filemode='a', encoding="utf-8", level=logging.INFO)
logger = logging.getLogger()

def log(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        arguments = {
            "args": [repr(a) for a in args],
            "kwargs": {k: repr(v) for k, v in kwargs.items()}
        }
        logger.info(f"Arguments: {arguments}")
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            logger.exception(f"Exception raised in {func.__name__}. Exception: {str(e)}")
            raise e
    return wrapper

# Helper code to debug - remove later
@log
def foo(**kwargs):
    raise ValueError("Waa")


if __name__ == "__main__":
    foo(a=1, b=2, c=[1, 1])