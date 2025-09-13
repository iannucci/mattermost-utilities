import json
from mattermostdriver import Driver
import pprint
import time

AGE_THRESHOLD_SECONDS = 86400

file_path = "config.json"
config_data = {}
try:
    with open(file_path, "r") as f:
        config_data = json.load(f)
    print("Configuration data loaded successfully")
except FileNotFoundError:
    print(f"Error: The file '{file_path}' was not found.")
except json.JSONDecodeError:
    print(f"Error: Could not decode JSON from '{file_path}'. Check file format.")
except Exception as e:
    print(f"An unexpected error occurred: {e}")

login_dict = {
    "url": config_data["hostname"],
    "token": config_data["pat"],
    "scheme": config_data["scheme"],
    "port": config_data["port"],
    "basepath": config_data["basepath"],
}

mattermost_api = Driver(login_dict)
mattermost_api.login()

testuser_data = {
    "email": "",
    "username": "",
    "first_name": "",
    "last_name": "",
    "nickname": "",
    "password": "",
}


def create_user(user_data=testuser_data):
    return mattermost_api.users.create_user(options=user_data)


def get_user_id_by_name(user_name):
    user_data = mattermost_api.users.get_user_by_username(user_name)
    if not user_data:
        print(f"User {user_name} not found.")
        return None
    return user_data["id"]


def print_user(user_id):
    user_data = mattermost_api.users.get_user(user_id)
    if not user_data:
        print(f"User {user_name} not found.")
        return None
    print(user_data)
    print(f'User ID: {user_data["id"]}')
    print(f'First name: {user_data["first_name"]}')
    print(f'Last name: {user_data["last_name"]}')
    print(f'Nickname: {user_data["nickname"]}')


def change_username(user_id, new_username):
    user_data = mattermost_api.users.get_user(user_id)
    if not user_data:
        print(f"User {user_id} not found.")
        return None
    user_data["username"] = new_username.strip()
    return mattermost_api.users.update_user(user_id, options=user_data)


def cleanup_user(user_id, first_name, last_name):
    """Adds first and last name to user and sets the nickname to first name + uppercase callsign"""
    user_data = mattermost_api.users.get_user(user_id)
    if not user_data:
        print(f"User {user_id} not found.")
        return None
    username = user_data["username"].strip()
    user_data["first_name"] = first_name.strip()
    user_data["last_name"] = last_name.strip()
    user_data["nickname"] = f"{first_name} {username.upper()}"
    return mattermost_api.users.update_user(user_id, options=user_data)


hoover_channel = {
    "team_id": next(
        (team for team in mattermost_api.teams.get_teams() if team["display_name"] == "Palo Alto ESV"),
        None,
    )["id"],
    "name": "hoover-newsfeed",
    "display_name": "Hoover Newsfeed",
    "type": "O",
    "purpose": "This channel provides newsfeeds from a variety of public sources.",
    "header": "Hoover Newsfeed",
}


def do_the_team_thing():
    teams = mattermost_api.teams.get_teams()
    # for team in teams:
    # 	print(f'Team: {team["display_name"]} ({team["name"]}) - ID: {team["id"]}')
    palo_alto_team = next((team for team in teams if team["display_name"] == "Palo Alto ESV"), None)
    if not palo_alto_team:
        print("Palo Alto ESV team not found.")
    else:
        print(f'Palo Alto ESV Team: {palo_alto_team["display_name"]} ({palo_alto_team["name"]})')
    w6ei = mattermost_api.users.get_user_by_username("w6ei")["id"]
    print(f"User ID for w6ei: {w6ei}")
    channel_dict = mattermost_api.channels.get_channels_for_user(w6ei, palo_alto_team["id"])
    if not channel_dict:
        print("No channels found for user w6ei in Palo Alto ESV team.")
    else:
        print(f"Channels found for user w6ei in Palo Alto ESV team: {len(channel_dict)}")
        for channel in channel_dict:
            print(f'Channel: {channel["display_name"]} ({channel["name"]}) - ID: {channel["id"]}')
    print(mattermost_api.channels.create_channel(options=hoover_channel))


def delete_messages_in_channel(
    user_name, channel_name, team_name, age_threshold_seconds=AGE_THRESHOLD_SECONDS
):
    user = mattermost_api.users.get_user_by_username(user_name)
    if not user:
        print(f"User {user_name} not found.")
        return
    user_id = user["id"]
    teams = mattermost_api.teams.get_teams()
    team = next((team for team in teams if team["display_name"] == team_name), None)
    if team is None:
        print(f"Team {team_name} not found.")
        return
    team_id = team["id"]
    channels = mattermost_api.channels.get_channels_for_user(get_user_id_by_name(user_name), team_id)
    if not channels:
        print(f"No channels found for team {team_name} and user {user_name}.")
        return
    channel = next(
        (channel for channel in channels if channel["display_name"] == channel_name),
        None,
    )
    if channel is None:
        print(f"Channel {channel_name} not found in team {team_name}.")
        return
    channel_id = channel["id"]
    print(
        f"Erasing messages in channel {channel_name} (ID: {channel_id}) in team {team_name} (ID: {team_id})"
    )
    now_timestamp = int(time.time())

    page_number = 0

    while True:
        posts = mattermost_api.posts.get_posts_for_channel(
            channel_id, params={"page": page_number, "per_page": 200}
        )["posts"]
        if not posts:
            print(f"No more messages found in channel {channel_name}.")
            return
        page_number += 1
        for post_id, post_dict in posts.items():
            update_at_timestamp = int(post_dict["update_at"] / 1000)
            age_seconds = now_timestamp - update_at_timestamp

            if age_seconds > age_threshold_seconds:
                print(
                    f"Deleting post {post_id} in channel {channel_name} because age in seconds ({age_seconds}) exceeds the thresshold ({age_threshold_seconds})."
                )
                mattermost_api.posts.delete_post(post_id)


def lookup_channel_by_name(channel_name, team_name, user_name):
    teams = mattermost_api.teams.get_teams()
    team = next((team for team in teams if team["display_name"] == team_name), None)
    if team is None:
        print(f"Team {team_name} not found.")
        return
    team_id = team["id"]
    channels = mattermost_api.channels.get_channels_for_user(get_user_id_by_name(user_name), team_id)
    if not channels:
        print(f"No channels found for team {team_name}.")
        return
    channel = next(
        (channel for channel in channels if channel["display_name"] == channel_name),
        None,
    )
    if channel is None:
        print(f"Channel {channel_name} not found in team {team_name}.")
        return
    return channel["id"]
    # print(f'Channel {channel_name} ID: {channel["id"]}')
    # print(json.dumps(channel, indent=4))


# print(lookup_channel_by_name('National Weather Service', 'Palo Alto ESV', 'hoover'))
# print(lookup_channel_by_name('CalTrans', 'Palo Alto ESV', 'hoover'))
# print(lookup_channel_by_name('US Geological Survey', 'Palo Alto ESV', 'hoover'))
# print(lookup_channel_by_name('Local Weather', 'Palo Alto ESV', 'hoover'))

user = ""
first = ""
last = ""

# create_user()
# print_user(get_user_id_by_name(user))
# cleanup_user(get_user_id_by_name(user), first, last)
# print_user(get_user_id_by_name(user))

delete_messages_in_channel("w6ei", "Local Weather", "Palo Alto ESV")
delete_messages_in_channel("w6ei", "CalTrans", "Palo Alto ESV")
delete_messages_in_channel("w6ei", "US Geological Survey", "Palo Alto ESV")
