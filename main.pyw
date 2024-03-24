import configparser
import logging
import math
import sys

from tendo import singleton

import time
import socket
from math import radians, sin, cos, sqrt, atan2
from pyproj import CRS, Transformer
import requests
from requests.exceptions import HTTPError, ConnectionError, Timeout, RequestException


def convert_to_osgb36(latitude, longitude):
    try:
        wgs84 = CRS.from_epsg(4326)
        osgb36 = CRS.from_epsg(27700)  # OSGB36 projection

        transformer = Transformer.from_crs(wgs84, osgb36, always_xy=True)
        easting, northing = transformer.transform(longitude, latitude)

        return easting, northing
    except Exception as e:
        print(f"Error converting coordinates: {e}")
        return None


def filter_nmea_sentence(sentence):
    # Split the block into lines
    nmea_lines = sentence.strip().split('\n')

    # Find the first GPGGA sentence, or return an empty string if not found
    return next((line for line in nmea_lines if line.startswith("$GPGGA")), "")


# Function to handle errors in NMEA sentence parsing
def parse_nmea_sentence(sentence):
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


# Function to handle errors in Haversine distance calculation
def haversine_distance(coord1, coord2):
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
    # Calculate the distance using the Haversine formula
    distance = haversine_distance(previous_position, current_position)

    return distance > threshold_distance


# Function to make API request with rate limiting
# Function to make API request with retry on timeout
def make_api_request_with_retry(api_url, payload, max_retries=1, timeout_seconds=10):
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


VALID_FOR_DURATION = "360"

# Logging configuration
logging.basicConfig(level=logging.INFO)


def read_config():
    try:
        config = configparser.ConfigParser()
        config.read("config.ini")
        return config
    except Exception as e:
        logging.error(f"Error reading configuration: {e}")
        sys.exit(-1)


def main():
    try:
        me = singleton.SingleInstance()
    except Exception:
        logging.warning("Already running")
        sys.exit(-1)

    config = read_config()

    radio_id = config.get('General', 'radioID')
    token = config.get("General", "token")

    host = config.get("Connection", "host_ip")
    port = int(config.get("Connection", "port"))

    api_url = config.get("Connection", "api_url")

    moving_time_limit = int(config.get("Variables", "moving_time_limit"))
    stationary_time_limit = int(config.get("Variables", "stationary_time_limit"))
    distance_limit = int(config.get("Variables", "distance_limit"))

    previous_position = (0.0, 0.0)
    last_request_time = time.time()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((host, port))

        logging.info(f"Listening for GPS data on {host}:{port}...")

        while True:
            data = s.recv(1024).decode('utf-8')

            if not data:
                break

            parsed_data = parse_nmea_sentence(filter_nmea_sentence(data))

            if parsed_data is not None:
                latitude, longitude, accuracy = parsed_data

                if accuracy <= 100:
                    easting, northing = convert_to_osgb36(latitude, longitude)
                    easting = math.floor(easting)
                    northing = math.floor(northing)

                    has_moved_flag = has_moved(previous_position, (latitude, longitude), distance_limit)

                    current_time = time.time()
                    time_since_last_request = current_time - last_request_time

                    logging.info(f"time since last send: {math.floor(time_since_last_request)}")
                    logging.info(f"has moved? {has_moved_flag}")

                    if has_moved_flag:
                        if time_since_last_request >= moving_time_limit:
                            payload = {'x': easting, 'y': northing, 'radioId': radio_id, 'validFor': VALID_FOR_DURATION,
                                       'token': token}
                            logging.info(f"Making API request with payload: {payload}")
                            result = make_api_request_with_retry(api_url, payload)
                            if result:
                                last_request_time = current_time
                                previous_position = (latitude, longitude)
                    elif time_since_last_request >= stationary_time_limit:
                        payload = {'x': easting, 'y': northing, 'radioId': radio_id, 'validFor': VALID_FOR_DURATION,
                                   'token': token}
                        logging.info(f"Making API request (stationary) with payload: {payload}")
                        result = make_api_request_with_retry(api_url, payload)
                        if result:
                            last_request_time = current_time
                            previous_position = (latitude, longitude)


if __name__ == "__main__":
    main()
