import configparser
import logging
import math
import sys

from tendo import singleton  # Ensures single instance execution

import time
import socket
from math import radians, sin, cos, sqrt, atan2
from pyproj import CRS, Transformer  # For coordinate system transformation
import requests
from requests.exceptions import HTTPError, ConnectionError, Timeout, RequestException


def convert_to_osgb36(latitude, longitude):
    """Converts latitude and longitude coordinates to OSGB36 projection."""
    try:
        # Define coordinate reference systems
        wgs84 = CRS.from_epsg(4326)
        osgb36 = CRS.from_epsg(27700)  # OSGB36 projection

        # Initialize coordinate transformer
        transformer = Transformer.from_crs(wgs84, osgb36, always_xy=True)
        easting, northing = transformer.transform(longitude, latitude)

        return easting, northing
    except Exception as e:
        print(f"Error converting coordinates: {e}")
        return None


def filter_nmea_sentence(sentence):
    """Filters and extracts GPGGA NMEA sentence from the data."""
    # Split the block into lines
    nmea_lines = sentence.strip().split('\n')

    # Find the first GPGGA sentence, or return an empty string if not found
    return next((line for line in nmea_lines if line.startswith("$GPGGA")), "")


def parse_nmea_sentence(sentence):
    """Parses GPGGA NMEA sentence to extract latitude, longitude, and accuracy."""
    try:
        if not sentence:
            return None

        if sentence.startswith("$GPGGA"):
            data = sentence.split(',')

            # Extract latitude
            latitude = float(data[2][:2]) + float(data[2][2:]) / 60
            if data[3] == 'S':
                latitude = -latitude

            # Extract longitude
            longitude = float(data[4][:3]) + float(data[4][3:]) / 60
            if data[5] == 'W':
                longitude = -longitude

            # Extract accuracy
            accuracy = float(data[8])

            return latitude, longitude, accuracy
        else:
            print("Unsupported NMEA sentence:", sentence)
            return None

    except (IndexError, ValueError) as e:
        print(f"Error parsing GPGGA sentence: {e}")
        return None


def haversine_distance(coord1, coord2):
    """Calculates the Haversine distance between two coordinates."""
    try:
        lat1, lon1 = map(radians, coord1)
        lat2, lon2 = map(radians, coord2)

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))

        earth_radius_meters = 6371000
        distance = earth_radius_meters * c

        return distance

    except Exception as e:
        print(f"Error calculating Haversine distance: {e}")
        return None


def has_moved(previous_position, current_position, threshold_distance):
    """Determines if the device has moved beyond a certain threshold distance."""
    # Calculate the distance using the Haversine formula
    distance = haversine_distance(previous_position, current_position)

    return distance > threshold_distance


def make_api_request_with_retry(api_url, payload, max_retries=1, timeout_seconds=10):
    """Makes an API request with retry on timeout."""
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(api_url, data=payload, timeout=timeout_seconds)
            response.raise_for_status()
            print(f"API request successful: {response.text}")
            return True  # Request succeeded, exit the loop
        except HTTPError as errh:
            print(f"HTTP Error: {errh}")
            return True
        except ConnectionError as errc:
            print(f"Error Connecting: {errc}")
        except Timeout as errt:
            print(f"Timeout Error: {errt}")
        except RequestException as err:
            print(f"Request Error: {err}")
            return True

        if attempt < max_retries:
            print(f"Retrying API request (attempt {attempt}/{max_retries})...")
            time.sleep(2)  # Add a short delay before retrying

    print(f"Maximum number of retries reached. API request failed.")
    return False  # Request failed after max retries


# Constants
VALID_FOR_DURATION = "360"

# Logging configuration
logging.basicConfig(level=logging.INFO)


def read_config():
    """Reads configuration from config.ini file."""
    try:
        config = configparser.ConfigParser()
        config.read("config.ini")
        return config
    except Exception as e:
        logging.error(f"Error reading configuration: {e}")
        sys.exit(-1)


def main():
    """Main function."""
    try:
        me = singleton.SingleInstance()  # Ensures single instance execution
    except Exception:
        logging.warning("Already running")
        sys.exit(-1)

    # Read configuration
    config = read_config()

    # Retrieve configuration values
    radio_id = config.get('General', 'radioID')
    token = config.get("General", "token")

    host = config.get("Connection", "host_ip")
    port = int(config.get("Connection", "port"))
    api_url = config.get("Connection", "api_url")

    moving_time_limit = int(config.get("Variables", "moving_time_limit"))
    stationary_time_limit = int(config.get("Variables", "stationary_time_limit"))
    distance_limit = int(config.get("Variables", "distance_limit"))

    previous_position = (0.0, 0.0)  # Initialize previous position
    last_request_time = time.time()  # Initialize last request time

    # Create a UDP socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((host, port))  # Bind to the specified host and port

        logging.info(f"Listening for GPS data on {host}:{port}...")

        while True:
            data = s.recv(1024).decode('utf-8')  # Receive data from the socket

            if not data:
                break

            parsed_data = parse_nmea_sentence(filter_nmea_sentence(data))  # Parse NMEA sentence

            if parsed_data is not None:
                latitude, longitude, accuracy = parsed_data

                if accuracy <= 100:  # Ensure accuracy is acceptable
                    # Convert coordinates to OSGB36 projection
                    easting, northing = convert_to_osgb36(latitude, longitude)
                    easting = math.floor(easting)
                    northing = math.floor(northing)

                    # Check if the device has moved
                    has_moved_flag = has_moved(previous_position, (latitude, longitude), distance_limit)

                    current_time = time.time()
                    time_since_last_request = current_time - last_request_time


                    # Log time since last request and movement status
                    logging.info(f"time since last send: {math.floor(time_since_last_request)}")
                    logging.info(f"has moved? {has_moved_flag}")

                    if has_moved_flag:
                        if time_since_last_request >= moving_time_limit:
                            # Construct payload for API request
                            payload = {'x': easting, 'y': northing, 'radioId': radio_id, 'validFor': VALID_FOR_DURATION,
                                       'token': token}
                            logging.info(f"Making API request with payload: {payload}")

                            # Make API request with retry
                            result = make_api_request_with_retry(api_url, payload)

                            if result:
                                # Update last request time and previous position
                                last_request_time = current_time
                                previous_position = (latitude, longitude)
                    elif time_since_last_request >= stationary_time_limit:
                        # Construct payload for API request (stationary)
                        payload = {'x': easting, 'y': northing, 'radioId': radio_id, 'validFor': VALID_FOR_DURATION,
                                   'token': token}
                        logging.info(f"Making API request (stationary) with payload: {payload}")

                        # Make API request with retry
                        result = make_api_request_with_retry(api_url, payload)

                        if result:
                            # Update last request time and previous position
                            last_request_time = current_time
                            previous_position = (latitude, longitude)


if __name__ == "__main__":
    main()
