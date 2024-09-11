from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


class Slack:
    def __init__(self, token: str, log_channel: str, update_channel: str):
        self.client = WebClient(token=token)
        self.log_channel = log_channel
        self.update_channel = update_channel

    def post_message(self, channel: str, message: str, thread: str = None) -> str:
        print(f"#{channel}: {message}")
        try:
            if thread:
                response = self.client.chat_postMessage(channel=channel, text=message, thread_ts=thread)
            else:
                response = self.client.chat_postMessage(channel=channel, text=message)
            # Return timestamp
            return response["ts"]
        except SlackApiError as e:
            return f"Error: {e.response['error']}"

    def post_log(self, message: str, thread: str = None) -> str:
        return self.post_message(self.log_channel, message, thread)

    def post_update(self, message: str, thread: str = None) -> str:
        return self.post_message(self.update_channel, message, thread)