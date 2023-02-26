import os
import yaml
class ConfigReader:
    def __init__(self):
        config_path = os.path.join(os.getcwd(), 'config.yaml')
        self.file_path = config_path

    def read(self):
        with open(self.file_path, 'r') as f:
            data = yaml.safe_load(f)
        return data

    def load_key(self, key):
        data = self.read()
        return data.get(key, {})

    def get_working_directory():
        return os.getcwd()