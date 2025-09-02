import meshtastic.tcp_interface as tcp
from mattermostdriver import Driver
from pubsub import pub  # https://pypubsub.readthedocs.io/en/v4.0.3/
import logging
import os
import argparse
import json
import time

DEFAULT_CFG = "/etc/meshtastic-client/config.json"

# {'num': 3723255035,
#  'user': {'id': '!ddec5cfb',
#           'longName': 'W6EI South Court',
#           'shortName': 'EI/S',
#           'macaddr': '2Drd7Fz7',
#           'hwModel': 'PORTDUINO'},
#  'position': {'latitudeI': 374202655,
#               'longitudeI': -1221206006,
#               'altitude': 11,
#               'time': 1756493982,
#               'locationSource': 'LOC_INTERNAL',
#               'latitude': 37.4202655,
#               'longitude': -122.1206006},
#  'snr': 7.0,
#  'lastHeard': 1756493982,
#  'deviceMetrics': {'batteryLevel': 101,
#                    'channelUtilization': 19.92,
#                    'airUtilTx': 2.6313055,
#                    'uptimeSeconds': 2355},
#  'isFavorite': True}
# pprint.pp(interface.getMyNodeInfo())

# {
#     "id": "!ddec5cfb",
#     "longName": "W6EI South Court",
#     "shortName": "EI/S",
#     "macaddr": "2Drd7Fz7",
#     "hwModel": "PORTDUINO",
# }
# pprint.pp(interface.getMyUser())


# {
#     "channel": 0,
#     "from": 3663154064,
#     "hop_start": 3,
#     "hops_away": 0,
#     "id": 2026663140,
#     "payload": {"text": "Test"},
#     "rssi": -45,
#     "sender": "!ddec5cfb",
#     "snr": 7.25,
#     "timestamp": 1756503807,
#     "to": 4294967295,
#     "type": "text",
# }


class MeshtasticClient:
    def __init__(self, host, callback, logger):
        self.host = host
        self.callback = callback
        self.logger = logger
        self.interface = None
        # Establish a connection to the Meshtastic device
        try:
            self.interface = tcp.TCPInterface(hostname=self.host, portNumber=4403, connectNow=True)
            pub.subscribe(self._on_receive, "meshtastic.receive")
            self.logger.info(
                "[Meshtastic] Connected to Meshtastic device and listening for messages"
            )
        except Exception as e:
            self.logger.error(f"[Meshtastic] Error connecting to device: {e}")
            raise

    def close(self):
        self.interface.close()

    def _get_long_name_from_id(self, node_id):
        """
        Connects to a Meshtastic device and retrieves the long name of a node
        given its ID.
        """
        try:
            iface = tcp.TCPInterface(self.host)
            if iface.nodes:
                # Iterate through the known nodes in the interface's node database
                for node_num, node_data in iface.nodes.items():
                    # Check if the current node's user ID matches the target node_id
                    if "user" in node_data and node_data["user"]["id"] == node_id:
                        return node_data["user"]["longName"]
            return None  # Return None if the node ID is not found

        except Exception as e:
            print(f"Error connecting to Meshtastic device or retrieving data: {e}")
            return None
        finally:
            if "iface" in locals() and iface:
                iface.close()  # Ensure the interface is closed

    # Translates a node ID into its short name and long name
    def _id_to_name(self, interface, id):
        short_name = ""
        long_name = ""
        for node_id, node_info in interface.nodes.items():
            if id == node_id:
                user = node_info["user"]
                long_name = user["longName"]
                short_name = user["shortName"]
                break
        return short_name, long_name

    def _on_receive(self, packet, interface):
        # Check if the packet contains a text message
        self.logger.debug("[Meshtastic] _on_receive")
        if (
            "decoded" in packet
            and "portnum" in packet["decoded"]
            and packet["decoded"]["portnum"] == "TEXT_MESSAGE_APP"
        ):
            try:
                text_message = packet["decoded"]["payload"].decode("utf-8")
                # from_node = packet["from"]
                from_id = packet["fromId"]  # from_id is of the form !da574b90
                short_name, long_name = self._id_to_name(interface, from_id)
                callsign = long_name.split()[0].upper()
                self.logger.debug(f"Received message <{text_message}> from {callsign}")
                self.callback(callsign, text_message)

            except UnicodeDecodeError:
                self.logger.debug("Received a non-UTF-8 text message.")
        # else:
        #   Handle other types of packets or log them for debugging
        #   print(f"Received non-text packet: {packet}")


class MattermostClient:
    def __init__(self, config, logger):
        self.host = config.get("host", "")
        self.scheme = config.get("scheme", "http")
        self.port = int(config.get("port", 80))
        self.basepath = config.get("basepath", "/api/v4").rstrip("/")
        self.team = config.get("team", "")
        self.admin_token = config.get("admin-token", "")
        self.users = config.get("users", [])
        self.mattermost_login_config = {
            "url": self.host,
            "token": self.admin_token,
            "scheme": self.scheme,
            "port": self.port,
            "basepath": self.basepath,
        }
        self.logger = logger
        self.admin_driver = None
        self.user_driver = None

    def close(self):
        if self.user_driver is not None:
            self.user_driver.logout()
            self.user_driver = None
        if self.admin_driver is not None:
            self.admin_driver.logout()
            self.admin_driver = None

    def callback(self, callsign, message):
        self.logger.info(f"[Mattermost] Callback received: {callsign}: {message}")
        self._post(callsign, message)

    # Returns the text team annd channel names as well as the user's token
    def _lookup_user_by_callsign(self, callsign):
        team = ""
        channel = ""
        token = ""
        callsign_lower = callsign.lower()
        for user in self.users:
            if user["callsign"] == callsign_lower:
                team = user["team"]
                channel = user["channel"]
                token = user["token"]
                break
        return team, channel, token

    def _get_channel_id_by_name(self, channel_name, team_name, user_name):
        try:
            self.admin_driver = Driver(self.mattermost_login_config)
            self.admin_driver.login()
        except Exception as e:
            self.logger.error(
                f"Could not establish a connection to the Mattermost server {self.host} for the admin user"
            )
        user_id = self.admin_driver.users.get_user_by_username(user_name).get("id")
        teams = self.admin_driver.teams.get_user_teams(user_id)
        team = next((team for team in teams if team["display_name"] == team_name), None)
        if team is None:
            self.logger.warning(f"[Mattermost] Team {team_name} not found for user {user_name}.")
            return
        team_id = team["id"]
        channels = self.admin_driver.channels.get_channels_for_user(user_id, team_id)
        if not channels:
            self.logger.warning(f"[Mattermost] No channels found for team {team_name}.")
            return
        channel = next(
            (channel for channel in channels if channel["display_name"] == channel_name),
            None,
        )
        if channel is None:
            self.logger.warning(
                f"[Mattermost] Channel {channel_name} not found in team {team_name}."
            )
            return
        self.close()
        return channel["id"]

    def _post(self, callsign, message):
        callsign = callsign.lower()  # the user dictionary and Mattermost use lower case callsigns
        try:
            team, channel, token = self._lookup_user_by_callsign(callsign)
            channel_id = self._get_channel_id_by_name(channel, team, callsign)
            user_login_config = {
                "url": self.host,
                "token": token,
                "scheme": self.scheme,
                "port": self.port,
                "basepath": self.basepath,
            }
            self.user_driver = Driver(user_login_config)
            self.user_driver.login()
            post_dict = {
                "channel_id": channel_id,
                "message": message,
            }
            self.user_driver.posts.create_post(post_dict)
        except Exception as e:
            self.logger.error(
                f"Could not establish a connection to the Mattermost server {self.host} for user {callsign}"
            )
        finally:
            self.close()


def find_config_path(cli_path: str):
    cwd_cfg = os.path.abspath(os.path.join(os.getcwd(), "config.json"))
    if os.path.exists(cwd_cfg):
        return cwd_cfg
    return cli_path


def build_logger(level: str):
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    return logging.getLogger("meshtastic-client")


def main():
    ap = argparse.ArgumentParser(description="mattermost-newsfeeds")
    ap.add_argument(
        "--config",
        default=DEFAULT_CFG,
        help=f"Path to config file (default: {DEFAULT_CFG})",
    )
    args = ap.parse_args()
    config_path = find_config_path(args.config)
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except Exception as e:
        print(f"Error loading config {config_path}: {e}")
        return
    logger = build_logger(config.get("log_level", "DEBUG"))
    logger.debug("Logging is active")

    meshtastic_client = None
    mattermost_client = None

    try:
        meshtastic_config = config.get("meshtastic", {})
        mattermost_config = config.get("mattermost", {})
        mattermost_client = MattermostClient(mattermost_config, logger)
        meshtastic_client = MeshtasticClient(
            meshtastic_config.get("host", ""), mattermost_client.callback, logger
        )
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\nExiting.")
    finally:
        if meshtastic_client is not None:
            meshtastic_client.close()
        if mattermost_client is not None:
            mattermost_client.close()


if __name__ == "__main__":
    main()
