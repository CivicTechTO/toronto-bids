from os import environ
from pathlib import Path
import pickle

class Keychain():

    def __init__(self):
        if Path("secrets.pickle").exists():
            with open("secrets.pickle", "rb") as f:
                self.cache = pickle.load(f)
        else:
            self.cache = {}

    def get_secret(self, secret_name):
        # Check if secret is in cache
        if secret_name in self.cache:
            return self.cache[secret_name]
        # Check if secret is in environment variables
        if secret_name in environ:
            return environ[secret_name]
        # Otherwise raise an exception
        raise KeyError(f"Secret {secret_name} not found")
