# !python3
# -*- coding: utf-8 -*-

"""
Meshbot Weather
=======================

meshbot.py: A message bot designed for Meshtastic, providing information from modules upon request:


Author:
- Andy
- April 2024

MIT License

Copyright (c) 2024 Andy

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""



import argparse
import logging
import threading
import time
import yaml
import datetime
import random
import signal
import sys
import requests
from pubsub import pub
from functools import partial

try:
    import meshtastic.serial_interface
    import meshtastic.tcp_interface
except ImportError:
    print(
        "ERROR: Missing meshtastic library!\nYou can install it via pip:\npip install meshtastic\n"
    )

from modules.temperature_24hour import Temperature24HourFetcher
from modules.forecast_2day import Forecast2DayFetcher
from modules.hourly_weather import EmojiWeatherFetcher
from modules.rain_24hour import RainChanceFetcher
from modules.forecast_5day import NWSWeatherFetcher5Day
from modules.weather_data_manager import WeatherDataManager
from modules.weather_alert_monitor import WeatherAlerts
from modules.forecast_4day import Forecast4DayFetcher
from modules.forecast_7day import Forecast7DayFetcher
from modules.wind_24hour import Wind24HourFetcher
from modules.reddit_rss import RedditRSSFetcher

UNRECOGNIZED_MESSAGES = [
    "Oops! I didn't recognize that command. Type 'menu' to see a list of options.",
    "I'm not sure what you mean. Type 'menu' for available commands.",
    "That command isn't in my vocabulary. Send 'menu' to see what I understand.",
    "Hmm, I don't know that one. Send 'menu' for a list of commands I know.",
    "Sorry, I didn't catch that. Send 'menu' to see what commands you can use.",
    "Well that's definitely not in my programming. Type 'menu' before we both crash.",
    "Oh sure, just make up commands. Type 'menu' for the real ones."
]


class MeshBot:
    def __init__(self, settings):
        self.settings = settings
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
        )
        self.logger = logging.getLogger()
        self.user_agent_app = settings.get("USER_AGENT_APP", "myweatherapp")
        self.user_agent_email = settings.get("USER_AGENT_EMAIL", "contact@example.com")
        self.user_agent = f"({self.user_agent_app}, {self.user_agent_email})"
        self.interface = None
        self.mynode = ""
        self.mynodes = settings.get("MYNODES")
        self.dm_mode = settings.get("DM_MODE")
        self.firewall = settings.get("FIREWALL")
        self.dutycycle = settings.get("DUTYCYCLE")
        self.cooldown = False
        self.transmission_count = 0
        self.alerts = None
        self.temperature_24hour_info = None
        self.forecast_2day_info = None
        self.emoji_weather_info = None
        self.rain_chance_info = None
        self.weather_manager = WeatherDataManager(
            settings.get("NWS_OFFICE", "HNX"),
            settings.get("NWS_GRID_X", "67"),
            settings.get("NWS_GRID_Y", "80"),
            self.user_agent
        )
        self.temperature_24hour = Temperature24HourFetcher(self.weather_manager)
        self.forecast_2day = Forecast2DayFetcher(self.weather_manager)
        self.emoji_weather_fetcher = EmojiWeatherFetcher(self.weather_manager)
        self.rain_chance_fetcher = RainChanceFetcher(self.weather_manager)
        self.nws_weather_fetcher_5day = NWSWeatherFetcher5Day(self.weather_manager)
        self.forecast_4day = Forecast4DayFetcher(self.weather_manager)
        self.forecast_7day = Forecast7DayFetcher(self.weather_manager)
        self.wind_24hour = Wind24HourFetcher(self.weather_manager)
        self.reddit_fetcher = RedditRSSFetcher("python")

    def find_serial_ports(self):
        import serial.tools.list_ports
        ports = [port.device for port in serial.tools.list_ports.comports()]
        filtered_ports = [port for port in ports if "COM" in port.upper() or "USB" in port.upper()]
        return filtered_ports

    def get_temperature_24hour(self):
        self.temperature_24hour_info = self.temperature_24hour.get_temperature_24hour()
        return self.temperature_24hour_info

    def get_forecast_2day(self):
        self.forecast_2day_info = self.forecast_2day.get_daily_weather()
        return self.forecast_2day_info

    def get_emoji_weather(self):
        self.emoji_weather_info = self.emoji_weather_fetcher.get_emoji_weather()
        return self.emoji_weather_info

    def get_rain_chance(self):
        self.rain_chance_info = self.rain_chance_fetcher.get_rain_chance()
        return self.rain_chance_info

    def reset_transmission_count(self):
        if self.dutycycle:
            self.transmission_count -= 1
            if self.transmission_count < 0:
                self.transmission_count = 0
            self.logger.info(f"Reducing transmission count {self.transmission_count}")
            threading.Timer(180.0, self.reset_transmission_count).start()

    def reset_cooldown(self):
        self.cooldown = False
        self.logger.info("Cooldown Disabled.")
        threading.Timer(240.0, self.reset_cooldown).start()

    def split_message(self, message, max_length=200, message_type="Hourly", start_index=1, total_count=None):
        lines = message.split('\n')
        messages = []
        current_message = []
        current_length = 0
        for line in lines:
            line_length = len(line.encode('utf-8')) + (1 if current_message else 0)
            if current_length + line_length > max_length:
                messages.append('\n'.join(current_message))
                current_message = []
                current_length = 0
            current_message.append(line)
            current_length += line_length
        if current_message:
            messages.append('\n'.join(current_message))
        # If total_count is provided, use it for page count
        if total_count is None:
            total_count = len(messages)
        for i in range(len(messages)):
            messages[i] = f"--({start_index + i}/{total_count}) {message_type}\n" + messages[i]
        return messages

    def get_forecast_4day(self):
        forecast_4day_info = self.forecast_4day.get_weekly_emoji_weather()
        return "\n".join(forecast_4day_info)

    def get_wind_24hour(self):
        wind_24hour_info = self.wind_24hour.get_wind_24hour()
        return wind_24hour_info

    def get_custom_lookup(self, message):
        """
        Parse message like 'loc lat/lon command' and return the weather info for that location.
        Uses api.weather.gov /points/{lat},{lon} to get grid/office.
        Supported commands: 2day, 4day, 5day, 7day, hourly, temp, rain, wind
        """
        import re
        import requests
        match = re.match(r"loc\s+([+-]?\d+\.\d+)/([+-]?\d+\.\d+)\s*(\w+)?", message)
        if not match:
            return "Invalid location format. Use 'loc lat/lon [command]'."
        lat, lon, command = match.groups()
        try:
            url = f"https://api.weather.gov/points/{lat},{lon}"
            resp = requests.get(url, headers={"User-Agent": self.user_agent})
            resp.raise_for_status()
            data = resp.json()
            office = data['properties']['cwa']
            grid_x = str(data['properties']['gridX'])
            grid_y = str(data['properties']['gridY'])
        except Exception as e:
            return f"Entered grid is invalid or not found for {lat},{lon}: Not part of NWS coverage area."
        temp_manager = WeatherDataManager(office, grid_x, grid_y, self.user_agent)
        fetchers = {
            '2day': lambda: Forecast2DayFetcher(temp_manager).get_daily_weather(),
            '4day': lambda: Forecast4DayFetcher(temp_manager).get_weekly_emoji_weather(),
            '5day': lambda: NWSWeatherFetcher5Day(temp_manager).get_daily_weather(),
            '7day': lambda: Forecast7DayFetcher(temp_manager).get_weekly_emoji_weather(),
            'hourly': lambda: EmojiWeatherFetcher(temp_manager).get_emoji_weather(),
            'temp': lambda: Temperature24HourFetcher(temp_manager).get_temperature_24hour(),
            'rain': lambda: RainChanceFetcher(temp_manager).get_rain_chance(),
            'wind': lambda: Wind24HourFetcher(temp_manager).get_wind_24hour(),
        }
        if command and command in fetchers:
            result = fetchers[command]()
            if isinstance(result, list):
                return '\n'.join(result)
            return str(result)
        else:
            return f"Custom location lookup: lat={lat}, lon={lon}, office={office}, grid=({grid_x},{grid_y})\nSupported commands: {', '.join(fetchers.keys())}"

    def get_my_node_id(self):
        try:
            my_info = self.interface.getMyNodeInfo()
            return str(my_info.get('num', ''))
        except Exception as e:
            self.logger.error(f"Failed to get node info: {e}")
            return ''

    def get_weather_alert_status(self):
        try:
            base_url = f"https://api.weather.gov/alerts/active"
            params = {
                "point": f"{self.settings.get('ALERT_LAT')},{self.settings.get('ALERT_LON')}"
            }
            headers = {
                "User-Agent": self.user_agent
            }
            response = requests.get(base_url, params=params, headers=headers)
            response.raise_for_status()
            return "🟢 Alert System: Active and monitoring for weather alerts"
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Weather Alert Monitor Status Check Failed: {str(e)}")
            return "🔴 Alert System: Unable to connect to weather service"
        except Exception as e:
            self.logger.error(f"Weather Alert Monitor Status Check Failed: {str(e)}")
            return "🔴 Alert System: Service interrupted - check logs"

    def schedule_daily_reboot(self, interface):
        if not self.settings.get('ENABLE_AUTO_REBOOT', True):
            return
        reboot_hour = self.settings.get('AUTO_REBOOT_HOUR', 3)
        reboot_minute = self.settings.get('AUTO_REBOOT_MINUTE', 0)
        reboot_delay = self.settings.get('REBOOT_DELAY_SECONDS', 10)
        while True:
            now = datetime.datetime.now()
            next_reboot = now.replace(
                hour=reboot_hour,
                minute=reboot_minute,
                second=0,
                microsecond=0
            )
            if now >= next_reboot:
                next_reboot += datetime.timedelta(days=1)
            seconds_until_reboot = (next_reboot - now).total_seconds()
            time.sleep(seconds_until_reboot)
            try:
                self.logger.info(f"Executing scheduled reboot at {next_reboot}")
                interface.localNode.reboot(secs=reboot_delay)
            except Exception as e:
                self.logger.error(f"Failed to execute scheduled reboot: {e}")

    def message_listener(self, packet, interface=None, **kwargs):
        try:
            if packet is not None and packet["decoded"].get("portnum") == "TEXT_MESSAGE_APP":
                message = packet["decoded"]["text"].lower()
                sender_id = packet["from"]
                
                # Check if it's a DM
                is_direct_message = False
                if "to" in packet:
                    is_direct_message = str(packet["to"]) == str(self.mynode)

                # Only log if it's a DM
                if is_direct_message:
                    self.logger.info(f"Message {packet['decoded']['text']} from {packet['from']}")
                    self.logger.info(f"transmission count {self.transmission_count}")

                # Enforce DM_MODE
                if self.dm_mode and not is_direct_message:
                    return

                # firewall logging
                if self.firewall and not any(node in str(packet["from"]) for node in self.mynodes):
                    self.logger.warning(f"Firewall blocked message from {packet['from']}: {message}")
                    return

                if (self.transmission_count < 16 or self.dutycycle == False):
                    first_message_delay = self.settings.get('FIRST_MESSAGE_DELAY', 3)
                    subsequent_message_delay = self.settings.get('MESSAGE_DELAY', 10)

                    # Helper function to handle message sequences
                    def send_message_sequence(messages, message_type=""):
                        for i, msg in enumerate(messages):
                            if i == 0:  # First message
                                time.sleep(first_message_delay)
                            self.interface.sendText(msg, wantAck=True, destinationId=sender_id)
                            if i < len(messages) - 1:  # Don't delay after last message
                                time.sleep(subsequent_message_delay)

                    if "test" in message:
                        self.transmission_count += 1
                        time.sleep(first_message_delay)
                        messages = self.split_message(" ACK", message_type="Test")
                        send_message_sequence(messages, message_type="Test")
                    elif "?" in message or "menu" in message:
                        self.transmission_count += 1
                        time.sleep(first_message_delay)
                        menu_text_1 = "    --Multi-Message--\n" \
                            "hourly - 24h outlook\n" \
                            "7day - 7 day simple\n" \
                            "5day - 5 day detailed\n" \
                            "wind - 24h wind\n"
                        menu_text_2 = "    --Single Message--\n" \
                            "2day - 2 day detailed\n" \
                            "4day - 4 day simple\n" \
                            "rain - 24h precipitation\n" \
                            "temp - 24h temperature\n"
                        if self.settings.get('ENABLE_CUSTOM_LOOKUP', False):
                            menu_text_2 += "loc lat/lon - custom location lookup\n"
                        if self.settings.get('FULL_MENU', True):
                            combined_menu = menu_text_1 + "\n" + menu_text_2
                            messages = self.split_message(combined_menu, message_type="Menu")
                            send_message_sequence(messages, message_type="Menu")
                        else:
                            simple_menu = "  --Weather Commands--\n" \
                                "2day - 2 day forecast\n" \
                                "4day - 4 day forecast\n" \
                                "temp - 24h temperature\n" \
                                "rain - 24h precipitation"
                            if self.settings.get('ENABLE_CUSTOM_LOOKUP', False):
                                simple_menu += "\nloc lat/lon - custom location lookup"
                            messages = self.split_message(simple_menu, message_type="Menu")
                            send_message_sequence(messages, message_type="Menu")
                    elif "loc" in message:
                        self.transmission_count += 1
                        time.sleep(first_message_delay)
                        custom_lookup_result = self.get_custom_lookup(message)
                        messages = self.split_message(str(custom_lookup_result), message_type="Custom")
                        send_message_sequence(messages, message_type="Custom")
                    elif "temp" in message:
                        self.transmission_count += 1
                        time.sleep(first_message_delay)
                        messages = self.split_message(self.get_temperature_24hour(), message_type="Temp")
                        send_message_sequence(messages, message_type="Temp")
                    elif "2day" in message:
                        self.transmission_count += 1
                        time.sleep(first_message_delay)
                        messages = self.split_message(self.get_forecast_2day(), message_type="2day")
                        send_message_sequence(messages, message_type="2day")
                    elif "hourly" in message:
                        if self.settings.get('ENABLE_HOURLY_WEATHER', True):
                            self.transmission_count += 1
                            weather_data = self.get_emoji_weather()
                            messages = self.split_message(weather_data, message_type="Hourly")
                            send_message_sequence(messages, message_type="Hourly")
                        else:
                            time.sleep(first_message_delay)
                            messages = self.split_message("Hourly weather module is disabled.", message_type="Hourly")
                            send_message_sequence(messages, message_type="Hourly")
                    elif "rain" in message:
                        self.transmission_count += 1
                        time.sleep(first_message_delay)
                        messages = self.split_message(self.get_rain_chance(), message_type="Rain")
                        send_message_sequence(messages, message_type="Rain")
                    elif "5day" in message:
                        if self.settings.get('ENABLE_5DAY_FORECAST', True):
                            self.transmission_count += 1
                            weather_messages = self.nws_weather_fetcher_5day.get_daily_weather()
                            messages = self.split_message('\n'.join(weather_messages), message_type="5day")
                            send_message_sequence(messages, message_type="5day")
                        else:
                            time.sleep(first_message_delay)
                            messages = self.split_message("5-day forecast module is disabled.", message_type="5day")
                            send_message_sequence(messages, message_type="5day")
                    elif "4day" in message:
                        self.transmission_count += 1
                        time.sleep(first_message_delay)
                        messages = self.split_message(self.get_forecast_4day(), message_type="4day")
                        send_message_sequence(messages, message_type="4day")
                    elif "wind" in message:
                        self.transmission_count += 1
                        weather_data = self.wind_24hour.get_wind_24hour()
                        if isinstance(weather_data, list):
                            weather_text = '\n'.join(weather_data)
                            messages = self.split_message(weather_text, message_type="Wind")
                            send_message_sequence(messages, message_type="Wind")
                        else:
                            time.sleep(first_message_delay)
                            messages = self.split_message(weather_data, message_type="Wind")
                            send_message_sequence(messages, message_type="Wind")
                    elif "advertise" in message:
                        self.transmission_count += 1
                        messages = self.split_message(
                            "Hello all! I am a weather bot that does weather alerts and forecasts. "
                            "You can DM me \"?\" for a list of my forecast commands.\n\n"
                            "For more information, check me out on Github. https://github.com/oasis6212/Meshbot_weather",
                            message_type="Advertise"
                        )
                        send_message_sequence(messages, message_type="Advertise")
                    elif "7day" in message:
                        if self.settings.get('ENABLE_7DAY_FORECAST', True):
                            self.transmission_count += 1
                            weather_data = self.forecast_7day.get_weekly_emoji_weather()
                            messages = self.split_message(weather_data, message_type="7day")
                            send_message_sequence(messages, message_type="7day")
                        else:
                            time.sleep(first_message_delay)
                            messages = self.split_message("7-day forecast module is disabled.", message_type="7day")
                            send_message_sequence(messages, message_type="7day")
                    elif "alert-status" in message:
                        self.transmission_count += 1
                        messages = self.split_message(self.get_weather_alert_status(), message_type="AlertStatus")
                        send_message_sequence(messages, message_type="AlertStatus")
                    elif "alert" in message:
                        self.transmission_count += 1
                        if self.alerts:
                            if not self.alerts.broadcast_full_alert(sender_id):
                                time.sleep(first_message_delay)
                                if not self.settings.get('ENABLE_FULL_ALERT_COMMAND', True):
                                    messages = self.split_message(
                                        "The full-alert command is disabled in settings.", message_type="Alert"
                                    )
                                    send_message_sequence(messages, message_type="Alert")
                                else:
                                    messages = self.split_message(
                                        "No active alerts at this time.", message_type="Alert"
                                    )
                                    send_message_sequence(messages, message_type="Alert")
                    elif "reddit" in message:
                        self.transmission_count += 1
                        time.sleep(first_message_delay)
                        # Parse subreddit from message
                        parts = message.split()
                        subreddit = parts[1] if len(parts) > 1 else "python"
                        reddit_fetcher = RedditRSSFetcher(subreddit)
                        reddit_titles = reddit_fetcher.get_post_titles()
                        reddit_message = f"Subreddit: r/{subreddit}\n" + "\n".join(reddit_titles)
                        self.logger.info(f"Reddit message: {reddit_message}")
                        messages = self.split_message(reddit_message, message_type="Reddit")
                        send_message_sequence(messages, message_type="Reddit")
        except KeyError as e:
            node_name = self.interface.getMyNodeInfo().get('user', {}).get('longName', 'Unknown')
            self.logger.error(f'Attached node "{node_name}" was unable to decode incoming message, possible key mismatch in its node-database.')
            return
        except Exception as e:
            self.logger.error(f"Unexpected error in message_listener: {e}")
            return

    def signal_handler(self, sig, frame):
        """Perform a graceful shutdown when CTRL+C is pressed"""
        self.logger.info("\nInitiating shutdown...")
        try:
            if self.interface is not None:
                try:
                    if self.settings.get('SHUTDOWN_NODE_ON_EXIT', False):
                        self.logger.info("Sending shutdown command to node...")
                        self.interface.localNode.shutdown()
                        self.logger.info("Waiting for node to complete shutdown...")
                        time.sleep(17)  # Time delay for node to finish shutting down
                    else:
                        self.logger.info("Skipping node shutdown; closing interface only...")
                        time.sleep(2)  # Short delay for cleanup
                except Exception as e:
                    self.logger.error(f"Error during shutdown cleanup: {e}")
                self.logger.info("Closing Meshtastic interface...")
                self.interface.close()
            self.logger.info("Shutdown complete")
        except Exception as e:
            self.logger.error(f"Error sending shutdown command: {e}")
        sys.exit(0)

    def run(self):
        import argparse
        import signal
        self.logger.info("Starting program.")
        self.reset_transmission_count()
        if self.dutycycle:
            self.reset_cooldown()
        parser = argparse.ArgumentParser(description="Meshbot_Weather a bot for Meshtastic devices")
        parser.add_argument("--port", type=str, help="Specify the serial port to probe")
        parser.add_argument("--host", type=str, help="Specify meshtastic host (IP address) if using API")
        args = parser.parse_args()
        use_tcp = False
        ip_host = None
        if args.port:
            serial_ports = [args.port]
            self.logger.info(f"Serial port {serial_ports}\n")
        elif args.host:
            ip_host = args.host
            use_tcp = True
            print(ip_host)
            self.logger.info(f"Meshtastic API host {ip_host}\n")
        else:
            serial_ports = self.find_serial_ports()
            if serial_ports:
                self.logger.info("Available serial ports:")
                for port in serial_ports:
                    self.logger.info(port)
                self.logger.info(
                    "Im not smart enough to work out the correct port, please use the --port argument with a relevent meshtastic port"
                )
            else:
                self.logger.info("No serial ports found.")
            exit(0)
        self.logger.info(f"Press CTRL-C to terminate the program")
        def connect_interface():
            while True:
                try:
                    if use_tcp:
                        self.interface = meshtastic.tcp_interface.TCPInterface(hostname=ip_host, noProto=False)
                    else:
                        self.interface = meshtastic.serial_interface.SerialInterface(serial_ports[0])
                    self.logger.info("Connected to Meshtastic node.")
                    return
                except Exception as e:
                    self.logger.error(f"Failed to connect to Meshtastic node: {e}. Retrying in 10 seconds...")
                    time.sleep(10)
        connect_interface()
        self.mynode = self.get_my_node_id()
        self.logger.info(f"Automatically detected MYNODE ID: {self.mynode}")
        if self.dm_mode and not self.mynode:
            self.logger.error("DM_MODE is enabled but failed to get MYNODE ID. Please check connection to device.")
            exit(1)
        if self.settings.get('ENABLE_AUTO_REBOOT', True):
            reboot_thread = threading.Thread(
                target=self.schedule_daily_reboot,
                args=(self.interface,),
                daemon=True
            )
            reboot_thread.start()
            self.logger.info("Daily reboot scheduler started")
        try:
            my_info = self.interface.getMyNodeInfo()
            self.logger.info("Connected to Meshtastic Node:")
            self.logger.info(f"Node Name: {my_info.get('user', {}).get('longName', 'Unknown')}")
        except Exception as e:
            self.logger.error(f"Failed to get node info: {e}")
        message_delay = self.settings.get('MESSAGE_DELAY', 10)
        self.alerts = WeatherAlerts(
            self.settings.get("ALERT_LAT"),
            self.settings.get("ALERT_LON"),
            self.interface,
            self.settings.get("USER_AGENT_APP"),
            self.settings.get("USER_AGENT_EMAIL"),
            self.settings.get("ALERT_CHECK_INTERVAL", 300),
            message_delay=message_delay,
            settings=self.settings
        )
        self.alerts.start_monitoring()
        pub.subscribe(self.message_listener, "meshtastic.receive")
        while True:
            try:
                time.sleep(1)
            except Exception as e:
                self.logger.error(f"Main loop error: {e}")
                if use_tcp:
                    self.logger.info("Attempting to reconnect to Meshtastic TCP node...")
                    connect_interface()
                    self.mynode = self.get_my_node_id()
                    self.alerts.interface = self.interface
                    pub.subscribe(self.message_listener, "meshtastic.receive")
                    self.logger.info("Reconnected and resubscribed to Meshtastic events.")

if __name__ == "__main__":
    with open("settings.yaml", "r") as file:
        settings = yaml.safe_load(file)
    meshbot = MeshBot(settings)
    meshbot.run()
