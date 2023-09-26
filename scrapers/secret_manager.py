import json
from os import environ
from pathlib import Path
import pickle
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient


class Keychain:
    def __init__(self):
        if Path("secrets.pickle").exists():
            with open("secrets.pickle", "rb") as f:
                self.cache = pickle.load(f)
        else:
            self.cache = {}
        self.vault_uri = self.get_config("vault_uri")
        self.credential = DefaultAzureCredential()
        self.client = SecretClient(vault_url=self.vault_uri, credential=self.credential)

    def get_secret(self, secret_name):
        # Check if secret is in cache
        if secret_name in self.cache:
            return self.cache[secret_name]
        # Check if secret is in environment variables
        if secret_name in environ:
            self.cache[secret_name] = environ[secret_name]
            return environ[secret_name]
        # Check if secret is in Azure Key Vault
        try:
            secret = self.client.get_secret(secret_name).value
            self.cache[secret_name] = secret
            return secret
        except Exception as e:
            print(e)
            pass
        # Otherwise raise an exception
        raise KeyError(f"Secret {secret_name} not found")

    def get_config(self, config_name):
        # Check if config is in cache
        if config_name in self.cache:
            return self.cache[config_name]
        # Check if config is in environment variables
        if config_name in environ:
            self.cache[config_name] = environ[config_name]
            return environ[config_name]
        # Check if config is in config.json
        with open("config.json", "r") as f:
            config = json.load(f)
        if config_name in config:
            self.cache[config_name] = config[config_name]
            return config[config_name]
        raise KeyError(f"Config {config_name} not found")
