import meshtastic.tcp_interface as tcp
from mattermostdriver import Driver
from pubsub import pub
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

HOSTNAME = "w6ei-southcourt-meshtastic.local.mesh"


class MeshtasticClient:
    def __init__(self, hostname, callback, logger):
        self.hostname = hostname
        self.callback = callback
        self.logger = logger
        self.interface = None
        # Establish a connection to the Meshtastic device
        try:
            self.interface = tcp.TCPInterface(
                hostname=self.hostname, portNumber=4403, connectNow=True
            )
            self.logger.debug("[Meshtastic] Connected to Meshtastic device.")
            pub.subscribe(self._on_receive, "meshtastic.receive")
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
            iface = tcp.TCPInterface(HOSTNAME)
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
                self.callback(callsign, text_message, self.logger)

            except UnicodeDecodeError:
                self.logger.debug("Received a non-UTF-8 text message.")
        # else:
        #   Handle other types of packets or log them for debugging
        #   print(f"Received non-text packet: {packet}")


class MattermostClient:
    def __init__(self, config, logger):
        self.host = config.get("host", "")
        self.token = config.get("token", "")
        self.scheme = config.get("scheme", "http")
        self.port = int(config.get("port", 80))
        self.basepath = config.get("basepath", "/api/v4").rstrip("/")
        self.team = config.get("team", "")
        self.user = config.get("user", "")
        self.mattermost_login_config = {
            "url": self.host,
            "token": self.token,
            "scheme": self.scheme,
            "port": self.port,
            "basepath": self.basepath,
        }
        self.logger = logger
        self.driver = None
        try:
            self.driver = Driver(self.mattermost_login_config)
        except Exception as e:
            self.logger.error(
                f"Could not establish a connection to the Mattermost server {self.host}"
            )
            raise

    def close(self):
        self.driver.close()


def find_config_path(cli_path: str):
    cwd_cfg = os.path.abspath(os.path.join(os.getcwd(), "config.json"))
    if os.path.exists(cwd_cfg):
        return cwd_cfg
    return cli_path


def build_logger(level: str):
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    return logging.getLogger("meshtastic-client")


def mestastic_callback(callsign, message, logger):
    logger.debug(f"Callback received: {callsign}: {message}")


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
    logger.info("Logging is active")

    meshtastic_client = None
    mattermost_client = None

    try:
        meshtastic_config = config.get("meshtastic", {})
        mattermost_config = config.get("mattermost", {})
        meshtastic_client = MeshtasticClient(
            meshtastic_config.get("host", ""), mestastic_callback, logger
        )
        mattermost_client = MattermostClient(mattermost_config, logger)
        while True:
            time.sleep(1)  # Keep the main thread alive
    except KeyboardInterrupt:
        logger.info("\nExiting.")
    finally:
        if meshtastic_client is not None:
            meshtastic_client.close()
        if mattermost_client is not None:
            mattermost_client.close()


if __name__ == "__main__":
    main()
